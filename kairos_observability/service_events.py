"""Private, bounded operational journal containing only fixed event codes/counters."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["read_service_events", "record_service_event", "record_service_event_async"]

_MAX_FILE_BYTES = 512 * 1024
_MAX_LINE_BYTES = 4096
_LOCK_TIMEOUT_SECONDS = 0.25
_CURRENT = "service-events.jsonl"
_ROTATED = "service-events.jsonl.1"
_COUNTERS = frozenset({"api_calls", "input_tokens", "output_tokens", "results"})
_CATALOG = {
    "web.started": ("web", "info", "Serviço web iniciado."),
    "web.stopped": ("web", "info", "Serviço web encerrado."),
    "chat.completed": ("chat", "info", "Chamada ao modelo concluída."),
    "chat.failed": ("chat", "error", "Falha na chamada ao modelo."),
    "chat.cancelled": ("chat", "warning", "Chamada ao modelo interrompida."),
    "search.completed": ("search", "info", "Busca web concluída."),
    "search.failed": ("search", "error", "Falha na busca web."),
    "cron.completed": ("cron", "info", "Execução agendada concluída."),
    "cron.failed": ("cron", "error", "Falha na execução agendada."),
    "cron.unknown": ("cron", "warning", "Resultado da execução agendada desconhecido."),
    "cron.no_change": ("cron", "info", "Fonte do monitor não mudou; o agente não rodou."),
    "cron.monitor_error": ("cron", "error", "Fonte do monitor falhou; o agente não rodou."),
}
_SERVICES = frozenset(item[0] for item in _CATALOG.values())
_LEVELS = frozenset(item[1] for item in _CATALOG.values())


def _validated_counters(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) - _COUNTERS:
        raise ValueError("invalid counters")
    if any(
        type(number) is not int or not 0 <= number <= 1_000_000_000 for number in value.values()
    ):
        raise ValueError("invalid counter value")
    return dict(value)


def _private(info: os.stat_result, *, directory: bool = False) -> None:
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not correct_type
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("unsafe journal")
    if not directory and info.st_size > _MAX_FILE_BYTES:
        raise ValueError("oversized journal")


@contextmanager
def _directory(home: Path, *, create: bool) -> Iterator[int]:
    if create:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if create:
            try:
                os.mkdir("logs", mode=0o700, dir_fd=root)
            except FileExistsError:
                pass
        directory = os.open("logs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    finally:
        os.close(root)
    try:
        _private(os.fstat(directory), directory=True)
        yield directory
    finally:
        os.close(directory)


@contextmanager
def _locked(directory: int, *, exclusive: bool) -> Iterator[None]:
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(directory, mode | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("journal busy") from None
            time.sleep(0.005)
    try:
        yield
    finally:
        fcntl.flock(directory, fcntl.LOCK_UN)


@contextmanager
def _file(directory: int, name: str, *, write: bool = False) -> Iterator[int]:
    flags = os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= os.O_WRONLY | os.O_APPEND | os.O_CREAT if write else os.O_RDONLY
    descriptor = os.open(name, flags, 0o600, dir_fd=directory)
    try:
        _private(os.fstat(descriptor))
        yield descriptor
    finally:
        os.close(descriptor)


def _append(directory: int, line: bytes) -> None:
    # Check the backup even when no rotation is needed; never silently replace
    # an unsafe or oversized file owned by a different source.
    try:
        with _file(directory, _ROTATED):
            pass
    except FileNotFoundError:
        pass
    with _file(directory, _CURRENT, write=True) as descriptor:
        rotate = os.fstat(descriptor).st_size + len(line) > _MAX_FILE_BYTES
        if not rotate:
            _write_all(descriptor, line)
    if rotate:
        os.replace(_CURRENT, _ROTATED, src_dir_fd=directory, dst_dir_fd=directory)
        with _file(directory, _CURRENT, write=True) as descriptor:
            _write_all(descriptor, line)
    os.fsync(directory)


def _write_all(descriptor: int, line: bytes) -> None:
    pending = memoryview(line)
    while pending:
        written = os.write(descriptor, pending)
        if written <= 0:
            raise OSError("journal write failed")
        pending = pending[written:]
    os.fsync(descriptor)


def record_service_event(home: Path, code: str, **counters: int) -> bool:
    """Persist one approved event; rejected input and storage failures return False."""
    try:
        if not isinstance(code, str) or code not in _CATALOG:
            return False
        record = {
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "code": code,
            "counters": _validated_counters(counters),
        }
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > _MAX_LINE_BYTES:
            return False
        with _directory(home, create=True) as directory, _locked(directory, exclusive=True):
            _append(directory, line)
        return True
    except (OSError, ValueError, TypeError):
        return False


async def record_service_event_async(home: Path, code: str, **counters: int) -> bool:
    """Offload journal I/O; finish the reserved write before propagating cancellation."""
    # Local import keeps the synchronous journal independent during package boot.
    # This cleanup utility has no dependency on integration or observability.
    from kairos_providers._async_cleanup import run_persistent_cleanup

    written = False

    async def persist() -> None:
        nonlocal written
        written = await asyncio.to_thread(record_service_event, home, code, **counters)

    outcome = await run_persistent_cleanup(persist, task_name="kairos-service-event-write")
    if outcome.cancellation is not None:
        raise outcome.cancellation
    if isinstance(outcome.error, asyncio.CancelledError | KeyboardInterrupt | SystemExit):
        raise outcome.error
    return written if outcome.error is None else False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate journal field")
        result[key] = value
    return result


def _public_record(line: bytes) -> dict[str, Any]:
    if len(line) > _MAX_LINE_BYTES or not line.endswith(b"\n"):
        raise ValueError("invalid journal line")
    record = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(record, dict) or set(record) != {"timestamp", "code", "counters"}:
        raise ValueError("invalid journal record")
    code, timestamp = record["code"], record["timestamp"]
    if not isinstance(code, str) or code not in _CATALOG:
        raise ValueError("invalid journal code")
    if not isinstance(timestamp, str) or len(timestamp) > 40:
        raise ValueError("invalid journal timestamp")
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError("journal timestamp must be UTC")
    service, level, message = _CATALOG[code]
    return {
        "timestamp": parsed.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "code": code,
        "service": service,
        "level": level,
        "message": message,
        "counters": _validated_counters(record["counters"]),
    }


def _read_file(directory: int, name: str) -> list[dict[str, Any]]:
    with _file(directory, name) as descriptor:
        body = bytearray()
        while chunk := os.read(descriptor, min(65_536, _MAX_FILE_BYTES + 1 - len(body))):
            body.extend(chunk)
            if len(body) > _MAX_FILE_BYTES:
                raise ValueError("oversized journal")
    return [_public_record(line) for line in bytes(body).splitlines(keepends=True)]


def _result(
    state: str, events: list[dict[str, Any]] | None = None, *, error: str | None = None
) -> dict:
    result = {
        "state": state,
        "events": events if events is not None else [],
        "source": "service-events",
    }
    if error is not None:
        result["error"] = error
    return result


def read_service_events(
    home: Path, *, limit: int = 50, service: str | None = None, level: str | None = None
) -> dict:
    """Read recent approved events without creating files or touching application state."""
    if (
        type(limit) is not int
        or not 1 <= limit <= 200
        or (service is not None and (not isinstance(service, str) or service not in _SERVICES))
        or (level is not None and (not isinstance(level, str) or level not in _LEVELS))
    ):
        return _result("error", error="invalid_filters")
    try:
        events: list[dict[str, Any]] = []
        present = False
        with _directory(home, create=False) as directory, _locked(directory, exclusive=False):
            for name in (_ROTATED, _CURRENT):
                try:
                    events.extend(_read_file(directory, name))
                    present = True
                except FileNotFoundError:
                    continue
        if not present:
            return _result("unavailable")
        matching = [
            event
            for event in reversed(events)
            if (service is None or event["service"] == service)
            and (level is None or event["level"] == level)
        ]
        matching.sort(key=lambda event: event["timestamp"], reverse=True)
        return _result("ready", matching[:limit])
    except FileNotFoundError:
        return _result("unavailable")
    except (ValueError, TypeError, RecursionError):
        return _result("error", error="invalid_journal")
    except OSError:
        return _result("error", error="read_failed")
