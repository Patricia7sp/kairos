"""Abertura por descritores e publicação exclusiva de entradas locais."""

from __future__ import annotations

import ctypes
import errno
import os
from contextlib import ExitStack
from pathlib import Path

from kairos_filesystem.contract import FilesystemError


def open_fd(stack: ExitStack, name, flags: int, *, parent: int | None = None, mode=0o600) -> int:
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent)
    stack.callback(os.close, fd)
    return fd


def same_entry(parent: int, name: str, fd: int) -> None:
    observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(fd)
    if (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino):
        raise FilesystemError(
            "conflict", "Entrada alterada durante a operação; arquivos preservados."
        )


def open_directory(
    stack: ExitStack, path: Path, *, create: bool = False
) -> tuple[int, list[tuple[int, str, int]]]:
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise FilesystemError("input", "O caminho não pode conter escapes.")
    fd = open_fd(stack, path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    chain = []
    for part in path.parts[1:]:
        try:
            child = open_fd(stack, part, os.O_RDONLY | os.O_DIRECTORY, parent=fd)
        except FileNotFoundError:
            if not create:
                raise
            check_chain(chain)
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = open_fd(stack, part, os.O_RDONLY | os.O_DIRECTORY, parent=fd)
        chain.append((fd, part, child))
        fd = child
    check_chain(chain)
    return fd, chain


def check_chain(chain: list[tuple[int, str, int]]) -> None:
    for parent, name, fd in chain:
        same_entry(parent, name, fd)


def rename_no_replace(old_parent: int, old_name: str, new_parent: int, new_name: str) -> None:
    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        raise FilesystemError(
            "unavailable", "Publicação segura indisponível nesta plataforma."
        ) from None
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    if function(old_parent, os.fsencode(old_name), new_parent, os.fsencode(new_name), 1) != 0:
        number = ctypes.get_errno()
        if number in (errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL, errno.EXDEV):
            raise FilesystemError(
                "unavailable", "Filesystem não oferece movimento seguro sem substituição."
            )
        if number in (errno.EEXIST, errno.ENOTEMPTY):
            raise FilesystemError(
                "conflict", "O nome está ocupado; ambas as versões foram preservadas."
            )
        raise FilesystemError("io", "Não foi possível mover a entrada com segurança.")
    os.fsync(old_parent)
    os.fsync(new_parent)
