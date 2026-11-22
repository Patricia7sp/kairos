"""Checks de segurança do Kairos."""

import os
import stat
from collections.abc import Callable, Collection, Iterator
from pathlib import Path

OMITTED_LINE_EVIDENCE = "Conteúdo da linha omitido por segurança."


def regular_files(
    root: Path,
    *,
    include: Callable[[Path], bool],
    ignored_dirs: Collection[str],
    on_error: Callable[[], None],
) -> Iterator[tuple[Path, Path]]:
    """Enumeração local com erros observáveis; não percorre links de diretório."""
    for directory, dirs, files in os.walk(root, onerror=lambda _error: on_error()):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in ignored_dirs and not (base / d).is_symlink())
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root)
            if not include(relative):
                continue
            try:
                regular = stat.S_ISREG(path.lstat().st_mode)
            except OSError:
                on_error()
                continue
            if regular:
                yield path, relative
            else:
                on_error()
