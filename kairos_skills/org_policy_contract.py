"""Contratos imutáveis da política local e de seus recibos sanitizados."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from kairos_skills.mutation_contract import require, validate_hash, validate_id, validate_name


def validate_org_home(value: str) -> Path:
    require(type(value) is str and bool(value) and "\x00" not in value)
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts and str(path) == value)
    return path


def validate_organization_id(value: str) -> None:
    require(type(value) is str and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value) is not None)


@dataclass(frozen=True)
class OrgPolicyContext:
    mode: Literal["none", "file"]
    source: Path | None = field(repr=False)

    def __post_init__(self):
        require(self.mode in ("none", "file"))
        if self.mode == "none":
            require(self.source is None)
        else:
            require(isinstance(self.source, Path))
            validate_org_home(str(self.source))


@dataclass(frozen=True)
class OrgBinding:
    home: str = field(repr=False)
    name: str
    organization_id: str
    active: bool

    def __post_init__(self):
        validate_org_home(self.home)
        validate_name(self.name)
        validate_organization_id(self.organization_id)
        require(type(self.active) is bool)


@dataclass(frozen=True)
class OrgProtectionBinding:
    organization_id: str
    active: bool

    def __post_init__(self):
        validate_organization_id(self.organization_id)
        require(type(self.active) is bool)


@dataclass(frozen=True)
class OrgPolicyReceipt:
    home_id: str
    name: str
    observation_id: str
    source_digest: str | None
    bindings: tuple[OrgProtectionBinding, ...]

    def __post_init__(self):
        validate_hash(self.home_id)
        validate_name(self.name)
        validate_id(self.observation_id)
        if self.source_digest is not None:
            validate_hash(self.source_digest)
        require(type(self.bindings) is tuple)
        require(all(type(binding) is OrgProtectionBinding for binding in self.bindings))
        require(len({binding.organization_id for binding in self.bindings}) == len(self.bindings))
        require(not self.bindings or self.source_digest is not None)


class OrgPolicyAction(StrEnum):
    CREATE = "create"
    RESTORE = "restore"
    REMOVE = "remove"
    REMOVE_FILE = "remove_file"
    ROLLBACK_CREATE = "rollback_create"
    ADOPT = "adopt"
    UNADOPT = "unadopt"
    SYNC = "sync"
    PROMOTE = "promote"
