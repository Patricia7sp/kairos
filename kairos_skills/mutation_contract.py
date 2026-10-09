"""Contratos verificáveis de autoria manual e seu journal."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.frontmatter import FrontmatterError, parse_frontmatter, validate_frontmatter
from kairos_skills.runtime import MAX_SKILL_BYTES, SkillSelectionError, validate_skill_name


class SkillMutationError(ValueError):
    """Erro público, sem valores da fonte ou exceções de backend."""

    def __init__(self, kind: str, message: str, *, operation_id: str | None = None):
        if kind not in {"input", "conflict", "io", "corrupt", "unavailable"}:
            raise ValueError("Categoria de erro inválida.")
        super().__init__(message)
        self.kind = kind
        self.operation_id = operation_id


def require(condition: bool, message: str = "Registro de autoria inválido.") -> None:
    if not condition:
        raise SkillMutationError("input", message)


def validate_name(name: str) -> None:
    try:
        require(type(name) is str)
        validate_skill_name(name)
    except SkillSelectionError:
        raise SkillMutationError(
            "input", "Nome de skill inválido; use kebab-case até 64 caracteres."
        ) from None


def validate_id(operation_id: str) -> None:
    require(
        type(operation_id) is str and re.fullmatch(r"[0-9a-f]{32}", operation_id) is not None,
        "ID inválido; informe um ID de criação do histórico.",
    )
    value = uuid.UUID(hex=operation_id)
    require(value.version == 4 and value.variant == uuid.RFC_4122, "ID de operação inválido.")


def validate_hash(value: str) -> None:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def validate_actor(actor: Actor) -> None:
    require(actor is Actor.USER_FOREGROUND, "Autoria manual exige o usuário em primeiro plano.")


def _creation_parts(text: str) -> tuple[str, str, int]:
    require(type(text) is str, "A fonte deve ser texto UTF-8.")
    try:
        data = text.encode("utf-8")
        require(0 < len(data) <= MAX_SKILL_BYTES, "SKILL.md excede 64 KiB ou está vazio.")
        fm, body = parse_frontmatter(text, strict_types=True)
        validate_frontmatter(fm, new_skill=True)
        require(bool(body.strip()), "O corpo da skill não pode ser vazio.")
    except (UnicodeError, FrontmatterError):
        raise SkillMutationError(
            "input", "SKILL.md inválido: revise YAML, nome, descrição e versão semver."
        ) from None
    return fm.name, hashlib.sha256(data).hexdigest(), len(data)


@dataclass(frozen=True)
class SkillCreation:
    name: str
    text: str
    sha256: str
    size_bytes: int

    def __post_init__(self):
        name, digest, size = _creation_parts(self.text)
        require(
            type(self.size_bytes) is int
            and (self.name, self.sha256, self.size_bytes) == (name, digest, size)
        )


def validate_skill_creation(text: str) -> SkillCreation:
    name, digest, size = _creation_parts(text)
    return SkillCreation(name, text, digest, size)


@dataclass(frozen=True)
class SkillDirectoryIdentity:
    directory_dev: int
    directory_ino: int
    skill_dev: int
    skill_ino: int

    def __post_init__(self):
        for value in (self.directory_dev, self.directory_ino, self.skill_dev, self.skill_ino):
            require(type(value) is int and 0 <= value < 2**64)
        require(self.directory_ino > 0 and self.skill_ino > 0)
        require(self.directory_dev == self.skill_dev)


@dataclass(frozen=True)
class SkillFilesystemEntry:
    identity: SkillDirectoryIdentity
    creation: SkillCreation

    def __post_init__(self):
        require(
            type(self.identity) is SkillDirectoryIdentity and type(self.creation) is SkillCreation
        )


class SkillMutationAction(StrEnum):
    CREATE = "create"
    ROLLBACK = "rollback"


class SkillMutationState(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    ABORTED = "aborted"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class SkillMutationDraft:
    operation_id: str
    home_id: str
    action: SkillMutationAction
    name: str
    actor: Actor
    created_at: float
    identity: SkillDirectoryIdentity
    sha256: str
    size_bytes: int
    reverts: str | None = None

    def __post_init__(self):
        validate_id(self.operation_id)
        validate_hash(self.home_id)
        validate_hash(self.sha256)
        validate_name(self.name)
        validate_actor(self.actor)
        require(type(self.action) is SkillMutationAction)
        require(
            type(self.created_at) is float
            and math.isfinite(self.created_at)
            and self.created_at >= 0
        )
        require(type(self.identity) is SkillDirectoryIdentity)
        require(type(self.size_bytes) is int and 0 < self.size_bytes <= MAX_SKILL_BYTES)
        if self.action is SkillMutationAction.ROLLBACK:
            validate_id(self.reverts)
            require(self.reverts != self.operation_id)
        else:
            require(self.reverts is None)


@dataclass(frozen=True)
class SkillMutationRecord:
    draft: SkillMutationDraft
    sequence: int
    state: SkillMutationState

    def __post_init__(self):
        require(
            type(self.draft) is SkillMutationDraft
            and type(self.sequence) is int
            and self.sequence > 0
        )
        require(type(self.state) is SkillMutationState)

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

    @property
    def provenance(self):
        return Provenance.USER

    @property
    def sha256(self):
        return self.draft.sha256

    @property
    def reverts(self):
        return self.draft.reverts
