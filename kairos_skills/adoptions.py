"""Adoção foreground de instalações existentes e revogação sob lock."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_skills.adoption_contract import (
    SkillAdoptionAction,
    SkillAdoptionDraft,
    SkillAdoptionRecord,
    SkillAdoptionResult,
)
from kairos_skills.catalog import catalog_home_id
from kairos_skills.identification import identify_skill, read_installation_capture
from kairos_skills.identification_contract import InstallationEvidence, OriginEvidence
from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
    require,
    validate_actor,
    validate_id,
    validate_name,
)
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.org_policy import OrgPolicySession
from kairos_skills.org_policy_contract import OrgPolicyAction
from kairos_skills.org_policy_guard import attest_policy_home, repository_guard
from kairos_skills.removal_origin import resolve_removal_origin
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def _same_root(current, captured: TreeCapture) -> bool:
    root = captured.entries[0]
    return current is not None and (current.device, current.inode) == (root.device, root.inode)


def _source_from_origin(origin: OriginEvidence) -> str:
    if origin.provenance is Provenance.USER:
        return "local"
    if origin.provenance is Provenance.BUNDLED:
        return "package_bundle"
    return "unknown"


def _adoption_record(repository: SkillMutationRepository, adoption_id: str, name: str):
    record = repository.get(adoption_id)
    if (
        not isinstance(record, SkillAdoptionRecord)
        or record.action is not SkillAdoptionAction.ADOPT
        or record.state is not SkillMutationState.COMMITTED
        or record.name != name
    ):
        raise SkillMutationError("corrupt", "Linhagem de adoção incompleta; operação recusada.")
    return record


def _require_no_pending(repository: SkillMutationRepository, name: str) -> None:
    pending = repository.pending(name)
    if pending:
        raise SkillMutationError(
            "conflict",
            "Existe operação pendente para este nome; resolva seu ID.",
            operation_id=pending[0].operation_id,
        )


def _conflict(message: str, *, operation_id: str | None = None) -> SkillMutationError:
    return SkillMutationError("conflict", message, operation_id=operation_id)


class SkillAdoptionService:
    """Assume ou revoga administração local sem alterar a árvore instalada."""

    def __init__(
        self, home: Path, repository: SkillMutationRepository, *, policy: OrgPolicySession
    ):
        self.home = Path(home).expanduser().absolute()
        require(".." not in self.home.parts, "Home não pode conter escapes.")
        require(
            repository.home_id == catalog_home_id(self.home),
            "Ledger pertence a outra instalação.",
        )
        attest_policy_home(policy, self.home)
        self.policy = policy
        self.repository = repository

    def _check(self, name, action):
        return repository_guard(self.policy, self.repository).check(name, action)

    @staticmethod
    def _validate_call(name: str, actor: Actor, confirmed: bool) -> None:
        validate_actor(actor)
        if confirmed is not True:
            raise SkillMutationError("denied", "Ação exige confirmação explícita --yes.")
        validate_name(name)

    def _existing_adoption_decision(
        self,
        name: str,
        before: InstallationEvidence,
        captured: TreeCapture,
        origin: OriginEvidence,
    ) -> SkillAdoptionResult | str:
        current = before.current
        same_root = _same_root(current, captured)
        if same_root and current is not None and current.adoption is not None:
            parent = _adoption_record(self.repository, current.adoption.adoption_id, name)
            if current.adoption.active:
                return self._return_existing(name, before, captured, parent, origin)
            return parent.draft.installation_source
        if same_root and current is not None:
            if (
                current.provenance is None
                and current.installation_source == "verified_acquisition"
                and origin.identity_valid is True
            ):
                return "verified_acquisition"
            if current.provenance not in (Provenance.USER, Provenance.BUNDLED):
                raise SkillMutationError(
                    "denied", "A proveniência registrada não pode ser adotada."
                )
            if origin.identity_valid is not True or origin.provenance is not current.provenance:
                raise _conflict("Não foi possível validar a prova independente para adoção.")
            return self._return_existing(name, before, captured, None, origin)
        if current is None and origin.provenance is Provenance.BUNDLED:
            return self._return_existing(name, before, captured, None, origin)
        return "unknown" if current is not None else _source_from_origin(origin)

    def _return_existing(
        self,
        name: str,
        before: InstallationEvidence,
        captured: TreeCapture,
        record: SkillAdoptionRecord | None,
        origin: OriginEvidence,
    ) -> SkillAdoptionResult:
        identification = identify_skill(self.home, name, policy=self.policy)
        latest = self.repository.installation_evidence(name)
        recaptured = read_installation_capture(self.home, name)
        expected_installation = None if before.current is None else before.current.operation_id
        if (
            latest != before
            or recaptured != captured
            or identification.current_installation_id != expected_installation
        ):
            raise _conflict("A prova existente mudou durante a adoção.")
        if record is not None:
            if (
                identification.adoption_id != record.operation_id
                or identification.adoption_active is not True
                or identification.authorization_basis != "user_adoption"
                or identification.installation_source != record.draft.installation_source
            ):
                raise _conflict("A concessão existente mudou durante a adoção.")
        elif (
            identification.authorization_basis != origin.authorization_basis
            or identification.ownership != (origin.provenance or Provenance.USER).value
            or identification.identity_valid != origin.identity_valid
        ):
            raise _conflict("A prova independente mudou durante a adoção.")
        return SkillAdoptionResult(record, identification)

    def _return_committed(
        self,
        name: str,
        captured: TreeCapture,
        record: SkillAdoptionRecord,
    ) -> SkillAdoptionResult:
        identification = identify_skill(self.home, name, policy=self.policy)
        evidence = self.repository.installation_evidence(name)
        recaptured = read_installation_capture(self.home, name)
        current = evidence.current
        authority = evidence.adoption
        if (
            recaptured != captured
            or current is None
            or current.operation_id != record.draft.installation_id
            or (current.device, current.inode) != (record.draft.device, record.draft.inode)
            or authority is None
            or authority.adoption_id != record.operation_id
            or not authority.active
            or evidence.pending_operation_ids
            or identification.current_installation_id != record.draft.installation_id
            or identification.adoption_id != record.operation_id
            or identification.adoption_active is not True
            or identification.authorization_basis != "user_adoption"
            or identification.installation_source != record.draft.installation_source
        ):
            raise _conflict("A adoção mudou antes de confirmar o resultado.")
        return SkillAdoptionResult(record, identification)

    def _revalidate_prepared(
        self,
        name: str,
        operation_id: str,
        before,
        captured: TreeCapture,
    ) -> None:
        recaptured = read_installation_capture(self.home, name)
        evidence = self.repository.installation_evidence(name)
        if (
            recaptured != captured
            or evidence.current != before.current
            or evidence.historical_creation_id != before.historical_creation_id
            or evidence.current_removal_id != before.current_removal_id
            or evidence.adoption != before.adoption
            or evidence.pending_operation_ids != (operation_id,)
        ):
            raise _conflict(
                "Instalação ou linhagem alteradas durante a adoção.", operation_id=operation_id
            )
        pending = self.repository.pending(name)
        if tuple(record.operation_id for record in pending) != (operation_id,):
            raise _conflict("Outra operação tornou a adoção pendente.", operation_id=operation_id)
        origin = resolve_removal_origin(self.home, name, before.current, recaptured)
        if before.current is None and origin.provenance is Provenance.BUNDLED:
            raise SkillMutationError(
                "denied",
                "Uma prova independente de bundle foi confirmada.",
                operation_id=operation_id,
            )

    def _commit_prepared_adoption(
        self,
        name: str,
        operation_id: str,
        before,
        captured: TreeCapture,
    ) -> SkillAdoptionRecord:
        try:
            self._revalidate_prepared(name, operation_id, before, captured)
            self._check(name, OrgPolicyAction.ADOPT)
            committed = self.repository.finish(operation_id, SkillMutationState.COMMITTED)
        except SkillMutationError as error:
            kind = error.kind
            if error.kind == "denied":
                self.repository.finish(operation_id, SkillMutationState.ABORTED)
            elif error.kind in ("conflict", "input"):
                self.repository.finish(operation_id, SkillMutationState.CONFLICT)
                kind = "conflict"
            raise SkillMutationError(
                kind,
                str(error),
                operation_id=error.operation_id or operation_id,
            ) from None
        if committed.state is not SkillMutationState.COMMITTED:
            raise _conflict("Adoção não foi confirmada no ledger.", operation_id=operation_id)
        return committed

    def adopt(self, name: str, *, actor: Actor, confirmed: bool = False) -> SkillAdoptionResult:
        self._validate_call(name, actor, confirmed)
        self._check(name, OrgPolicyAction.ADOPT)
        operation_id = uuid.uuid4().hex
        try:
            with skill_mutation_lock(self.home):
                _require_no_pending(self.repository, name)
                before = self.repository.installation_evidence(name)
                captured = read_installation_capture(self.home, name)
                current = before.current
                same_root = _same_root(current, captured)

                origin = resolve_removal_origin(
                    self.home,
                    name,
                    current,
                    captured,
                )

                decision = self._existing_adoption_decision(name, before, captured, origin)
                if isinstance(decision, SkillAdoptionResult):
                    return decision
                source = decision

                repeated = self.repository.installation_evidence(name)
                latest = read_installation_capture(self.home, name)
                if repeated != before or latest != captured:
                    raise _conflict("Instalação ou ledger alterados durante a adoção.")

                # Recheck the only independently provable source for a home with
                # no current journal installation. A new matching bundle proof
                # suppresses the explicit adoption rather than replacing it.
                if current is None:
                    origin = resolve_removal_origin(self.home, name, None, latest)
                    if origin.provenance is Provenance.BUNDLED:
                        return self._return_existing(name, before, captured, None, origin)
                    source = _source_from_origin(origin)

                draft = SkillAdoptionDraft(
                    operation_id,
                    self.repository.home_id,
                    SkillAdoptionAction.ADOPT,
                    name,
                    actor,
                    time.time(),
                    operation_id,
                    current.operation_id if same_root and current is not None else operation_id,
                    captured.entries[0].device,
                    captured.entries[0].inode,
                    source,
                    operation_id,
                )
                adoption_repository = self.repository._adoption_repository()
                self._check(name, OrgPolicyAction.ADOPT)
                adoption_repository.prepare_adopt(draft, captured)

                committed = self._commit_prepared_adoption(name, operation_id, before, captured)
                return self._return_committed(name, captured, committed)
        except OSError:
            raise SkillMutationError(
                "io",
                "Não foi possível concluir a adoção; arquivos preservados.",
                operation_id=operation_id,
            ) from None

    def unadopt(
        self,
        name: str,
        *,
        actor: Actor,
        confirmed: bool = False,
        expect_adoption: str | None = None,
    ) -> SkillAdoptionRecord:
        self._validate_call(name, actor, confirmed)
        self._check(name, OrgPolicyAction.UNADOPT)
        if expect_adoption is not None:
            validate_id(expect_adoption)

        try:
            with skill_mutation_lock(self.home):
                _require_no_pending(self.repository, name)
                before = self.repository.installation_evidence(name)
                current = before.current
                if current is None:
                    raise _conflict("Instalação ausente; não há adoção para revogar.")
                captured = read_installation_capture(self.home, name)
                if not _same_root(current, captured):
                    raise _conflict("Raiz substituída; adoção preservada.")
                authority = current.adoption
                if authority is None or not authority.active:
                    raise SkillMutationError(
                        "denied", "Não existe adoção vigente para esta instalação."
                    )
                if current.provenance is not Provenance.USER:
                    raise SkillMutationError(
                        "denied", "A proveniência registrada não pertence a uma adoção local."
                    )
                parent = _adoption_record(self.repository, authority.adoption_id, name)
                if authority.installation_id != current.operation_id:
                    raise SkillMutationError("corrupt", "Linhagem de adoção incoerente.")
                if expect_adoption is not None and expect_adoption != authority.adoption_id:
                    raise _conflict("A adoção corrente mudou; nenhuma revogação foi registrada.")

                operation_id = uuid.uuid4().hex
                draft = SkillAdoptionDraft(
                    operation_id,
                    self.repository.home_id,
                    SkillAdoptionAction.UNADOPT,
                    name,
                    actor,
                    time.time(),
                    authority.adoption_id,
                    current.operation_id,
                    current.device,
                    current.inode,
                    parent.draft.installation_source,
                    authority.adoption_id,
                )

                def revalidate_before_unadopt() -> None:
                    self._check(name, OrgPolicyAction.UNADOPT)
                    try:
                        recaptured = read_installation_capture(self.home, name)
                    except SkillMutationError as error:
                        if error.kind == "input":
                            raise _conflict(
                                "Instalação tornou-se insegura antes da revogação.",
                                operation_id=operation_id,
                            ) from None
                        raise
                    evidence = self.repository.installation_evidence(name)
                    lineage = self.repository.get(authority.adoption_id)
                    if (
                        recaptured != captured
                        or evidence != before
                        or evidence.current != current
                        or evidence.adoption != authority
                        or lineage != parent
                        or self.repository.home_id != draft.home_id
                    ):
                        raise _conflict(
                            "Instalação ou linhagem mudaram antes da revogação.",
                            operation_id=operation_id,
                        )

                return self.repository._adoption_repository().commit_unadopt(
                    draft, revalidate=revalidate_before_unadopt
                )
        except OSError:
            raise SkillMutationError(
                "io", "Não foi possível concluir a revogação; arquivos preservados."
            ) from None
