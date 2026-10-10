"""Retirada de instalações sem carregar manifesto, código ou configuração."""

from __future__ import annotations

import os
import stat
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from kairos_filesystem.contract import FilesystemError
from kairos_filesystem.descriptors import (
    check_chain,
    open_directory,
    open_fd,
    rename_no_replace,
    same_entry,
)
from kairos_filesystem.lock import filesystem_lock
from kairos_filesystem.tree import (
    _create_directory,
    assert_creation_supported,
    capture_tree,
    delete_verified_tree,
    observed_mount_id,
    verify_tree,
)
from kairos_plugins.removal_records import (
    PLUGIN_LIMITS,
    read_metadata,
    read_terminal,
    validate_id,
    validate_name,
    write_metadata,
    write_terminal,
)


class PluginRemovalError(Exception):
    def __init__(
        self, kind: str, message: str, *, operation_id: str | None = None, retired: bool = False
    ):
        super().__init__(message)
        self.kind = kind
        self.operation_id = operation_id
        self.retired = retired


@dataclass(frozen=True)
class PluginRemovalResult:
    name: str
    operation_id: str
    removed: bool
    requires_restart: bool = True


def _exists(parent: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _private(stack: ExitStack, parent: int, name: str, mount: int, *, create: bool) -> int:
    if create and not _exists(parent, name):
        fd = _create_directory(stack, parent, name, mount)
        os.fsync(parent)
    else:
        fd = open_fd(stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
    info = os.fstat(fd)
    if (
        stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or observed_mount_id(fd) != mount
        or os.listxattr(fd)
    ):
        raise FilesystemError("conflict", "Diretório de provas inseguro; operação recusada.")
    same_entry(parent, name, fd)
    return fd


def _failure(exc, operation_id=None, retired=False):
    kind = exc.kind if isinstance(exc, FilesystemError) else "io"
    message = (
        str(exc)
        if isinstance(exc, FilesystemError)
        else "Falha de I/O; arquivos e provas preservados."
    )
    return PluginRemovalError(kind, message, operation_id=operation_id, retired=retired)


def _recover_record(retired, plugins, name, mount, record_id, *, chain):
    tree = False
    try:
        with ExitStack() as stack:
            operation = _private(stack, retired, record_id, mount, create=False)
            if not _exists(operation, "metadata.json"):
                return None
            recorded_name, operation_id, expected = read_metadata(operation)
            if recorded_name != name:
                return None
            terminal = read_terminal(operation)
            installed = plugins is not None and _exists(plugins, name)
            tree = _exists(operation, "tree")
            if terminal == "aborted" and not tree:
                return None
            if terminal == "removed" and not tree:
                check_chain(chain)
                same_entry(retired, operation_id, operation)
                return PluginRemovalResult(name, operation_id, True)
            if terminal is None and installed and not tree:
                verify_tree(plugins, name, expected)
                check_chain(chain)
                same_entry(retired, operation_id, operation)
                write_terminal(operation, state="aborted")
                return None
            raise PluginRemovalError(
                "conflict",
                "Retirada interrompida ou divergente; resíduo preservado para inspeção.",
                operation_id=operation_id,
                retired=tree or not installed,
            )
    except (FilesystemError, OSError) as exc:
        raise _failure(exc, record_id, tree) from None


def _recover(retired: int, plugins: int | None, name: str, mount: int, chain):
    historical = None
    with os.scandir(retired) as records:
        for index, record in enumerate(records):
            if index >= 10000:
                raise FilesystemError("input", "Limite de provas excedido.")
            try:
                validate_id(record.name)
            except FilesystemError:
                continue
            result = _recover_record(retired, plugins, name, mount, record.name, chain=chain)
            if result is not None:
                historical = result
    return historical


def _retire(stack, chain, plugins, proofs, name, *, capture, mount):
    operation_id = str(uuid.uuid4())
    retired_effect = False
    try:
        operation = _private(stack, proofs, operation_id, mount, create=True)
        chain.append((proofs, operation_id, operation))
        write_metadata(operation, name, operation_id, capture)
        check_chain(chain)
        verify_tree(plugins, name, capture)
        try:
            rename_no_replace(plugins, name, operation, "tree")
        finally:
            retired_effect = _exists(operation, "tree")
        check_chain(chain)
        verify_tree(operation, "tree", capture)
        delete_verified_tree(operation, "tree", capture)
        check_chain(chain)
        write_terminal(operation, state="removed")
        return PluginRemovalResult(name, operation_id, True)
    except (FilesystemError, OSError) as exc:
        raise _failure(exc, operation_id, retired_effect) from None


def remove_plugin(home: Path, name: str, *, confirmed: bool) -> PluginRemovalResult:
    if confirmed is not True:
        raise PluginRemovalError("confirmation", "Confirme a remoção com --yes.")
    try:
        validate_name(name)
    except FilesystemError as exc:
        raise PluginRemovalError(exc.kind, str(exc)) from None
    try:
        with filesystem_lock(home, ".plugins-write.lock"), ExitStack() as stack:
            root, chain = open_directory(stack, home)
            mount = observed_mount_id(root)
            assert_creation_supported(root)
            plugins = None
            if _exists(root, "plugins"):
                plugins = open_fd(stack, "plugins", os.O_RDONLY | os.O_DIRECTORY, parent=root)
                chain.append((root, "plugins", plugins))
                if observed_mount_id(plugins) != mount:
                    raise FilesystemError("input", "Instalação em outro mount; operação recusada.")
            proofs = None
            historical = None
            if _exists(root, ".plugins-retired"):
                proofs = _private(stack, root, ".plugins-retired", mount, create=False)
                chain.append((root, ".plugins-retired", proofs))
                historical = _recover(proofs, plugins, name, mount, chain)
            if plugins is None or not _exists(plugins, name):
                if historical is not None:
                    return historical
                raise FilesystemError("not_found", "Plugin ausente sem prova terminal de remoção.")
            capture = capture_tree(plugins, name, limits=PLUGIN_LIMITS, include_contents=False)
            check_chain(chain)
            if proofs is None:
                proofs = _private(stack, root, ".plugins-retired", mount, create=True)
                chain.append((root, ".plugins-retired", proofs))
            return _retire(stack, chain, plugins, proofs, name, capture=capture, mount=mount)
    except (FilesystemError, OSError) as exc:
        raise _failure(exc) from None
