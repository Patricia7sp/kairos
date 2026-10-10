"""Lock único de mutações por instalação, com prazo limitado."""

from __future__ import annotations

import os
import stat
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_filesystem.contract import FilesystemError
from kairos_filesystem.descriptors import check_chain, open_directory, open_fd, same_entry

_mutexes: dict[tuple[str, str], threading.Lock] = {}
_guard = threading.Lock()


@contextmanager
def io_errors():
    try:
        yield
    except OSError:
        raise FilesystemError("io", "Não foi possível acessar o lock com segurança.") from None


@contextmanager
def filesystem_lock(home: Path, name: str, *, timeout: float = 5.0):
    if type(timeout) not in (int, float) or not 0 <= timeout <= 5:
        raise FilesystemError("input", "Prazo do lock inválido.")
    if (
        type(name) is not str
        or not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise FilesystemError("input", "Nome de lock inválido.")
    deadline = time.monotonic() + timeout
    try:
        import fcntl
    except ImportError:
        raise FilesystemError("unavailable", "Esta plataforma não oferece lock seguro.") from None
    with _guard:
        mutex = _mutexes.setdefault(
            (str(Path(home).expanduser().absolute()), name), threading.Lock()
        )
    if not mutex.acquire(timeout=max(0, deadline - time.monotonic())):
        raise FilesystemError("conflict", "Outra mutação está em andamento; tente novamente.")
    try:
        with ExitStack() as stack:
            with io_errors():
                parent, chain = open_directory(stack, home, create=True)
                fd = open_fd(stack, name, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK, parent=parent)
                observed = os.fstat(fd)
                if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                    raise FilesystemError("conflict", "Lock inseguro; operação recusada.")
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise FilesystemError(
                                "conflict",
                                "Outra mutação está em andamento; tente novamente.",
                            ) from None
                        time.sleep(min(0.02, remaining))
            try:
                with io_errors():
                    check_chain(chain)
                    same_entry(parent, name, fd)
                yield
                with io_errors():
                    check_chain(chain)
                    same_entry(parent, name, fd)
            finally:
                with io_errors():
                    fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        mutex.release()
