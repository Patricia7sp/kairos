"""Reconciliação de árvores somente pelas provas de IDs preparados conhecidos."""

from kairos_domain.ownership import Provenance
from kairos_skills.adoption_contract import (
    AdoptionAuthority,
    SkillAdoptionAction,
    SkillAdoptionRecord,
)
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_recovery import mark_conflict
from kairos_skills.org_policy_guard import attest_policy_home, repository_guard
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeRecord
from kairos_skills.removal_origin import resolve_removal_origin


def tree_matches(actual, record, snapshot) -> bool:
    return (
        actual is not None
        and actual.entries == record.draft.proof.entries
        and actual.contents == snapshot.contents
    )


def _linked_remove_authority(repository, record):
    authority = record.draft.adoption
    if authority is None:
        return
    evidence = repository.installation_evidence(record.name)
    founder = repository.get(authority.adoption_id)
    root = record.draft.proof.entries[0]
    if (
        evidence.current is None
        or evidence.current.operation_id != authority.installation_id
        or evidence.current.adoption != authority
        or not authority.active
        or evidence.current_removal_id is not None
        or evidence.pending_operation_ids != (record.operation_id,)
        or evidence.current.device != root.device
        or evidence.current.inode != root.inode
        or not isinstance(founder, SkillAdoptionRecord)
        or founder.action is not SkillAdoptionAction.ADOPT
        or founder.state is not SkillMutationState.COMMITTED
        or founder.name != record.name
        or founder.draft.installation_source != record.draft.installation_source
    ):
        return mark_conflict(repository, record)


def _linked_restore_authority(repository, record):
    authority = record.draft.adoption
    if authority is None:
        return None
    removed = repository.get(record.reverts)
    founder = repository.get(authority.adoption_id)
    evidence = repository.installation_evidence(record.name)
    expected = None
    if (
        isinstance(removed, SkillTreeRecord)
        and removed.action is SkillTreeAction.REMOVE
        and removed.draft.adoption is not None
    ):
        expected = AdoptionAuthority(removed.draft.adoption.adoption_id, record.operation_id, True)
    if (
        expected is None
        or authority != expected
        or record.draft.installation_source != removed.draft.installation_source
        or evidence.current is not None
        or evidence.current_removal_id != removed.operation_id
        or evidence.adoption != removed.draft.adoption
        or not evidence.adoption.active
        or evidence.pending_operation_ids != (record.operation_id,)
        or not isinstance(founder, SkillAdoptionRecord)
        or founder.action is not SkillAdoptionAction.ADOPT
        or founder.state is not SkillMutationState.COMMITTED
        or founder.name != record.name
        or founder.draft.installation_source != record.draft.installation_source
    ):
        return mark_conflict(repository, record)
    return removed


def reconcile_tree_operation(
    repository, files, record: SkillTreeRecord, *, policy
) -> SkillTreeRecord:
    attest_policy_home(policy, files.home)
    guard = repository_guard(policy, repository)
    try:
        guard.check_recovery(record)
        if record.action is SkillTreeAction.REMOVE:
            _linked_remove_authority(repository, record)
        elif record.action is SkillTreeAction.RESTORE:
            _linked_restore_authority(repository, record)
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
        if (
            record.action is SkillTreeAction.RESTORE
            and record.draft.adoption is not None
            and state is SkillMutationState.COMMITTED
        ):
            evidence = repository.installation_evidence(record.name)
            origin = resolve_removal_origin(files.home, record.name, None, installed)
            if origin.provenance is Provenance.BUNDLED or (
                origin.identity_valid is True and origin.provenance is Provenance.SEDIMENT
            ):
                return mark_conflict(repository, record)
            if (
                files.capture_installed(record.name) != installed
                or repository.installation_evidence(record.name) != evidence
            ):
                return mark_conflict(repository, record)
        guard.check_recovery(record)
        files.sync_parents()
        guard.check_recovery(record)
        return repository.finish(record.operation_id, state)
    except SkillMutationError as error:
        if error.kind in ("input", "conflict"):
            repository.finish(record.operation_id, SkillMutationState.CONFLICT)
        raise SkillMutationError(error.kind, str(error), operation_id=record.operation_id) from None
