"""Contratos imutáveis de aquisição, publicação e confirmações duráveis."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture, TreeEntry
from kairos_skills.adoption_contract import AdoptionAuthority
from kairos_skills.mutation_contract import (
    SkillMutationState,
    require,
    validate_hash,
    validate_id,
    validate_name,
)
from kairos_skills.org_policy_contract import OrgPolicyReceipt, OrgProtectionBinding
from kairos_skills.removal_contract import InstallationProof, validate_capture


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def publication_inventory_hash(capture: TreeCapture) -> str:
    validate_capture(capture)
    rows = [(e.path, e.kind, e.mode, e.size, e.sha256) for e in capture.entries]
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()


def acquisition_scan_hash(inventory: str, findings) -> str:
    validate_hash(inventory)
    require(type(findings) in (list, tuple) and all(type(item) is str for item in findings))
    return hashlib.sha256(
        canonical_json({"inventory_sha256": inventory, "findings": list(findings)}).encode()
    ).hexdigest()


def _proof(value):
    validate_capture(value)
    require(value.contents is None)


class PublicationWriter(StrEnum):
    BUNDLED_SYNC = "bundled_sync"
    QUARANTINE_PROMOTE = "quarantine_promote"


class SkillPublicationAction(StrEnum):
    PUBLISH = "publish"


@dataclass(frozen=True)
class AcquisitionReceipt:
    format_version: int
    acquisition_id: str
    home_id: str
    name: str
    issuer: str
    source_key: str
    source_category: str
    inventory_sha256: str
    scan_sha256: str
    caller_actor: Actor
    source_proof: TreeCapture
    quarantine_identity: tuple[int, int, int]

    def __post_init__(self):
        require(type(self.format_version) is int and self.format_version == 1)
        validate_id(self.acquisition_id)
        validate_hash(self.home_id)
        validate_name(self.name)
        validate_id(self.source_key)
        require(self.issuer == "kairos_quarantine_v1" and self.source_category == "unverified")
        require(self.caller_actor is Actor.USER_FOREGROUND)
        require(
            type(self.quarantine_identity) is tuple
            and len(self.quarantine_identity) == 3
            and all(type(value) is int and 0 <= value < 2**64 for value in self.quarantine_identity)
            and self.quarantine_identity[1] > 0
            and self.quarantine_identity[2] > 0
        )
        _proof(self.source_proof)
        require(publication_inventory_hash(self.source_proof) == self.inventory_sha256)
        require(acquisition_scan_hash(self.inventory_sha256, []) == self.scan_sha256)


@dataclass(frozen=True)
class ManifestDelta:
    name: str
    before: str | None
    after: str | None

    def __post_init__(self):
        validate_name(self.name)
        for value in (self.before, self.after):
            require(
                value is None
                or (
                    type(value) is str
                    and (value == "" or re.fullmatch(r"[0-9a-f]{32}", value) is not None)
                )
            )


@dataclass(frozen=True)
class PublicationDraft:
    operation_id: str
    home_id: str
    name: str
    source_ref: str
    source_key: str
    writer: PublicationWriter
    actor: Actor | None
    source_proof: TreeCapture
    staged_tree: TreeCapture
    previous_installation: InstallationProof | None
    previous_tree: TreeCapture | None
    policy_receipt: OrgPolicyReceipt
    manifest_delta: ManifestDelta | None
    created_at: float

    def __post_init__(self):
        validate_id(self.operation_id)
        validate_hash(self.home_id)
        validate_name(self.name)
        require(type(self.writer) is PublicationWriter)
        _proof(self.source_proof)
        _proof(self.staged_tree)
        require(
            publication_inventory_hash(self.source_proof)
            == publication_inventory_hash(self.staged_tree)
        )
        if self.writer is PublicationWriter.BUNDLED_SYNC:
            require(
                self.actor is None
                and self.source_key == "package_bundle"
                and self.source_ref == self.name
            )
        else:
            validate_id(self.source_key)
            require(self.actor is Actor.USER_FOREGROUND)
            parts = self.source_ref.split("/") if type(self.source_ref) is str else []
            require(len(parts) == 2 and parts[1] == self.name)
            validate_id(parts[0])
        if self.previous_tree is not None:
            _proof(self.previous_tree)
        if self.previous_installation is not None:
            require(
                type(self.previous_installation) is InstallationProof
                and self.previous_tree is not None
            )
            root = self.previous_tree.entries[0]
            require(
                (root.device, root.inode)
                == (self.previous_installation.device, self.previous_installation.inode)
            )
        require(type(self.policy_receipt) is OrgPolicyReceipt)
        require(
            (self.policy_receipt.home_id, self.policy_receipt.name) == (self.home_id, self.name)
        )
        require(
            not self.policy_receipt.bindings,
            "Publicação não pode criar exceção ao veto organizacional.",
        )
        require(
            self.manifest_delta is None
            or (
                type(self.manifest_delta) is ManifestDelta and self.manifest_delta.name == self.name
            )
        )
        require(
            type(self.created_at) in (int, float)
            and math.isfinite(self.created_at)
            and self.created_at >= 0
        )


@dataclass(frozen=True)
class SkillPublicationRecord:
    draft: PublicationDraft
    state: SkillMutationState
    manifest_complete: bool
    cleanup_complete: bool
    projection_delta: ManifestDelta | None = None

    @property
    def operation_id(self):
        return self.draft.operation_id

    @property
    def name(self):
        return self.draft.name

    @property
    def action(self):
        return SkillPublicationAction.PUBLISH

    @property
    def home_id(self):
        return self.draft.home_id

    @property
    def actor(self):
        return self.draft.actor

    @property
    def provenance(self):
        return Provenance.BUNDLED if self.draft.writer is PublicationWriter.BUNDLED_SYNC else None

    @property
    def pending(self):
        return self.state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT) or (
            self.state is SkillMutationState.COMMITTED
            and not (self.manifest_complete and self.cleanup_complete)
        )


@dataclass(frozen=True)
class PublicationResult:
    operation_id: str
    name: str
    state: SkillMutationState
    complete: bool
    error_kind: str | None
    manifest_complete: bool
    cleanup_complete: bool

    def __post_init__(self):
        validate_id(self.operation_id)
        validate_name(self.name)
        require(type(self.state) is SkillMutationState)
        require(
            all(
                type(v) is bool
                for v in (self.complete, self.manifest_complete, self.cleanup_complete)
            )
        )
        require(
            not self.complete
            or (
                self.state is SkillMutationState.COMMITTED
                and self.manifest_complete
                and self.cleanup_complete
                and self.error_kind is None
            )
        )


def capture_from_json(value):
    require(
        type(value) is dict and set(value) == {"entries", "contents"} and value["contents"] is None
    )
    result = TreeCapture(tuple(TreeEntry(**entry) for entry in value["entries"]), None)
    _proof(result)
    return result


def receipt_from_json(value):
    value = dict(value)
    value["source_proof"] = capture_from_json(value["source_proof"])
    value["caller_actor"] = Actor(value["caller_actor"])
    value["quarantine_identity"] = tuple(value["quarantine_identity"])
    return AcquisitionReceipt(**value)


def draft_from_json(value):
    value = dict(value)
    value["writer"] = PublicationWriter(value["writer"])
    value["actor"] = None if value["actor"] is None else Actor(value["actor"])
    for field in ("source_proof", "staged_tree", "previous_tree"):
        value[field] = None if value[field] is None else capture_from_json(value[field])
    previous = value["previous_installation"]
    if previous is not None:
        previous = dict(previous)
        previous["provenance"] = (
            None if previous["provenance"] is None else Provenance(previous["provenance"])
        )
        previous["adoption"] = (
            None if previous["adoption"] is None else AdoptionAuthority(**previous["adoption"])
        )
        value["previous_installation"] = InstallationProof(**previous)
    policy = value["policy_receipt"]
    value["policy_receipt"] = OrgPolicyReceipt(
        policy["home_id"],
        policy["name"],
        policy["observation_id"],
        policy["source_digest"],
        tuple(OrgProtectionBinding(**binding) for binding in policy["bindings"]),
    )
    value["manifest_delta"] = (
        None if value["manifest_delta"] is None else ManifestDelta(**value["manifest_delta"])
    )
    return PublicationDraft(**value)


def record_payload(value) -> str:
    return canonical_json(asdict(value))
