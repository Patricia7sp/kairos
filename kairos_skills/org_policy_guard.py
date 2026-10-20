"""União do veto corrente, namespace e proteção durável, sem overrides."""

from __future__ import annotations

import logging
from contextlib import contextmanager

from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, require, validate_name
from kairos_skills.org_policy import OrgPolicySession
from kairos_skills.org_policy_contract import OrgPolicyAction
from kairos_state.repositories.skill_org_protections import (
    SkillOrgProtectionRepository,
    has_org_schema,
)

_LOGGER = logging.getLogger(__name__)


def attest_policy_home(policy: OrgPolicySession, home) -> None:
    require(isinstance(policy, OrgPolicySession), "Sessão de política obrigatória.")
    policy.revalidate()
    require(policy.home_id == catalog_home_id(home), "Política pertence a outro home.")


def repository_guard(policy: OrgPolicySession, repository) -> SkillOrgGuard:
    require(repository.home_id == policy.home_id, "Política pertence a outro home.")
    repository._timeline()
    protections = (
        SkillOrgProtectionRepository(repository._conn, home_id=repository.home_id)
        if has_org_schema(repository._conn)
        else None
    )
    return SkillOrgGuard(policy, protections, repository=repository)


@contextmanager
def publication_guard(policy: OrgPolicySession, home):
    from kairos_skills.mutation_lock import skill_mutation_lock
    from kairos_skills.removal_state import database_connection, read_skill_repository

    attest_policy_home(policy, home)
    with skill_mutation_lock(home), database_connection(home, write=False) as connection:
        repository = None if connection is None else read_skill_repository(connection, home)
        guard = (
            SkillOrgGuard(policy, None)
            if repository is None
            else repository_guard(policy, repository)
        )
        yield guard


class SkillOrgGuard:
    def __init__(
        self,
        policy: OrgPolicySession,
        protections: SkillOrgProtectionRepository | None,
        *,
        repository=None,
    ):
        require(isinstance(policy, OrgPolicySession))
        policy.revalidate()
        require(protections is None or protections.home_id == policy.home_id)
        self.policy = policy
        self.protections = protections
        self.repository = repository

    def _refuse(self, action, kind, reason, *, operation_id=None):
        _LOGGER.warning(
            "skill_org_operation_refused",
            extra={
                "action": action.value,
                "kind": kind,
                "reason": reason,
                "operation_id": operation_id,
            },
        )
        raise SkillMutationError(
            kind,
            "Proteção organizacional impede a operação; versões preservadas.",
            operation_id=operation_id,
        )

    def check(self, name: str, action: OrgPolicyAction):
        require(type(action) is OrgPolicyAction)
        self.policy.revalidate()
        require(type(name) is str)
        if "_org" in name.split("/"):
            self._refuse(action, "denied", "reserved_namespace")
        if action is OrgPolicyAction.REMOVE_FILE:
            parts = name.split("/")
            require(all(part not in ("", ".", "..") for part in parts))
            name = parts[0]
        validate_name(name)
        if action is OrgPolicyAction.PROMOTE and self.repository is not None:
            pending = self.repository.pending(name)
            if pending:
                self._refuse(
                    action, "conflict", "pending_operation", operation_id=pending[0].operation_id
                )
        receipt = self.policy.receipt(name)
        recorded = (
            () if self.protections is None else self.protections.protected_organizations(name)
        )
        if action not in (
            OrgPolicyAction.CREATE,
            OrgPolicyAction.RESTORE,
            OrgPolicyAction.UNADOPT,
        ) and (receipt.bindings or recorded):
            self._refuse(action, "denied", "protected_target")
        return receipt

    def check_recovery(self, record):
        from kairos_skills.mutation_contract import SkillMutationAction
        from kairos_skills.removal_contract import SkillTreeAction

        require(record.draft.home_id == self.policy.home_id, "Operação pertence a outro home.")
        action = (
            OrgPolicyAction.ROLLBACK_CREATE
            if record.action is SkillMutationAction.ROLLBACK
            else OrgPolicyAction(record.action.value)
        )
        receipt = self.check(record.name, action)
        if record.action in (SkillMutationAction.CREATE, SkillTreeAction.RESTORE):
            prepared = (
                None
                if self.protections is None
                else self.protections.prepared_receipt(record.operation_id)
            )
            known = (
                set()
                if prepared is None
                else {binding.organization_id for binding in prepared.bindings}
            )
            if any(binding.organization_id not in known for binding in receipt.bindings):
                self._refuse(
                    action, "conflict", "new_recovery_binding", operation_id=record.operation_id
                )
        return receipt
