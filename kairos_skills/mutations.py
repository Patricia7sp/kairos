"""Autoria manual coordenada entre journal durável e filesystem."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_skills.adoption_contract import (
    AdoptionAuthority,
    SkillAdoptionAction,
    SkillAdoptionRecord,
)
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationState,
    require,
    validate_actor,
    validate_id,
    validate_name,
)
from kairos_skills.mutation_io import SkillMutationFiles, read_skill_creation
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import expected_entry, mark_conflict, reconcile_pending
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_skills.removal_io import SkillRemovalFiles
from kairos_skills.removal_origin import prove_removal_origin, resolve_removal_origin
from kairos_skills.removal_recovery import tree_matches
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def operation_error(error, operation_id: str) -> SkillMutationError:
    kind = error.kind if isinstance(error, SkillMutationError) else "io"
    message = (
        str(error)
        if isinstance(error, SkillMutationError)
        else "Não foi possível concluir a operação; arquivos preservados."
    )
    return SkillMutationError(
        kind, message, operation_id=getattr(error, "operation_id", None) or operation_id
    )


def _linked_adoption(repository, removed):
    authority = removed.draft.adoption
    if authority is None:
        return None
    founder = repository.get(authority.adoption_id)
    if (
        not isinstance(founder, SkillAdoptionRecord)
        or founder.action is not SkillAdoptionAction.ADOPT
        or founder.state is not SkillMutationState.COMMITTED
        or founder.name != removed.name
        or founder.draft.installation_source != removed.draft.installation_source
    ):
        raise SkillMutationError(
            "corrupt", "Linhagem da remoção adotada está incompleta; restauração recusada."
        )
    return founder


def _adoption_rollback_error(repository, record, operation_id):
    if record.action is SkillAdoptionAction.UNADOPT:
        inverse = "adopt"
    else:
        authority = repository.installation_evidence(record.name).adoption
        inverse = (
            "unadopt"
            if authority is not None
            and authority.adoption_id == record.operation_id
            and authority.active
            else "adopt"
        )
    return SkillMutationError(
        "denied",
        f"Adoção não é rollback de arquivo; use {inverse} para alterar a autoridade.",
        operation_id=operation_id,
    )


def _removal_adoption(repository, name, current):
    adoption = None if current is None else current.adoption
    if adoption is None:
        return None, None
    if not adoption.active:
        raise SkillMutationError(
            "denied", "A adoção foi revogada; readoção confirmada é necessária."
        )
    evidence = repository.installation_evidence(name)
    if evidence.adoption != adoption:
        raise SkillMutationError("conflict", "A linhagem de adoção mudou; remoção recusada.")
    founder = repository.get(adoption.adoption_id)
    if (
        not isinstance(founder, SkillAdoptionRecord)
        or founder.action is not SkillAdoptionAction.ADOPT
        or founder.state is not SkillMutationState.COMMITTED
        or founder.name != name
    ):
        raise SkillMutationError("corrupt", "Linhagem de adoção incompleta; remoção recusada.")
    return adoption, founder.draft.installation_source


def _require_restore_authority(repository, removed, previous=None, *, pending_operation_id=None):
    if removed.draft.adoption is None:
        return
    founder = _linked_adoption(repository, removed)
    evidence = repository.installation_evidence(removed.name)
    authority = evidence.adoption
    if previous is None:
        valid = (
            evidence.current is None
            and evidence.current_removal_id == removed.operation_id
            and authority == removed.draft.adoption
        )
    else:
        expected = AdoptionAuthority(
            removed.draft.adoption.adoption_id, previous.operation_id, True
        )
        valid = (
            previous.action is SkillTreeAction.RESTORE
            and previous.reverts == removed.operation_id
            and previous.draft.adoption == expected
            and evidence.current is not None
            and evidence.current.operation_id == previous.operation_id
            and authority == expected
        )
    expected_pending = () if pending_operation_id is None else (pending_operation_id,)
    if evidence.pending_operation_ids != expected_pending:
        raise SkillMutationError(
            "conflict",
            "A adoção vinculada não está vigente para esta restauração; versões preservadas.",
            operation_id=removed.operation_id,
        )
    if (
        authority is not None
        and authority.adoption_id == removed.draft.adoption.adoption_id
        and not authority.active
    ):
        raise SkillMutationError(
            "denied",
            "A adoção vinculada foi revogada; versões preservadas.",
            operation_id=removed.operation_id,
        )
    if not valid or authority is None:
        raise SkillMutationError(
            "conflict",
            "A adoção vinculada não está vigente para esta restauração; versões preservadas.",
            operation_id=removed.operation_id,
        )
    if founder.draft.installation_source != removed.draft.installation_source:
        raise SkillMutationError("corrupt", "Origem da adoção vinculada diverge.")


class SkillMutationService:
    def __init__(self, home: Path, repository: SkillMutationRepository):
        self.home = Path(home).expanduser().absolute()
        require(".." not in self.home.parts, "Home não pode conter escapes.")
        require(
            repository.home_id == catalog_home_id(self.home), "Ledger pertence a outra instalação."
        )
        self.repository = repository

    def history(self, *, name: str | None = None, limit: int = 20):
        return self.repository.history(name=name, limit=limit)

    def ownership(self, name: str) -> Provenance:
        return self.repository.provenance(name) or Provenance.USER

    def _abort_staged(self, files, operation_id, expected):
        # Só há autorização de limpeza para a identidade preparada por esta chamada.
        if files.inspect_private(operation_id) != expected:
            raise SkillMutationError(
                "conflict", "Staging alterado; conteúdo preservado.", operation_id=operation_id
            )
        files.discard_staging(operation_id, expected)
        record = self.repository.get(operation_id)
        if record is not None and record.state in (
            SkillMutationState.PREPARED,
            SkillMutationState.CONFLICT,
        ):
            self.repository.finish(operation_id, SkillMutationState.ABORTED)

    def add(self, source: Path, *, actor: Actor):
        validate_actor(actor)
        creation = read_skill_creation(source)
        operation_id = uuid.uuid4().hex
        try:
            with skill_mutation_lock(self.home), SkillMutationFiles(self.home) as files:
                reconcile_pending(self.repository, files, name=creation.name)
                files.assert_name_available(creation.name)
                entry = files.stage(operation_id, creation)
                attempted = False
                try:
                    draft = SkillMutationDraft(
                        operation_id,
                        self.repository.home_id,
                        SkillMutationAction.CREATE,
                        creation.name,
                        actor,
                        time.time(),
                        entry.identity,
                        creation.sha256,
                        creation.size_bytes,
                    )
                    self.repository.prepare(draft, creation.text)
                    attempted = True
                    files.publish(operation_id, creation.name)
                    if files.inspect_installed(creation.name) != entry:
                        raise SkillMutationError(
                            "conflict", "Publicação divergiu; conteúdo preservado."
                        )
                    return self.repository.finish(operation_id, SkillMutationState.COMMITTED)
                except (SkillMutationError, OSError):
                    if not attempted:
                        self._abort_staged(files, operation_id, entry)
                    raise
        except (SkillMutationError, OSError) as error:
            raise operation_error(error, operation_id) from None

    def rollback(self, create_id: str, *, actor: Actor):
        validate_actor(actor)
        validate_id(create_id)
        operation_id = create_id
        try:
            with skill_mutation_lock(self.home):
                created = self.repository.get(create_id)
                if created is not None and created.action is SkillTreeAction.REMOVE:
                    return self._restore_removed(created, actor=actor)
                if isinstance(created, SkillAdoptionRecord):
                    raise _adoption_rollback_error(self.repository, created, create_id)
                require(
                    created is not None
                    and created.action is SkillMutationAction.CREATE
                    and created.state is SkillMutationState.COMMITTED,
                    "Informe o ID de uma criação concluída neste home.",
                )
                previous = self.repository.latest_rollback(create_id)
                if previous is not None and previous.state is SkillMutationState.COMMITTED:
                    return previous
                with SkillMutationFiles(self.home) as files:
                    reconcile_pending(self.repository, files, name=created.name)
                    previous = self.repository.latest_rollback(create_id)
                    if previous is not None and previous.state is SkillMutationState.COMMITTED:
                        return previous
                    current = self.repository.current_installation(created.name)
                    if current is None or current.operation_id != create_id:
                        raise SkillMutationError(
                            "conflict",
                            "A criação não é a instalação corrente; arquivos preservados.",
                            operation_id=None if current is None else current.operation_id,
                        )
                    expected = expected_entry(self.repository, created)
                    if files.inspect_installed(created.name) != expected:
                        raise SkillMutationError(
                            "conflict",
                            "A criação foi editada, substituída ou removida; arquivos preservados.",
                        )
                    operation_id = uuid.uuid4().hex
                    draft = SkillMutationDraft(
                        operation_id,
                        self.repository.home_id,
                        SkillMutationAction.ROLLBACK,
                        created.name,
                        actor,
                        time.time(),
                        expected.identity,
                        expected.creation.sha256,
                        expected.creation.size_bytes,
                        create_id,
                    )
                    self.repository.prepare(draft, expected.creation.text)
                    files.retire(created.name, operation_id)
                    try:
                        unchanged = files.inspect_private(operation_id, retired=True) == expected
                    except SkillMutationError as error:
                        if error.kind not in ("input", "conflict"):
                            raise
                        unchanged = False
                    if not unchanged:
                        self._restore_changed(files, draft, expected)
                        raise SkillMutationError(
                            "conflict",
                            "Edição concorrente preservada e devolvida; rollback abortado.",
                        )
                    if not files.installed_name_absent(created.name):
                        mark_conflict(self.repository, self.repository.get(operation_id))
                    return self.repository.finish(operation_id, SkillMutationState.COMMITTED)
        except (SkillMutationError, OSError) as error:
            raise operation_error(error, operation_id) from None

    def remove(self, name: str, *, actor: Actor, confirmed: bool = False):
        validate_actor(actor)
        if confirmed is not True:
            raise SkillMutationError("denied", "Remoção exige confirmação explícita --yes.")
        validate_name(name)
        operation_id = uuid.uuid4().hex
        try:
            with skill_mutation_lock(self.home), SkillRemovalFiles(self.home) as files:
                reconcile_pending(self.repository, files, name=name)
                captured = files.capture_installed(name)
                current = self.repository.current_installation(name)
                if captured is None:
                    return self._repeat_removal(files, name, current)
                adoption, source = _removal_adoption(self.repository, name, current)
                provenance = prove_removal_origin(self.home, name, current, captured)
                draft = SkillTreeDraft(
                    operation_id,
                    self.repository.home_id,
                    SkillTreeAction.REMOVE,
                    name,
                    actor,
                    time.time(),
                    provenance,
                    TreeCapture(captured.entries, None),
                    operation_id,
                    None,
                    adoption,
                    source,
                )
                record = self.repository.prepare_remove(draft, captured)
                if files.capture_installed(name) != captured:
                    mark_conflict(self.repository, record)
                files.retire(name, operation_id)
                try:
                    unchanged = tree_matches(
                        files.capture_private(operation_id, retired=True), record, captured
                    )
                except SkillMutationError as error:
                    if error.kind not in ("input", "conflict"):
                        raise
                    unchanged = False
                if not unchanged:
                    try:
                        if files.retired_owned(
                            operation_id, draft.proof
                        ) and files.installed_name_absent(name):
                            files.return_retired(operation_id, name)
                    except SkillMutationError as error:
                        if error.kind not in ("input", "conflict"):
                            raise
                    mark_conflict(self.repository, record)
                if not files.installed_name_absent(name):
                    mark_conflict(self.repository, record)
                files.sync_parents()
                return self.repository.finish(operation_id, SkillMutationState.COMMITTED)
        except (SkillMutationError, OSError) as error:
            raise operation_error(error, operation_id) from None

    def _repeat_removal(self, files, name, current):
        previous = self.repository.current_removal(name)
        if current is None and previous is not None:
            if self.repository.has_later_installation(previous.operation_id):
                raise SkillMutationError(
                    "conflict",
                    "Instalação posterior impede repetir a remoção antiga.",
                    operation_id=previous.operation_id,
                )
            snapshot = self.repository.snapshot(previous.operation_id)
            if tree_matches(
                files.capture_private(previous.operation_id, retired=True), previous, snapshot
            ):
                return previous
            raise SkillMutationError(
                "conflict",
                "Prova privada da remoção diverge; versões preservadas.",
                operation_id=previous.operation_id,
            )
        raise SkillMutationError("conflict", "Skill ausente sem prova de remoção corrente.")

    def _restore_removed(self, removed, *, actor: Actor):
        require(removed.state is SkillMutationState.COMMITTED, "Informe o ID de remoção concluída.")
        operation_id = removed.operation_id
        try:
            previous = self.repository.latest_restore(removed.operation_id)
            if (
                removed.draft.adoption is None
                and previous is not None
                and previous.state is SkillMutationState.COMMITTED
            ):
                return previous
            with SkillRemovalFiles(self.home) as files:
                reconcile_pending(self.repository, files, name=removed.name)
                previous = self.repository.latest_restore(removed.operation_id)
                if previous is not None and previous.state is SkillMutationState.COMMITTED:
                    _require_restore_authority(self.repository, removed, previous)
                    return previous
                _require_restore_authority(self.repository, removed)
                current = self.repository.current_removal(removed.name)
                installed = self.repository.current_installation(removed.name)
                if (
                    installed is not None
                    or current is None
                    or current.operation_id != removed.operation_id
                ):
                    raise SkillMutationError(
                        "conflict",
                        "Remoção não é corrente; versões preservadas.",
                        operation_id=installed.operation_id
                        if installed is not None
                        else (
                            current.operation_id if current is not None else removed.operation_id
                        ),
                    )
                if not files.installed_name_absent(removed.name):
                    raise SkillMutationError("conflict", "Nome ocupado; instalação preservada.")
                if self.repository.has_later_installation(removed.operation_id):
                    raise SkillMutationError(
                        "conflict",
                        "Instalação posterior impede restaurar a remoção antiga.",
                        operation_id=removed.operation_id,
                    )
                snapshot = self.repository.snapshot(removed.operation_id)
                operation_id = uuid.uuid4().hex
                staged = files.stage(operation_id, snapshot)
                adoption = (
                    None
                    if removed.draft.adoption is None
                    else AdoptionAuthority(removed.draft.adoption.adoption_id, operation_id, True)
                )
                draft = SkillTreeDraft(
                    operation_id,
                    self.repository.home_id,
                    SkillTreeAction.RESTORE,
                    removed.name,
                    actor,
                    time.time(),
                    removed.provenance,
                    TreeCapture(staged.entries, None),
                    removed.operation_id,
                    removed.operation_id,
                    adoption,
                    removed.draft.installation_source if adoption is not None else None,
                )
                record = self.repository.prepare_restore(draft)
                if not tree_matches(
                    files.capture_private(operation_id, retired=False), record, snapshot
                ):
                    mark_conflict(self.repository, record)
                files.publish(operation_id, removed.name)
                if not tree_matches(files.capture_installed(removed.name), record, snapshot):
                    mark_conflict(self.repository, record)
                if files.capture_private(operation_id, retired=False) is not None:
                    mark_conflict(self.repository, record)
                if adoption is not None:
                    _require_restore_authority(
                        self.repository,
                        removed,
                        pending_operation_id=operation_id,
                    )
                    restored_capture = files.capture_installed(removed.name)
                    origin = resolve_removal_origin(self.home, removed.name, None, restored_capture)
                    if origin.provenance is Provenance.BUNDLED or (
                        origin.identity_valid is True and origin.provenance is Provenance.SEDIMENT
                    ):
                        mark_conflict(self.repository, record)
                    if files.capture_installed(removed.name) != restored_capture:
                        mark_conflict(self.repository, record)
                files.sync_parents()
                return self.repository.finish(operation_id, SkillMutationState.COMMITTED)
        except (SkillMutationError, OSError) as error:
            raise operation_error(error, operation_id) from None

    def _restore_changed(self, files, draft, expected):
        record = self.repository.get(draft.operation_id)
        if not files.directory_owned(draft.operation_id, expected.identity, retired=True):
            mark_conflict(self.repository, record)
        try:
            files.restore(draft.operation_id, draft.name)
        except SkillMutationError as error:
            if error.kind == "conflict":
                mark_conflict(self.repository, record)
            raise
        if not files.directory_owned(draft.name, expected.identity):
            mark_conflict(self.repository, record)
        self.repository.finish(draft.operation_id, SkillMutationState.ABORTED)
