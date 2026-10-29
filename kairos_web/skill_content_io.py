"""Escrita do editor de skills ancorada em descritores sem seguir links."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from pathlib import Path

from kairos_filesystem.contract import FilesystemError
from kairos_filesystem.descriptors import check_chain, open_directory, open_fd, same_entry

_SKILL_FILENAME = "SKILL.md"


def write_skill_content(home: Path, name: str, content: str) -> None:
    try:
        name.encode("utf-8")
    except UnicodeError:
        raise FilesystemError("input", "Nome de skill inválido.") from None
    relative = Path(name)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts or "\0" in name:
        raise FilesystemError("input", "Nome de skill inválido.")
    data = content.encode("utf-8")
    with ExitStack() as stack:
        parent, chain = open_directory(stack, Path(home) / "skills" / relative, create=True)
        descriptor = open_fd(
            stack, _SKILL_FILENAME, os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK, parent=parent
        )
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise FilesystemError("input", "Use arquivo regular sem links.")
        same_entry(parent, _SKILL_FILENAME, descriptor)
        check_chain(chain)
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            os.ftruncate(descriptor, 0)
            stream.write(data)
            stream.flush()
            os.fsync(descriptor)
        same_entry(parent, _SKILL_FILENAME, descriptor)
        check_chain(chain)
