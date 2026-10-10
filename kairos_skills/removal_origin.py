"""Origem comprovada pela projeção ou por bundle confiável do pacote."""

from __future__ import annotations

import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path

from kairos_domain.ownership import Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_skills.mutation_contract import SkillMutationError, validate_name
from kairos_skills.mutation_io import check_chain, io_errors, open_directory, open_fd, read_regular
from kairos_skills.removal_contract import InstallationProof, validate_capture
from kairos_skills.removal_io import capture_skill


def trusted_bundle_root() -> Path:
    return Path(__file__).resolve().parent.parent / "skills"


def _origin_name(name: str) -> None:
    try:
        validate_name(name)
    except SkillMutationError:
        raise SkillMutationError("conflict", "Inventário de origem contém nome inválido.") from None


def _manifest_names(raw: bytes) -> set[str]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError:
        raise SkillMutationError("conflict", "Manifesto bundled inválido.") from None
    names = set()
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        name, sep, digest = entry.partition(":")
        _origin_name(name)
        if not sep or re.fullmatch(r"[0-9a-f]{32}", digest) is None or name in names:
            raise SkillMutationError("conflict", "Manifesto bundled v2 íntegro exigido.")
        names.add(name)
    return names


def _bundle_capture(name: str) -> TreeCapture:
    with io_errors(), ExitStack() as stack:
        root, chain = open_directory(stack, trusted_bundle_root())
        candidates = {}

        def visit(parent: int, component: str, *, category: bool):
            directory = open_fd(stack, component, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
            chain.append((parent, component, directory))
            try:
                skill = os.stat("SKILL.md", dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                if category:
                    discover(directory, category=False)
                return
            if not stat.S_ISREG(skill.st_mode) or skill.st_nlink != 1:
                raise SkillMutationError("conflict", "Bundle contém SKILL.md inseguro.")
            _origin_name(component)
            if component in candidates:
                raise SkillMutationError("conflict", "Bundle contém nomes duplicados.")
            candidates[component] = (parent, component)

        def discover(parent: int, *, category: bool):
            with os.scandir(parent) as entries:
                for entry in entries:
                    value = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(value.st_mode):
                        raise SkillMutationError("conflict", "Links não comprovam origem bundled.")
                    if not entry.name.startswith(".") and stat.S_ISDIR(value.st_mode):
                        visit(parent, entry.name, category=category)

        discover(root, category=True)
        if name not in candidates:
            raise SkillMutationError("conflict", "Origem não comprovada pelo bundle instalado.")
        result = capture_skill(*candidates[name])
        check_chain(chain)
        return result


def _inventory(captured: TreeCapture):
    return tuple(
        (entry.path, entry.kind, entry.mode, entry.size, entry.sha256) for entry in captured.entries
    )


def prove_removal_origin(
    home: Path, name: str, current: InstallationProof | None, captured: TreeCapture
) -> Provenance:
    validate_name(name)
    validate_capture(captured, contents=True)
    with io_errors(), ExitStack() as stack:
        installed, chain = open_directory(stack, Path(home) / "skills")
        if capture_skill(installed, name) != captured:
            raise SkillMutationError("conflict", "Instalação alterada durante prova de origem.")
        check_chain(chain)
        root = captured.entries[0]
        if current is not None:
            if (current.device, current.inode) != (root.device, root.inode):
                raise SkillMutationError("conflict", "Instalação substituída; origem recusada.")
            return current.provenance
        try:
            raw, _ = read_regular(installed, ".bundled_manifest", 1024 * 1024)
        except FileNotFoundError:
            raise SkillMutationError("conflict", "Origem desconhecida; remoção recusada.") from None
        if name not in _manifest_names(raw):
            raise SkillMutationError("conflict", "Nome ausente do manifesto bundled v2.")
        bundled = _bundle_capture(name)
        if _inventory(bundled) != _inventory(captured) or bundled.contents != captured.contents:
            raise SkillMutationError("conflict", "Árvore diverge do bundle; origem recusada.")
        check_chain(chain)
        if capture_skill(installed, name) != captured:
            raise SkillMutationError("conflict", "Instalação alterada durante prova de origem.")
        return Provenance.BUNDLED
