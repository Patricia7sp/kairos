"""Seleção explícita e leitura contida de skills instaladas."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from kairos_skills.frontmatter import FrontmatterError, parse_frontmatter, validate_frontmatter

MAX_SELECTED_SKILLS = 8
MAX_SKILL_BYTES = 64 * 1024
MAX_SELECTED_SKILL_BYTES = 128 * 1024
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class SkillSelectionError(ValueError):
    """Recusa pública sem reproduzir o conteúdo da skill."""


def _check_name(name: str) -> None:
    if not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name):
        raise SkillSelectionError("Nome de skill inválido: use kebab-case, até 64 caracteres.")


@dataclass(frozen=True)
class SkillSnapshot:
    name: str
    text: str
    description: str = field(init=False)
    version: str = field(init=False)
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _check_name(self.name)
        if not isinstance(self.text, str):
            raise SkillSelectionError("Conteúdo da skill deve ser texto UTF-8.")
        try:
            data = self.text.encode("utf-8")
        except UnicodeError:
            raise SkillSelectionError("Conteúdo da skill não é UTF-8 válido.") from None
        if len(data) > MAX_SKILL_BYTES:
            raise SkillSelectionError("Skill excede o limite de 64 KiB.")
        try:
            frontmatter, body = parse_frontmatter(self.text, strict_types=True)
            validate_frontmatter(frontmatter, new_skill=False)
        except FrontmatterError:
            raise SkillSelectionError(f"Skill {self.name}: frontmatter inválido.") from None
        if frontmatter.name != self.name:
            raise SkillSelectionError(
                f"Skill {self.name}: nome do frontmatter diverge do diretório."
            )
        if not body.strip():
            raise SkillSelectionError(f"Skill {self.name}: corpo vazio.")
        object.__setattr__(self, "description", frontmatter.description)
        object.__setattr__(self, "version", frontmatter.version)
        object.__setattr__(self, "sha256", hashlib.sha256(data).hexdigest())


def validate_skill_snapshots(skills: Sequence[SkillSnapshot]) -> tuple[SkillSnapshot, ...]:
    if isinstance(skills, (str, bytes)) or not isinstance(skills, Sequence):
        raise SkillSelectionError("Skills devem ser uma sequência de snapshots.")
    selected = tuple(skills)
    if len(selected) > MAX_SELECTED_SKILLS:
        raise SkillSelectionError("Selecione até oito skills por turno.")
    names = set()
    total = 0
    for snapshot in selected:
        if type(snapshot) is not SkillSnapshot:
            raise SkillSelectionError("Snapshot de skill inválido.")
        if snapshot.name in names:
            raise SkillSelectionError("Snapshots de skills repetidos.")
        names.add(snapshot.name)
        total += len(snapshot.text.encode("utf-8"))
    if total > MAX_SELECTED_SKILL_BYTES:
        raise SkillSelectionError("Skills selecionadas excedem o limite total de 128 KiB.")
    return selected


def _open(stack: ExitStack, name, flags: int, *, directory: int | None = None) -> int:
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    stack.callback(os.close, fd)
    return fd


def _read_skill(root: int, name: str) -> str:
    with ExitStack() as stack:
        directory = _open(stack, name, os.O_RDONLY | os.O_DIRECTORY, directory=root)
        fd = _open(stack, "SKILL.md", os.O_RDONLY | os.O_NONBLOCK, directory=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SkillSelectionError(f"Skill {name}: use arquivo regular sem links.")
        if before.st_size > MAX_SKILL_BYTES:
            raise SkillSelectionError("Skill excede o limite de 64 KiB.")
        data = bytearray()
        while len(data) <= MAX_SKILL_BYTES:
            chunk = os.read(fd, MAX_SKILL_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if (
            len(data) > MAX_SKILL_BYTES
            or after.st_nlink != 1
            or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        ):
            raise SkillSelectionError(f"Skill {name}: arquivo alterado ou acima do limite.")
        try:
            return data.decode("utf-8")
        except UnicodeError:
            raise SkillSelectionError(f"Skill {name}: arquivo não é UTF-8 válido.") from None


def load_selected_skills(home: Path, names: Sequence[str]) -> tuple[SkillSnapshot, ...]:
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise SkillSelectionError("Seleção de skills deve ser uma lista de nomes.")
    selected = tuple(names)
    if not selected:
        return ()
    for name in selected:
        _check_name(name)
    selected = tuple(dict.fromkeys(selected))
    if len(selected) > MAX_SELECTED_SKILLS:
        raise SkillSelectionError("Selecione até oito skills por turno.")
    try:
        with ExitStack() as stack:
            home_fd = _open(stack, Path(home).expanduser().resolve(), os.O_RDONLY | os.O_DIRECTORY)
            root = _open(stack, "skills", os.O_RDONLY | os.O_DIRECTORY, directory=home_fd)
            snapshots = [SkillSnapshot(name, _read_skill(root, name)) for name in selected]
            return validate_skill_snapshots(snapshots)
    except OSError:
        raise SkillSelectionError(
            "Não foi possível ler a skill instalada; confira o nome e arquivos sem links em skills."
        ) from None
