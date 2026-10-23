"""Recuperação por ID canônico e layout observado, sob o lock da sessão."""

from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState, require
from kairos_skills.publication_contract import SkillPublicationRecord
from kairos_skills.publications import (
    PublicationCoordinator,
    publication_failure,
    publication_result,
)


def reconcile_publication(session, record: SkillPublicationRecord):
    require(type(record) is SkillPublicationRecord)
    current = session.publications.get(record.operation_id)
    require(current is not None and current.draft == record.draft, "ID canônico obrigatório.")
    record = current
    draft = record.draft
    try:
        coordinator = PublicationCoordinator(session)
        coordinator.guard(draft)
        if record.state is SkillMutationState.ABORTED:
            return publication_result(record)
        if record.state is SkillMutationState.COMMITTED:
            return coordinator.finish_phases(record)
        coordinator.source(draft)
        installed = session.files.capture_installed(draft.name)
        staged = session.files.capture_staged(draft.operation_id)
        previous = session.files.capture_previous(draft.operation_id)

        def guard():
            coordinator.guard(draft)

        if installed == draft.staged_tree and staged is None and previous == draft.previous_tree:
            session.files.sync_parents(guard=guard)
            record = coordinator.persist(
                draft,
                lambda check: session.publications.finish(
                    draft.operation_id, SkillMutationState.COMMITTED, guard=check
                ),
                installed=installed,
                staged=None,
                previous=previous,
            )
            return coordinator.finish_phases(record)
        if staged == draft.staged_tree and (
            (installed == draft.previous_tree and previous is None)
            or (installed is None and previous == draft.previous_tree)
        ):
            if previous is not None:
                session.files.return_previous(draft.operation_id, draft.name, previous, guard=guard)
            session.files.discard_staged(draft.operation_id, staged, guard=guard)
            session.files.sync_parents(guard=guard)
            record = coordinator.persist(
                draft,
                lambda check: session.publications.finish(
                    draft.operation_id, SkillMutationState.ABORTED, guard=check
                ),
                installed=draft.previous_tree,
                staged=None,
                previous=None,
            )
            return publication_result(record)
        record = coordinator.persist(
            draft,
            lambda check: session.publications.finish(
                draft.operation_id, SkillMutationState.CONFLICT, guard=check
            ),
            installed=installed,
            staged=staged,
            previous=previous,
        )
        return publication_result(record, error_kind="conflict")
    except (SkillMutationError, OSError) as error:
        return publication_failure(
            session,
            draft.operation_id,
            error,
            action="recover",
            phase="reconciliation",
            draft=draft,
        )
