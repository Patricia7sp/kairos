"""Provas privadas, exclusivas e duráveis de retirada de instalações."""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import asdict

from kairos_filesystem.contract import (
    MAX_COMPONENT_BYTES,
    FilesystemError,
    TreeCapture,
    TreeEntry,
    TreeLimits,
)
from kairos_filesystem.descriptors import same_entry
from kairos_filesystem.tree import observed_mount_id, validate_capture

PLUGIN_LIMITS = TreeLimits(256 * 1024 * 1024, 256 * 1024 * 1024, 10000, 32)
# Cada byte POSIX pode virar seis bytes JSON ASCII; 512 cobre chaves,
# separadores, hash e identidades uint64 por entrada e o envelope externo.
MAX_METADATA = 512 + PLUGIN_LIMITS.entries * (
    PLUGIN_LIMITS.depth * (MAX_COMPONENT_BYTES * 6 + 1) + 512
)


def validate_name(name: str) -> None:
    if (
        type(name) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name) is None
        or ".." in name
    ):
        raise FilesystemError(
            "input", "Nome de plugin inválido; use um filho imediato de plugins/."
        )


def validate_id(operation_id: str) -> None:
    try:
        valid = type(operation_id) is str and str(uuid.UUID(operation_id)) == operation_id
    except ValueError:
        valid = False
    if not valid:
        raise FilesystemError("corrupt", "ID da prova de remoção inválido.")


def _directory_id(fd: int) -> str:
    path = os.readlink(f"/proc/self/fd/{fd}")
    operation_id = path.rsplit("/", 1)[-1]
    validate_id(operation_id)
    return operation_id


def _private_file(fd: int, parent: int, name: str) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or observed_mount_id(fd) != observed_mount_id(parent)
    ):
        raise FilesystemError("corrupt", "Prova privada insegura; operação recusada.")
    if os.listxattr(fd):
        raise FilesystemError("corrupt", "Prova privada contém atributos não suportados.")
    same_entry(parent, name, fd)
    return info


def _write(fd: int, name: str, value: dict) -> None:
    data = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    if len(data) > MAX_METADATA:
        raise FilesystemError("input", "Prova excede o limite permitido.")
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd
    )
    try:
        os.fchmod(descriptor, 0o600)
        _private_file(descriptor, fd, name)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining[:65536])
            if written <= 0:
                raise FilesystemError("io", "Gravação de prova incompleta.")
            remaining = remaining[written:]
        os.fsync(descriptor)
        _private_file(descriptor, fd, name)
        os.fsync(fd)
    finally:
        os.close(descriptor)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _read(fd: int, name: str, *, durable: bool = False) -> dict:
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
    )
    try:
        before = _private_file(descriptor, fd, name)
        if not 0 < before.st_size <= MAX_METADATA:
            raise FilesystemError("corrupt", "Tamanho da prova inválido.")
        data = bytearray()
        while chunk := os.read(descriptor, 65536):
            data.extend(chunk)
            if len(data) > MAX_METADATA:
                raise FilesystemError("corrupt", "Prova excede o limite permitido.")
        after = _private_file(descriptor, fd, name)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise FilesystemError("conflict", "Prova alterada durante leitura.")
        try:
            result = json.loads(data, object_pairs_hook=_unique_pairs)
        except (ValueError, UnicodeError, RecursionError):
            raise FilesystemError("corrupt", "Prova de remoção ilegível.") from None
        if type(result) is not dict:
            raise FilesystemError("corrupt", "Prova de remoção inválida.")
        if durable:
            os.fsync(descriptor)
            _private_file(descriptor, fd, name)
            os.fsync(fd)
        return result
    finally:
        os.close(descriptor)


def write_metadata(operation_fd: int, name: str, operation_id: str, capture: TreeCapture) -> None:
    validate_name(name)
    validate_id(operation_id)
    validate_capture(capture, limits=PLUGIN_LIMITS)
    if capture.contents is not None or _directory_id(operation_fd) != operation_id:
        raise FilesystemError("corrupt", "Identidade da prova divergente.")
    _write(
        operation_fd,
        "metadata.json",
        {
            "version": 1,
            "operation_id": operation_id,
            "name": name,
            "entries": [asdict(entry) for entry in capture.entries],
        },
    )


def read_metadata(operation_fd: int) -> tuple[str, str, TreeCapture]:
    value = _read(operation_fd, "metadata.json")
    if (
        set(value) != {"version", "operation_id", "name", "entries"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or type(value["entries"]) is not list
    ):
        raise FilesystemError("corrupt", "Formato da prova de remoção inválido.")
    validate_name(value["name"])
    validate_id(value["operation_id"])
    if _directory_id(operation_fd) != value["operation_id"]:
        raise FilesystemError("corrupt", "ID da prova não corresponde ao diretório.")
    try:
        capture = TreeCapture(tuple(TreeEntry(**entry) for entry in value["entries"]), None)
    except (TypeError, ValueError):
        raise FilesystemError("corrupt", "Inventário da prova inválido.") from None
    validate_capture(capture, limits=PLUGIN_LIMITS)
    return value["name"], value["operation_id"], capture


def write_terminal(operation_fd: int, *, state: str) -> None:
    if state not in ("removed", "aborted"):
        raise FilesystemError("input", "Estado terminal inválido.")
    _write(
        operation_fd,
        "result.json",
        {"version": 1, "operation_id": _directory_id(operation_fd), "state": state},
    )


def read_terminal(operation_fd: int) -> str | None:
    try:
        value = _read(operation_fd, "result.json", durable=True)
    except FileNotFoundError:
        return None
    if (
        set(value) != {"version", "operation_id", "state"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["operation_id"] != _directory_id(operation_fd)
        or value["state"] not in ("removed", "aborted")
    ):
        raise FilesystemError("corrupt", "Resultado terminal inválido.")
    return value["state"]
