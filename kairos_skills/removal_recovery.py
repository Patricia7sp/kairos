"""Reconciliação de árvores somente pelas provas de IDs preparados conhecidos."""

from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_recovery import mark_conflict
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeRecord


def tree_matches(actual, record, snapshot) -> bool:
    return (
        actual is not None
        and actual.entries == record.draft.proof.entries
        and actual.contents == snapshot.contents
    )


def reconcile_tree_operation(repository, files, record: SkillTreeRecord) -> SkillTreeRecord:
    try:
        snapshot = repository.snapshot(record.operation_id)
        installed = files.capture_installed(record.name)
        private = files.capture_private(
            record.operation_id, retired=record.action is SkillTreeAction.REMOVE
        )
        if private is None and tree_matches(installed, record, snapshot):
            state = (
                SkillMutationState.ABORTED
                if record.action is SkillTreeAction.REMOVE
                else SkillMutationState.COMMITTED
            )
        elif installed is None and tree_matches(private, record, snapshot):
            state = (
                SkillMutationState.COMMITTED
                if record.action is SkillTreeAction.REMOVE
                else SkillMutationState.ABORTED
            )
        else:
            return mark_conflict(repository, record)
        files.sync_parents()
        return repository.finish(record.operation_id, state)
    except SkillMutationError as error:
        if error.kind in ("input", "conflict"):
            repository.finish(record.operation_id, SkillMutationState.CONFLICT)
        raise SkillMutationError(error.kind, str(error), operation_id=record.operation_id) from None
