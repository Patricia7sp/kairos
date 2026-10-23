"""Protocolo PREPARED → movimentos comprovados → COMMITTED na sessão viva."""

from __future__ import annotations

import logging
import time
import uuid

from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
    require,
    validate_name,
)
from kairos_skills.org_policy_contract import OrgPolicyAction
from kairos_skills.org_policy_guard import repository_guard
from kairos_skills.publication_contract import (
    PublicationDraft,
    PublicationResult,
    PublicationWriter,
)
from kairos_skills.publication_files import observe_publication_trees
from kairos_skills.publication_io import delete_publication
from kairos_skills.publication_session import PublicationSession
from kairos_state.repositories.skill_mutations import SkillMutationRepository

_LOGGER = logging.getLogger(__name__)


def publication_result(record, *, error_kind=None):
    return PublicationResult(
        record.operation_id,
        record.name,
        record.state,
        record.state is SkillMutationState.COMMITTED
        and record.manifest_complete
        and record.cleanup_complete
        and error_kind is None,
        error_kind,
        record.manifest_complete,
        record.cleanup_complete,
    )


def publication_failure(session, operation_id, error, *, action, phase, draft=None):
    kind = error.kind if isinstance(error, SkillMutationError) else "io"
    _LOGGER.warning(
        "skill_publication_incomplete",
        extra={
            "action": action,
            "writer": session.context.writer.value,
            "phase": phase,
            "kind": kind,
            "operation_id": operation_id,
            "reason": "publication_attestation_failed",
        },
    )
    try:
        record = session.publications.get(operation_id)
    except SkillMutationError:
        raise SkillMutationError(
            kind, "Não foi possível atestar o journal; retome o ID.", operation_id=operation_id
        ) from None
    if record is None or (draft is not None and record.draft != draft):
        raise SkillMutationError(
            kind,
            "Publicação recusada antes de PREPARED; resíduos preservados.",
            operation_id=operation_id,
        ) from None
    return publication_result(record, error_kind=kind)


class PublicationCoordinator:
    """Guard do próprio ID, sem dispensar pendências alheias nem proteção B1."""

    def __init__(self, session):
        require(type(session) is PublicationSession, "Sessão de publicação obrigatória.")
        self.session = session
        self.common = SkillMutationRepository(session.connection, home_id=session.context.home_id)
        self.policy = repository_guard(session.policy, self.common)

    def projection_guard(self, draft):
        from kairos_skills.sync import check_sync_policy

        self.session.revalidate()
        self.compatible_context(draft)
        self.session.files.check_operation(draft.operation_id)
        self.policy.check_publication(draft)
        current = self.session.publications.get(draft.operation_id)
        if current is not None and current.projection_delta != draft.manifest_delta:
            require(current.projection_delta is not None and current.projection_delta.after is None)
            require(not self.session.context.contains(draft.name), "Skill voltou ao pacote.")
        else:
            check_sync_policy(self.session, draft)
        others = [
            r for r in self.common.pending(draft.name) if r.operation_id != draft.operation_id
        ]
        if others:
            raise SkillMutationError(
                "conflict",
                "Outra operação pendente bloqueia o nome.",
                operation_id=others[0].operation_id,
            )

    def guard(self, draft):
        from kairos_skills.sync import check_sync_intent

        self.projection_guard(draft)
        check_sync_intent(self.session, draft)

    def trees(self, draft, *, installed, staged, previous):
        root = self.session.home
        area = root / ".skill-publications" / draft.operation_id
        return [
            (root / "skills" / draft.name, installed),
            (area / "staged", staged),
            (area / "previous", previous),
        ]

    def persist(self, draft, operation, *, installed, staged, previous):
        with observe_publication_trees(
            self.trees(draft, installed=installed, staged=staged, previous=previous)
        ) as check:

            def guard():
                self.guard(draft)
                check()

            return operation(guard)

    def compatible_context(self, draft):
        context = self.session.context
        require(
            (context.home_id, context.writer, context.source_key, context.actor)
            == (draft.home_id, draft.writer, draft.source_key, draft.actor),
            "Fonte incompatível com o ID.",
        )
        if draft.writer is PublicationWriter.QUARANTINE_PROMOTE:
            require(
                context.replacement == draft.previous_tree, "Intenção de substituição divergente."
            )
            receipt = self.session.publications.receipt(draft.source_ref.split("/")[0])
            require(
                receipt is not None and receipt.quarantine_identity == context.root_identity,
                "ID pertence a outra raiz de quarentena.",
            )

    def source(self, draft):
        self.compatible_context(draft)
        context = self.session.context
        acquisition_id = (
            None
            if draft.writer is PublicationWriter.BUNDLED_SYNC
            else draft.source_ref.split("/")[0]
        )
        path, proof = context.resolve(
            draft.name, acquisition_id=acquisition_id, repository=self.session.publications
        )
        if proof != draft.source_proof:
            raise SkillMutationError("conflict", "Fonte do ID alterada; versões preservadas.")
        return path

    def finish_phases(self, record):
        from kairos_skills.sync import observe_manifest, reconcile_manifest

        draft = record.draft
        self.guard(draft)
        if record.projection_delta != draft.manifest_delta:
            if not record.manifest_complete:
                reconcile_manifest(
                    self.session.home, record, guard=lambda: self.projection_guard(draft)
                )
                _manifest, self.session._manifest_observation = observe_manifest(self.session.home)
                record = self.session.publications.confirm_manifest(
                    draft.operation_id, guard=lambda: self.guard(draft)
                )
            return publication_result(record)
        installed = self.session.files.capture_installed(draft.name)
        previous = self.session.files.capture_previous(draft.operation_id)
        staged = self.session.files.capture_staged(draft.operation_id)
        if (installed, previous, staged) != (draft.staged_tree, draft.previous_tree, None):
            raise SkillMutationError(
                "conflict", "Instalação ou snapshot divergente; versões preservadas."
            )
        if not record.manifest_complete:
            with observe_publication_trees(
                self.trees(draft, installed=installed, staged=None, previous=previous)
            ) as check:

                def projection_guard():
                    self.projection_guard(draft)
                    check()
                    if record.projection_delta != record.draft.manifest_delta:
                        require(
                            not self.session.context.contains(draft.name), "Skill voltou ao pacote."
                        )

                reconcile_manifest(self.session.home, record, guard=projection_guard)
                _manifest, self.session._manifest_observation = observe_manifest(self.session.home)

                def confirmation_guard():
                    projection_guard()
                    self.session._manifest_observation.check()

                record = self.session.publications.confirm_manifest(
                    draft.operation_id, guard=confirmation_guard
                )
        if not record.cleanup_complete:
            source = self.source(draft)
            self.guard(draft)
            with observe_publication_trees(
                self.trees(draft, installed=installed, staged=None, previous=previous)
            ) as check:

                def guard():
                    self.guard(draft)
                    check()

                self.session.context._release_source_for_cleanup(source, draft.source_proof)
                delete_publication(source, draft.source_proof, guard=guard)
                record = self.session.publications.confirm_cleanup(draft.operation_id, guard=guard)
        result = publication_result(record)
        if result.complete and draft.writer is PublicationWriter.BUNDLED_SYNC:
            self.session.context._release_completed_bundle(draft.name)
        return result


