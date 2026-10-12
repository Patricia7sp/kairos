"""Evidências imutáveis para diagnóstico de instalações de skills."""

from __future__ import annotations

from dataclasses import dataclass

from kairos_domain.ownership import Provenance
from kairos_skills.removal_contract import InstallationProof


@dataclass(frozen=True)
class InstallationEvidence:
    current: InstallationProof | None
    historical_creation_id: str | None
    current_removal_id: str | None
    pending_operation_ids: tuple[str, ...]


@dataclass(frozen=True)
class OriginEvidence:
    provenance: Provenance | None
    identity_valid: bool | None
    authorization_basis: str
    reasons: tuple[str, ...]
