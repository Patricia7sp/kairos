"""Capacidade de arquivos do turno, ancorada num descritor de diretório.

Links simbólicos e arquivos com múltiplos hard links são recusados. Nenhuma
checagem de caminho substitui a abertura relativa ao descritor autorizado.
"""

from __future__ import annotations

import errno
import os
import stat as stat_module
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


class WorkspaceDenied(PermissionError):
    """Erro público sem conteúdo de arquivos nem detalhes do host."""

    def __init__(self) -> None:
        super().__init__("Acesso recusado pelo workspace: use um caminho interno sem links.")


_active: ContextVar[Workspace | None] = ContextVar("kairos_tool_workspace", default=None)
_DIRECTORY = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_WORKSPACE_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "patch",
        "web_search",
        "web_extract",
    }
)


class Workspace:
    def __init__(self, root: str | Path) -> None:
        if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")):
            raise WorkspaceDenied()
        if os.open not in os.supports_dir_fd or os.listdir not in os.supports_fd:
            raise WorkspaceDenied()
        self._lock = threading.Lock()
        self.root = Path(os.path.normpath(Path(root).expanduser()))
        if not self.root.is_absolute():
            raise WorkspaceDenied()
        descriptor = os.open("/", _DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                child = os.open(component, _DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        except BaseException:
            os.close(descriptor)
            raise
        self.fd = descriptor

    def close(self) -> None:
        with self._lock:
            descriptor, self.fd = self.fd, -1
            if descriptor >= 0:
                os.close(descriptor)

    def path(self, value: str | Path) -> Path:
        requested = Path(value).expanduser()
        if not requested.is_absolute():
            requested = self.root / requested
        normalized = Path(os.path.normpath(requested))
        if not normalized.is_relative_to(self.root):
            raise WorkspaceDenied()
        return normalized

    @contextmanager
    def parent(self, value: str | Path, *, create: bool = False):
        parts = self.path(value).relative_to(self.root).parts
        with self._lock:
            if self.fd < 0:
                raise WorkspaceDenied()
            descriptor = os.dup(self.fd)
        try:
            for component in parts[:-1]:
                if create:
                    try:
                        os.mkdir(component, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(component, _DIRECTORY, dir_fd=descriptor)
                except (NotADirectoryError, PermissionError):
                    raise WorkspaceDenied() from None
                os.close(descriptor)
                descriptor = child
            yield descriptor, parts[-1] if parts else "."
        finally:
            os.close(descriptor)

    @contextmanager
    def open(self, value: str | Path, *, directory: bool = False, write: bool = False):
        with self.parent(value, create=write) as (parent, name):
            flags = _DIRECTORY if directory else os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if not directory:
                flags |= os.O_WRONLY | os.O_CREAT if write else os.O_RDONLY
            try:
                descriptor = os.open(name, flags, 0o666, dir_fd=parent)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise WorkspaceDenied() from None
                raise
            try:
                observed = os.fstat(descriptor)
                if not directory and (
                    not stat_module.S_ISREG(observed.st_mode) or observed.st_nlink != 1
                ):
                    raise WorkspaceDenied()
                yield descriptor
            finally:
                os.close(descriptor)


@contextmanager
def workspace_scope(root: str | Path | None):
    workspace = Workspace(root) if root is not None else None
    token = _active.set(workspace)
    try:
        yield workspace
    finally:
        _active.reset(token)
        if workspace is not None:
            workspace.close()


def restricted() -> bool:
    return _active.get() is not None


def tool_allowed(name: str, *, override: bool = False) -> bool:
    if name == "skill_view":
        from kairos_tools.skill_view import skill_catalog_available

        return not override and skill_catalog_available()
    if not restricted():
        return True
    return not override and name in _WORKSPACE_TOOLS


def file_path(value: str | Path) -> Path:
    workspace = _active.get()
    return workspace.path(value) if workspace else Path(value).expanduser()


def read_text(value: str | Path, *, encoding="utf-8", errors="strict") -> str:
    workspace = _active.get()
    if workspace is None:
        return Path(value).read_text(encoding=encoding, errors=errors)
    with (
        workspace.open(value) as descriptor,
        os.fdopen(os.dup(descriptor), encoding=encoding, errors=errors) as stream,
    ):
        return stream.read()


def write_text(value: str | Path, content: str, *, encoding="utf-8") -> None:
    workspace = _active.get()
    if workspace is None:
        Path(value).write_text(content, encoding=encoding)
        return
    with workspace.open(value, write=True) as descriptor:
        os.ftruncate(descriptor, 0)
        with os.fdopen(os.dup(descriptor), "w", encoding=encoding) as stream:
            stream.write(content)


def file_stat(value: str | Path):
    workspace = _active.get()
    if workspace is None:
        return Path(value).stat()
    with workspace.parent(value) as (parent, name):
        observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat_module.S_ISLNK(observed.st_mode) or (
            stat_module.S_ISREG(observed.st_mode) and observed.st_nlink != 1
        ):
            raise WorkspaceDenied()
        return observed


def is_file(value: str | Path) -> bool:
    try:
        return stat_module.S_ISREG(file_stat(value).st_mode)
    except FileNotFoundError:
        return False


def is_dir(value: str | Path) -> bool:
    try:
        return stat_module.S_ISDIR(file_stat(value).st_mode)
    except FileNotFoundError:
        return False


def exists(value: str | Path) -> bool:
    try:
        file_stat(value)
        return True
    except FileNotFoundError:
        return False


def entries(value: str | Path) -> list[Path]:
    workspace = _active.get()
    if workspace is None:
        return list(Path(value).iterdir())
    path = workspace.path(value)
    with workspace.open(value, directory=True) as descriptor:
        return [path / name for name in os.listdir(descriptor)]


def validate_file(value: str | Path) -> None:
    workspace = _active.get()
    if workspace is not None:
        with workspace.open(value):
            pass
