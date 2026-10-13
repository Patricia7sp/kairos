"""Evidências imutáveis para diagnóstico de instalações de skills."""

from __future__ import annotations

from dataclasses import dataclass

from kairos_domain.ownership import Provenance
from kairos_skills.adoption_contract import AdoptionAuthority
from kairos_skills.removal_contract import InstallationProof


@dataclass(frozen=True)
class InstallationEvidence:
    current: InstallationProof | None
    historical_creation_id: str | None
    current_removal_id: str | None
    pending_operation_ids: tuple[str, ...]
    adoption: AdoptionAuthority | None = None


@dataclass(frozen=True)
class OriginEvidence:
    provenance: Provenance | None
    identity_valid: bool | None
    authorization_basis: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SkillIdentification:
    name: str
    historical_creation_origin: str
    installation_source: str
    authorization_basis: str
    ownership: str
    current_installation_id: str | None
    historical_creation_id: str | None
    current_removal_id: str | None
    pending_operation_ids: tuple[str, ...]
    identity_valid: bool | None
    known_protection: tuple[str, ...]
    protection_coverage: str
    removivel: bool
    reasons: tuple[str, ...]
