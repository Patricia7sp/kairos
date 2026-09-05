"""Ciclo de vida do processo Codex App Server."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from .codex_rpc import CodexRpc
from .errors import RuntimeErrorInfo

__all__ = ["CodexSupervisor"]


PRODUCTION_EXECUTABLE = "codex"
PRODUCTION_ARGS = ("app-server", "--listen", "stdio://")
RESTART_BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30)
MAX_RESTART_ATTEMPTS = 5
STDERR_DIAGNOSTIC_LIMIT = 64 * 1024


SubprocessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


def _unavailable() -> RuntimeErrorInfo:
    return RuntimeErrorInfo("unavailable", "Codex App Server indisponível", True)


class CodexSupervisor:
    """Mantém uma geração de App Server e garante encerramento/reap do filho."""

    def __init__(
        self,
        *,
        codex_home: str,
        executable: str = PRODUCTION_EXECUTABLE,
        server_args: Sequence[str] = PRODUCTION_ARGS,
        subprocess_exec: SubprocessFactory = asyncio.create_subprocess_exec,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        generation_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        lock_fds: tuple[int, ...] = (),
        logger: logging.Logger | None = None,
    ) -> None:
        if not os.path.isabs(codex_home) or not os.path.isdir(codex_home):
            raise ValueError("codex_home dedicado deve ser um diretório absoluto existente")
        if not executable or not server_args or any(not arg for arg in server_args):
            raise ValueError("comando do App Server inválido")
        if any(type(fd) is not int or fd < 0 for fd in lock_fds):
            raise ValueError("lock_fds inválido")
        self._command = (executable, *tuple(server_args))
        self._codex_home = codex_home
        self._subprocess_exec = subprocess_exec
        self._clock = clock
        self._sleep = sleep
        self._generation_factory = generation_factory
        self._lock_fds = lock_fds
        self._logger = logger or logging.getLogger(__name__)
        self._process: asyncio.subprocess.Process | None = None
        self._rpc: CodexRpc | None = None
        self._generation: str | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = False
        self._lifecycle_lock = asyncio.Lock()
        self._started_at: float | None = None

    @property
    def generation(self) -> str:
        if self._generation is None:
            raise _unavailable()
        return self._generation

    @property
    def rpc(self) -> CodexRpc:
        if self._rpc is None:
            raise _unavailable()
        return self._rpc

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    async def start(self) -> CodexRpc:
        async with self._lifecycle_lock:
            if self._closed:
                raise _unavailable()
            if self._process is not None and self._process.returncode is None:
                return self.rpc
            await self._cleanup_process()
            return await self._launch()

    async def restart(self) -> CodexRpc:
        async with self._lifecycle_lock:
            if self._closed:
                raise _unavailable()
            await self._cleanup_process()
            last_error: BaseException | None = None
            for attempt in range(MAX_RESTART_ATTEMPTS):
                await self._sleep(RESTART_BACKOFF_SECONDS[attempt])
                try:
                    return await self._launch()
                except (OSError, RuntimeErrorInfo) as exc:
                    last_error = exc
                    await self._cleanup_process()
            raise _unavailable() from last_error

    async def aclose(self) -> None:
        cancelled = False
        cleanup = asyncio.create_task(self._close_owned_resources())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
            await cleanup
        if cancelled:
            raise asyncio.CancelledError

    async def _close_owned_resources(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            await self._cleanup_process()
            for fd in self._lock_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    async def _launch(self) -> CodexRpc:
        generation = self._generation_factory()
        environment = os.environ.copy()
        environment["CODEX_HOME"] = self._codex_home
        try:
            process_or_awaitable = self._subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                pass_fds=self._lock_fds,
                env=environment,
            )
            process = (
                await process_or_awaitable
                if inspect.isawaitable(process_or_awaitable)
                else process_or_awaitable
            )
        except OSError as exc:
            raise _unavailable() from exc
        if process.stdin is None or process.stdout is None or process.stderr is None:
            await self._terminate_and_reap(process)
            raise _unavailable()

        rpc = CodexRpc(process.stdout, process.stdin, generation=generation)
        self._process = process
        self._rpc = rpc
        self._generation = generation
        self._started_at = self._clock()
        self._stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
        await rpc.start()
        try:
            response = await rpc.call(
                "initialize",
                {
                    "clientInfo": {
                        "name": "kairos",
                        "title": "Kairos",
                        "version": "0.1.0",
                    }
                },
            )
            self._validate_initialize(response)
            await rpc.notify("initialized")
        except (OSError, RuntimeErrorInfo):
            await self._cleanup_process()
            raise
        return rpc

    def _validate_initialize(self, response: dict[str, Any]) -> None:
        required_text = ("codexHome", "platformFamily", "platformOs", "userAgent")
        if any(
            not isinstance(response.get(field), str) or not response[field]
            for field in required_text
        ):
            raise RuntimeErrorInfo("incompatible", "Codex App Server incompatível", False)
        if response["userAgent"] != "codex/0.153.4":
            raise RuntimeErrorInfo("incompatible", "Codex App Server incompatível", False)
        if not os.path.isabs(response["codexHome"]):
            raise RuntimeErrorInfo("incompatible", "Codex App Server incompatível", False)
        if response["codexHome"] != self._codex_home:
            raise RuntimeErrorInfo("incompatible", "Codex App Server incompatível", False)

    async def _cleanup_process(self) -> None:
        rpc, process, stderr_task = self._rpc, self._process, self._stderr_task
        self._rpc = None
        self._process = None
        self._stderr_task = None
        self._generation = None
        self._started_at = None
        if rpc is not None:
            await rpc.aclose()
        if process is not None:
            await self._terminate_and_reap(process)
        if stderr_task is not None:
            if not stderr_task.done():
                stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

    @staticmethod
    async def _terminate_and_reap(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    async def _drain_stderr(self, stderr: asyncio.StreamReader) -> None:
        observed = 0
        while True:
            chunk = await stderr.read(4096)
            if not chunk:
                return
            remaining = max(0, STDERR_DIAGNOSTIC_LIMIT - observed)
            counted = min(len(chunk), remaining)
            observed += counted
            if counted:
                self._logger.warning("codex_stderr category=%s bytes=%d", "diagnostic", counted)
