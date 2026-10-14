"""Contratos imutáveis de administração local explícita e revogável."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from kairos_domain.ownership import Actor
from kairos_skills.mutation_contract import (
    SkillMutationState,
    require,
    validate_actor,
    validate_hash,
    validate_id,
    validate_name,
)

if TYPE_CHECKING:
    from kairos_skills.identification_contract import SkillIdentification
    from kairos_skills.removal_contract import CommonRecord

INSTALLATION_SOURCES = frozenset({"unknown", "local", "package_bundle", "verified_acquisition"})


class SkillAdoptionAction(StrEnum):
    ADOPT = "adopt"
    UNADOPT = "unadopt"


@dataclass(frozen=True)
class AdoptionAuthority:
    adoption_id: str
    installation_id: str
    active: bool

    def __post_init__(self):
        validate_id(self.adoption_id)
        validate_id(self.installation_id)
        require(type(self.active) is bool)


@dataclass(frozen=True)
class SkillAdoptionDraft:
    operation_id: str
    home_id: str
    action: SkillAdoptionAction
    name: str
    actor: Actor
    created_at: float
    adoption_id: str
    installation_id: str
    device: int
    inode: int
    installation_source: str
    snapshot_id: str

    def __post_init__(self):
        validate_actor(self.actor)
        validate_id(self.operation_id)
        validate_hash(self.home_id)
        validate_name(self.name)
        validate_id(self.adoption_id)
        validate_id(self.installation_id)
        validate_id(self.snapshot_id)
        require(type(self.action) is SkillAdoptionAction)
        require(
            type(self.created_at) is float
            and math.isfinite(self.created_at)
            and self.created_at >= 0
        )
        require(type(self.device) is int and 0 <= self.device < 2**64)
        require(type(self.inode) is int and 0 < self.inode < 2**64)
        require(
            type(self.installation_source) is str
            and self.installation_source in INSTALLATION_SOURCES
        )
        require(self.snapshot_id == self.adoption_id)
        require(
            (self.action is SkillAdoptionAction.ADOPT) == (self.adoption_id == self.operation_id)
        )


@dataclass(frozen=True)
class SkillAdoptionRecord:
    draft: SkillAdoptionDraft
    state: SkillMutationState

    def __post_init__(self):
        require(type(self.draft) is SkillAdoptionDraft and type(self.state) is SkillMutationState)

    @property
    def operation_id(self):
        return self.draft.operation_id

    @property
    def name(self):
        return self.draft.name

    @property
    def action(self):
        return self.draft.action

    @property
    def actor(self):
        return self.draft.actor


@dataclass(frozen=True)
class SkillAdoptionResult:
    record: CommonRecord | None
    identification: SkillIdentification