class SkillPublicationService:
    def __init__(self, session: PublicationSession):
        require(type(session) is PublicationSession, "Sessão de publicação obrigatória.")
        self.session = session

    def publish(self, name, *, acquisition_id=None, manifest_delta=None):
        session = self.session
        operation_id = uuid.uuid4().hex
        draft = None
        phase = "validation"
        try:
            session.revalidate()
            validate_name(name)
            coordinator = PublicationCoordinator(session)
            pending = coordinator.common.pending(name)
            if pending:
                raise SkillMutationError(
                    "conflict",
                    "Nome possui operação pendente.",
                    operation_id=pending[0].operation_id,
                )
            action = (
                OrgPolicyAction.SYNC
                if session.context.writer is PublicationWriter.BUNDLED_SYNC
                else OrgPolicyAction.PROMOTE
            )
            receipt = coordinator.policy.check(name, action)
            require(session.publications.get(operation_id) is None, "ID ocupado.")
            source, proof = session.context.resolve(
                name, acquisition_id=acquisition_id, repository=session.publications
            )
            previous = session.files.capture_installed(name)
            current = coordinator.common.current_installation(name)
            if session.context.writer is PublicationWriter.QUARANTINE_PROMOTE:
                if session.context.replacement != previous:
                    raise SkillMutationError(
                        "conflict", "Substituição exige intenção sobre a árvore exata."
                    )
            else:
                from kairos_skills.sync import prepare_sync_publication

                prepare_sync_publication(
                    session,
                    name,
                    previous=previous,
                    current=current,
                    delta=manifest_delta,
                    source_proof=proof,
                )
            phase = "staging"
            staged = session.files.stage(operation_id, source, proof, guard=session.revalidate)
            draft = PublicationDraft(
                operation_id,
                session.context.home_id,
                name,
                name if acquisition_id is None else f"{acquisition_id}/{name}",
                session.context.source_key,
                session.context.writer,
                session.context.actor,
                proof,
                staged,
                current,
                previous,
                receipt,
                manifest_delta,
                time.time(),
            )
            phase = "prepare"
            coordinator.persist(
                draft,
                lambda guard: session.publications.prepare(draft, guard=guard),
                installed=previous,
                staged=staged,
                previous=None,
            )
            phase = "installation"

            def guard():
                coordinator.guard(draft)

            if previous is not None:
                session.files.retire(name, operation_id, previous, guard=guard)
            session.files.publish(operation_id, name, staged, guard=guard)
            session.files.sync_parents(guard=guard)
            phase = "commit"
            record = coordinator.persist(
                draft,
                lambda guard: session.publications.finish(
                    operation_id, SkillMutationState.COMMITTED, guard=guard
                ),
                installed=staged,
                staged=None,
                previous=previous,
            )
            phase = "confirmations"
            return coordinator.finish_phases(record)
        except (SkillMutationError, OSError) as error:
            if draft is None:
                kind = error.kind if isinstance(error, SkillMutationError) else "io"
                _LOGGER.warning(
                    "skill_publication_refused",
                    extra={
                        "action": "publish",
                        "writer": session.context.writer.value,
                        "phase": phase,
                        "kind": kind,
                        "operation_id": getattr(error, "operation_id", None) or operation_id,
                        "reason": "publication_validation_failed",
                    },
                )
                if isinstance(error, SkillMutationError) and error.operation_id is not None:
                    raise
                raise SkillMutationError(
                    kind, "Publicação recusada; versões preservadas.", operation_id=operation_id
                ) from None
            return publication_failure(
                session, operation_id, error, action="publish", phase=phase, draft=draft
            )

    def recover(self, operation_id):
        from kairos_skills.publication_recovery import reconcile_publication

        record = self.session.publications.get(operation_id)
        require(record is not None, "ID de publicação desconhecido; resíduos preservados.")
        return reconcile_publication(self.session, record)
