"""Cópia limitada de projeto, sem seguir links ou preservar metadados perigosos."""

from __future__ import annotations

import io
import os
import stat
import tarfile
from pathlib import Path

EXCLUDED = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".worktrees"}
)


def snapshot_project(
    project: Path,
    *,
    max_bytes: int = 64 * 1024**2,
    max_entries: int = 10000,
    include_all: bool = False,
) -> bytes:
    """Freeze regular files through directory-relative, no-follow descriptors.

    The caller authorizes the project contents. Exclusions are conveniences,
    not general secret detection. Ownership/permissions in the tar are normalized.
    include_all is reserved for isolated worker checkpoints, preserving generated
    hidden state instead of applying the initial host-project exclusions.
    """
    if max_bytes <= 0 or max_entries <= 0:
        raise ValueError("limites de snapshot inválidos")
    # Open every component: a symlink in a parent must not bypass O_NOFOLLOW.
    absolute = Path(os.path.abspath(project))
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            os.close(root_fd)
            root_fd = next_fd
        output = io.BytesIO()
        budget = [max_bytes, max_entries]
        with tarfile.open(fileobj=output, mode="w") as archive:
            _add_directory(
                archive, root_fd, "", budget, os.fstat(root_fd).st_dev, include_all=include_all
            )
        return output.getvalue()
    except OSError as exc:
        raise ValueError("projeto contém caminho inválido ou inacessível") from exc
    finally:
        os.close(root_fd)


def _add_directory(archive, directory_fd, prefix, budget, device, *, include_all):
    for name in sorted(os.listdir(directory_fd)):
        if not include_all and (name in EXCLUDED or name.startswith(".env")):
            continue
        budget[1] -= 1
        if budget[1] < 0:
            raise ValueError("snapshot excede limite de entradas")
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
            raise ValueError("snapshot rejeita links e arquivos especiais")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if stat.S_ISDIR(metadata.st_mode):
            flags |= os.O_DIRECTORY
        fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ValueError("projeto mudou durante snapshot")
            if opened.st_dev != device:
                raise ValueError("snapshot não cruza mounts")
            path = f"{prefix}{name}"
            member = tarfile.TarInfo(path)
            if stat.S_ISDIR(opened.st_mode):
                member.type = tarfile.DIRTYPE
                member.mode = 0o755
                archive.addfile(member)
                _add_directory(archive, fd, path + "/", budget, device, include_all=include_all)
            else:
                if opened.st_nlink != 1 or opened.st_size > budget[0]:
                    raise ValueError("hardlink ou limite de snapshot excedido")
                budget[0] -= opened.st_size
                member.size = opened.st_size
                member.mode = 0o755 if opened.st_mode & 0o111 else 0o644
                with os.fdopen(os.dup(fd), "rb") as source:
                    archive.addfile(member, source)
                after = os.fstat(fd)
                if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    opened.st_size,
                    opened.st_mtime_ns,
                    opened.st_ctime_ns,
                ):
                    raise ValueError("arquivo mudou durante snapshot")
        finally:
            os.close(fd)
