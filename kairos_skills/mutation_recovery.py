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


def reconcile_rollback(repository, files, record):
    expected = expected_entry(repository, record)
    installed = files.inspect_installed(record.name)
    retired = files.inspect_private(record.operation_id, retired=True)
    if retired is None and installed is not None and installed.identity == expected.identity:
        # Retirada não aconteceu, ou a mesma versão editada já foi devolvida.
        return repository.finish(record.operation_id, SkillMutationState.ABORTED)
    if installed is None and retired == expected:
        files.sync_parents()
        return repository.finish(record.operation_id, SkillMutationState.COMMITTED)
    return mark_conflict(repository, record)


def reconcile_pending(repository, files, *, name: str):
    from kairos_skills.mutation_io import SkillMutationFiles
    from kairos_skills.removal_contract import SkillTreeAction
    from kairos_skills.removal_io import SkillRemovalFiles
    from kairos_skills.removal_recovery import reconcile_tree_operation

    results = []
    for record in repository.pending(name):
        try:
            if record.action is SkillMutationAction.CREATE:
                handler, adapter = reconcile_create, SkillMutationFiles
            elif record.action is SkillMutationAction.ROLLBACK:
                handler, adapter = reconcile_rollback, SkillMutationFiles
            elif record.action in (SkillTreeAction.REMOVE, SkillTreeAction.RESTORE):
                handler, adapter = reconcile_tree_operation, SkillRemovalFiles
            else:
                raise SkillMutationError("corrupt", "Ação de skills desconhecida.")
            if isinstance(files, adapter):
                results.append(handler(repository, files, record))
            else:
                with adapter(files.home) as operation_files:
                    results.append(handler(repository, operation_files, record))
        except SkillMutationError as error:
            if error.kind in ("conflict", "input"):
                repository.finish(record.operation_id, SkillMutationState.CONFLICT)
            raise SkillMutationError(
                error.kind, str(error), operation_id=record.operation_id
            ) from None
    return tuple(results)
