"""Captura local e leitura por descritores, sem seguir links."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from kairos_skills import catalog
from kairos_skills.catalog import (
    MAX_REFERENCE_BYTES,
    MAX_REFERENCE_DEPTH,
    MAX_REFERENCES,
    MAX_SKILLS,
    SkillAssetRecord,
    SkillCatalogEntry,
    SkillCatalogError,
    SkillCatalogSnapshot,
    catalog_home_id,
    validate_asset_text,
    validate_reference,
)
from kairos_skills.runtime import (
    MAX_SKILL_BYTES,
    SkillSelectionError,
    SkillSnapshot,
    validate_skill_name,
)


class _ResourceLimitError(SkillCatalogError):
    pass


def _open(stack: ExitStack, name, flags: int, *, directory: int | None = None) -> int:
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    stack.callback(os.close, fd)
    return fd


def _root(stack: ExitStack, home: Path) -> int:
    path = Path(home).expanduser().absolute()
    fd = _open(stack, path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    for part in path.parts[1:]:
        if part == "..":
            raise SkillCatalogError("Home do catálogo deve usar um caminho sem escapes.")
        fd = _open(stack, part, os.O_RDONLY | os.O_DIRECTORY, directory=fd)
    return _open(stack, "skills", os.O_RDONLY | os.O_DIRECTORY, directory=fd)


def _read(directory: int, filename: str, limit: int) -> str:
    with ExitStack() as stack:
        fd = _open(stack, filename, os.O_RDONLY | os.O_NONBLOCK, directory=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise SkillCatalogError(
                "Asset inválido: use arquivo regular sem links dentro do limite."
            )
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(64 * 1024, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if (
            len(data) > limit
            or after.st_nlink != 1
            or len(data) != before.st_size
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise SkillCatalogError("Asset alterado durante leitura ou acima do limite.")
        try:
            return data.decode("utf-8")
        except UnicodeError:
            raise SkillCatalogError("Asset não é UTF-8 válido.") from None


def _record(name: str, reference: str | None, text: str) -> SkillAssetRecord:
    data = text.encode()
    return SkillAssetRecord(name, reference, hashlib.sha256(data).hexdigest(), len(data), len(text))


def _references(
    directory: int, name: str, consume: Callable[[SkillAssetRecord], None]
) -> tuple[list[SkillAssetRecord], int]:
    assets = []
    omitted = 0

    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal omitted
        with os.scandir(fd) as items:
            for item in items:
                reference = prefix + "/" + item.name
                try:
                    if item.is_dir(follow_symlinks=False):
                        if depth >= MAX_REFERENCE_DEPTH:
                            omitted += 1
                            continue
                        with ExitStack() as stack:
                            child = _open(
                                stack, item.name, os.O_RDONLY | os.O_DIRECTORY, directory=fd
                            )
                            walk(child, reference, depth + 1)
                    elif Path(item.name).suffix in (".md", ".txt") or item.is_symlink():
                        validate_reference(reference)
                        text = _read(fd, item.name, MAX_REFERENCE_BYTES)
                        record = _record(name, reference, text)
                        consume(record)
                        assets.append(record)
                except _ResourceLimitError:
                    raise
                except (OSError, SkillCatalogError):
                    omitted += 1
                if len(assets) > MAX_REFERENCES:
                    raise _ResourceLimitError("Catálogo acima do limite de referências por skill.")

    with ExitStack() as stack:
        try:
            refs = _open(stack, "references", os.O_RDONLY | os.O_DIRECTORY, directory=directory)
        except FileNotFoundError:
            return [], 0
        except OSError:
            return [], 1
        walk(refs, "references", 0)
    return sorted(assets, key=lambda a: a.reference), omitted


def capture_skill_catalog(home: Path) -> SkillCatalogSnapshot:
    entries = []
    omitted_skills = omitted_refs = 0
    total_bytes = 0

    def consume(record: SkillAssetRecord) -> None:
        nonlocal total_bytes
        total_bytes += record.size_bytes
        if total_bytes > catalog.MAX_TOTAL_ASSET_BYTES:
            raise _ResourceLimitError("Catálogo acima do limite total de assets.")

    try:
        with ExitStack() as stack:
            root = _root(stack, home)
            with os.scandir(root) as items:
                for item in items:
                    if not item.is_dir(follow_symlinks=False) and not item.is_symlink():
                        continue
                    try:
                        validate_skill_name(item.name)
                        with ExitStack() as entry_stack:
                            directory = _open(
                                entry_stack, item.name, os.O_RDONLY | os.O_DIRECTORY, directory=root
                            )
                            text = _read(directory, "SKILL.md", MAX_SKILL_BYTES)
                            skill = SkillSnapshot(item.name, text)
                            record = _record(skill.name, None, text)
                            consume(record)
                            refs, count = _references(directory, item.name, consume)
                            entries.append(
                                SkillCatalogEntry(
                                    skill.name, skill.description, skill.version, (record, *refs)
                                )
                            )
                            omitted_refs += count
                    except (OSError, SkillSelectionError):
                        omitted_skills += 1
                    except _ResourceLimitError:
                        raise
                    except SkillCatalogError:
                        omitted_skills += 1
                    if len(entries) > MAX_SKILLS:
                        raise SkillCatalogError("Catálogo acima do limite de skills.")
    except OSError:
        raise SkillCatalogError(
            "Não foi possível capturar o catálogo instalado sem links."
        ) from None
    return SkillCatalogSnapshot(
        catalog_home_id(home),
        tuple(sorted(entries, key=lambda e: e.name)),
        omitted_skills,
        omitted_refs,
    )


def read_catalog_asset(home: Path, asset: SkillAssetRecord) -> str:
    if type(asset) is not SkillAssetRecord:
        raise SkillCatalogError("Asset não pertence ao catálogo.")
    try:
        with ExitStack() as stack:
            directory = _root(stack, home)
            directory = _open(stack, asset.name, os.O_RDONLY | os.O_DIRECTORY, directory=directory)
            parts = ["SKILL.md"] if asset.reference is None else asset.reference.split("/")
            for part in parts[:-1]:
                directory = _open(stack, part, os.O_RDONLY | os.O_DIRECTORY, directory=directory)
            text = _read(
                directory,
                parts[-1],
                MAX_SKILL_BYTES if asset.reference is None else MAX_REFERENCE_BYTES,
            )
            validate_asset_text(asset, text)
            return text
    except OSError:
        raise SkillCatalogError("Asset indisponível; inicie uma nova sessão.") from None
