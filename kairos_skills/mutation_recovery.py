"""Reconciliação somente de IDs conhecidos, sob lock do chamador."""

from kairos_skills.mutation_contract import (
    SkillFilesystemEntry,
    SkillMutationAction,
    SkillMutationError,
    SkillMutationState,
)


def expected_entry(repository, record):
    return SkillFilesystemEntry(record.draft.identity, repository.content(record.operation_id))


def mark_conflict(repository, record):
    repository.finish(record.operation_id, SkillMutationState.CONFLICT)
    raise SkillMutationError(
        "conflict",
        "Operação diverge dos arquivos atuais; versões preservadas. Resolva o conflito antes de repetir.",
        operation_id=record.operation_id,
    )


def reconcile_create(repository, files, record):
    expected = expected_entry(repository, record)
    installed = files.inspect_installed(record.name)
    staged = files.inspect_private(record.operation_id)
    if installed is None and staged is None:
        return repository.finish(record.operation_id, SkillMutationState.ABORTED)
    if installed is None and staged == expected:
        files.discard_staging(record.operation_id, expected)
        return repository.finish(record.operation_id, SkillMutationState.ABORTED)
    if staged is None and installed == expected:
        files.sync_parents()
        return repository.finish(record.operation_id, SkillMutationState.COMMITTED)
    return mark_conflict(repository, record)


def reconcile_pending(repository, files, *, name: str):
    results = []
    for record in repository.pending(name):
        try:
            if record.action is SkillMutationAction.CREATE:
                results.append(reconcile_create(repository, files, record))
            else:
                mark_conflict(repository, record)
        except SkillMutationError as error:
            if error.kind in ("conflict", "input"):
                repository.finish(record.operation_id, SkillMutationState.CONFLICT)
            raise SkillMutationError(
                error.kind, str(error), operation_id=record.operation_id
            ) from None
    return tuple(results)
