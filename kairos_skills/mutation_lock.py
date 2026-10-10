"""Lock único de mutações por instalação, com prazo limitado."""

from __future__ import annotations

import os
import stat
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_skills.mutation_contract import SkillMutationError, require
from kairos_skills.mutation_io import check_chain, io_errors, open_directory, open_fd, same_entry

_mutexes: dict[str, threading.Lock] = {}
_guard = threading.Lock()


@contextmanager
def skill_mutation_lock(home: Path, *, timeout: float = 5.0):
    require(type(timeout) in (int, float) and 0 <= timeout <= 5, "Prazo do lock inválido.")
    deadline = time.monotonic() + timeout
    try:
        import fcntl
    except ImportError:
        raise SkillMutationError(
            "unavailable", "Esta plataforma não oferece lock seguro de skills."
        ) from None
    with _guard:
        mutex = _mutexes.setdefault(str(Path(home).expanduser().absolute()), threading.Lock())
    if not mutex.acquire(timeout=max(0, deadline - time.monotonic())):
        raise SkillMutationError(
            "conflict", "Outra mutação de skills está em andamento; tente novamente."
        )
    try:
        with ExitStack() as stack:
            with io_errors():
                parent, chain = open_directory(stack, home, create=True)
                name = ".skills-write.lock"
                fd = open_fd(stack, name, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK, parent=parent)
                observed = os.fstat(fd)
                if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                    raise SkillMutationError(
                        "conflict", "Lock de skills inseguro; operação recusada."
                    )
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise SkillMutationError(
                                "conflict",
                                "Outra mutação de skills está em andamento; tente novamente.",
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
