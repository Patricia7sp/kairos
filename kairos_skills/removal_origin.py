"""Origem comprovada pela projeção ou por bundle confiável do pacote."""

from __future__ import annotations

import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path

from kairos_domain.ownership import Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_filesystem.tree import _metadata, observed_mount_id
from kairos_skills.identification_contract import OriginEvidence
from kairos_skills.mutation_contract import SkillMutationError, validate_name
from kairos_skills.mutation_io import (
    _filesystem_errors,
    check_chain,
    io_errors,
    open_directory,
    open_fd,
    read_regular,
)
from kairos_skills.removal_contract import InstallationProof, validate_capture
from kairos_skills.removal_io import _observed_tree_errors, capture_skill


def trusted_bundle_root() -> Path:
    return Path(__file__).resolve().parent.parent / "skills"


def _origin_name(name: str) -> None:
    try:
        validate_name(name)
    except SkillMutationError:
        raise SkillMutationError("conflict", "Inventário de origem contém nome inválido.") from None


def _manifest_names(raw: bytes) -> tuple[set[str], bool]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError:
        raise SkillMutationError("conflict", "Manifesto bundled inválido.") from None
    names = set()
    version = None
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        name, sep, digest = entry.partition(":")
        _origin_name(name)
        entry_version = 2 if sep else 1
        if (
            name in names
            or (version is not None and version != entry_version)
            or (sep and re.fullmatch(r"[0-9a-f]{32}", digest) is None)
        ):
            raise SkillMutationError("conflict", "Manifesto bundled v2 íntegro exigido.")
        version = entry_version
        names.add(name)
    return names, version == 1


def _stamp(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _check_origin_chain(chain):
    try:
        check_chain(chain)
    except FileNotFoundError:
        raise SkillMutationError(
            "conflict", "Cadeia de origem alterada durante observação."
        ) from None


def _read_manifest(installed: int) -> tuple[bytes, tuple[int, ...]] | None:
    try:
        before = os.stat(".bundled_manifest", dir_fd=installed, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024 * 1024:
        raise SkillMutationError("conflict", "Manifesto instalado inseguro.")
    try:
        raw, after = read_regular(installed, ".bundled_manifest", 1024 * 1024)
    except FileNotFoundError:
        raise SkillMutationError("conflict", "Manifesto alterado durante observação.") from None
    except SkillMutationError as error:
        if error.kind == "input":
            raise SkillMutationError("conflict", "Manifesto instalado inseguro.") from None
        raise
    if _stamp(before) != _stamp(after):
        raise SkillMutationError("conflict", "Manifesto alterado durante observação.")
    return raw, _stamp(after)


def _bundle_capture(name: str) -> TreeCapture | None:
    with _observed_tree_errors(), io_errors(), _filesystem_errors(), ExitStack() as stack:
        root, chain = open_directory(stack, trusted_bundle_root())
        mount = observed_mount_id(root)
        directories = {root: _stamp(_metadata(root, mount))}
        skill_entries = {}
        candidates = {}

        def visit(parent: int, component: str, *, category: bool):
            directory = open_fd(stack, component, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
            chain.append((parent, component, directory))
            directories[directory] = _stamp(_metadata(directory, mount))
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
            skill_entries[directory] = _stamp(skill)

        def discover(parent: int, *, category: bool):
            with os.scandir(parent) as entries:
                for entry in entries:
                    value = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(value.st_mode):
                        raise SkillMutationError("conflict", "Links não comprovam origem bundled.")
                    if not entry.name.startswith(".") and stat.S_ISDIR(value.st_mode):
                        visit(parent, entry.name, category=category)

        def check_discovery():
            _check_origin_chain(chain)
            if any(_stamp(_metadata(fd, mount)) != before for fd, before in directories.items()):
                raise SkillMutationError("conflict", "Inventário do bundle alterado durante prova.")
            for directory, before in skill_entries.items():
                try:
                    after = os.stat("SKILL.md", dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    raise SkillMutationError(
                        "conflict", "Inventário do bundle alterado durante prova."
                    ) from None
                if _stamp(after) != before:
                    raise SkillMutationError(
                        "conflict", "Inventário do bundle alterado durante prova."
                    )

        discover(root, category=True)
        check_discovery()
        if name not in candidates:
            return None
        result = capture_skill(*candidates[name])
        check_discovery()
        if capture_skill(*candidates[name]) != result:
            raise SkillMutationError("conflict", "Bundle alterado durante prova de origem.")
        check_discovery()
        return result


def _inventory(captured: TreeCapture):
    return tuple(
        (entry.path, entry.kind, entry.mode, entry.size, entry.sha256) for entry in captured.entries
    )


def _resolve_bundle_origin(
    name: str, captured: TreeCapture, manifest: tuple[bytes, tuple[int, ...]] | None
) -> OriginEvidence:
    if manifest is None:
        return OriginEvidence(None, None, "none", ("unknown_origin",))
    names, legacy = _manifest_names(manifest[0])
    if legacy:
        return OriginEvidence(None, None, "none", ("legacy_manifest",))
    if name not in names:
        return OriginEvidence(None, None, "none", ("not_in_manifest",))
    bundled = _bundle_capture(name)
    if bundled is None:
        return OriginEvidence(None, None, "none", ("not_in_package_bundle",))
    if _inventory(bundled) != _inventory(captured) or bundled.contents != captured.contents:
        return OriginEvidence(None, None, "none", ("bundle_mismatch",))
    return OriginEvidence(Provenance.BUNDLED, None, "verified_bundle", ())


def resolve_removal_origin(
    home: Path, name: str, current: InstallationProof | None, captured: TreeCapture
) -> OriginEvidence:
    validate_name(name)
    validate_capture(captured, contents=True)
    with io_errors(), ExitStack() as stack:
        installed, chain = open_directory(stack, Path(home) / "skills")
        if capture_skill(installed, name) != captured:
            raise SkillMutationError("conflict", "Instalação alterada durante prova de origem.")
        _check_origin_chain(chain)
        root = captured.entries[0]
        manifest = None
        if current is not None:
            if (current.device, current.inode) != (root.device, root.inode):
                result = OriginEvidence(None, False, "none", ("replaced_root",))
            else:
                result = OriginEvidence(current.provenance, True, "proven_write", ())
        else:
            manifest = _read_manifest(installed)
            result = _resolve_bundle_origin(name, captured, manifest)
        _check_origin_chain(chain)
        if capture_skill(installed, name) != captured:
            raise SkillMutationError("conflict", "Instalação alterada durante prova de origem.")
        if current is None and _read_manifest(installed) != manifest:
            raise SkillMutationError("conflict", "Manifesto alterado durante prova de origem.")
        _check_origin_chain(chain)
        return result


def prove_removal_origin(
    home: Path, name: str, current: InstallationProof | None, captured: TreeCapture
) -> Provenance:
    result = resolve_removal_origin(home, name, current, captured)
    if result.provenance is None:
        raise SkillMutationError("conflict", "Origem não comprovada; remoção recusada.")
    return result.provenance
