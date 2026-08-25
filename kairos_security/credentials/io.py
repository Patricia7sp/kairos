"""Primitivas duráveis e serializadas para arquivos do cofre."""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_held = threading.local()


@contextmanager
def credential_file_lock(path: Path) -> Iterator[None]:
    key = str(path.resolve())
    with _locks_guard:
        thread_lock = _locks.setdefault(key, threading.RLock())
    with thread_lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except ImportError:
                pass
            _held.paths = held | {key}
            yield
        finally:
            _held.paths = getattr(_held, "paths", set()) - {key}
            try:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            os.close(descriptor)


def secure_atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp.exists():
            tmp.unlink()
