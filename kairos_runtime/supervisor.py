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

from kairos_providers._async_cleanup import (
    PersistentCleanupOutcome,
    run_persistent_cleanup,
)

from .codex_rpc import CodexRpc
from .errors import RuntimeErrorInfo

__all__ = ["CodexSupervisor"]


PRODUCTION_EXECUTABLE = "codex"
PRODUCTION_VERSION_ARGS = ("--version",)
PRODUCTION_ARGS = ("app-server", "--listen", "stdio://")
EXPECTED_CODEX_VERSION = "codex-cli 0.154.0"
SANDBOX_PROBE_ARGS = ("sandbox", "/bin/true")
RESTART_BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30)
MAX_RESTART_ATTEMPTS = 5
STDERR_DIAGNOSTIC_LIMIT = 64 * 1024

_AUTH_ENVIRONMENT_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "KAIROS_WEB_TOKEN",
        "MISTRAL_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
    }
)


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
        version_args: Sequence[str] = PRODUCTION_VERSION_ARGS,
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
        if (
            not executable
            or not version_args
            or any(not arg for arg in version_args)
            or not server_args
            or any(not arg for arg in server_args)
        ):
            raise ValueError("comando do App Server inválido")
        if any(type(fd) is not int or fd < 0 for fd in lock_fds):
            raise ValueError("lock_fds inválido")
        self._command = (executable, *tuple(server_args))
        self._version_command = (executable, *tuple(version_args))
        self._codex_home = codex_home
        self._subprocess_exec = subprocess_exec
        self._clock = clock
        self._sleep = sleep
        self._generation_factory = generation_factory
        self._lock_fds = lock_fds
        self._logger = logger or logging.getLogger(__name__)
        self._process: asyncio.subprocess.Process | None = None
        self._preflight_process: asyncio.subprocess.Process | None = None
        self._rpc: CodexRpc | None = None
        self._generation: str | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = False
        self._lifecycle_lock = asyncio.Lock()
        self._started_at: float | None = None
        self._version_validated = False
        self._sandbox_validated = False

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

    async def probe_sandbox(self, *, timeout: float = 10.0) -> None:
        """Prove the packaged sandbox can execute before container admission."""
        if self._sandbox_validated:
            return
        if timeout <= 0:
            raise ValueError("timeout do probe deve ser positivo")
        environment = _runtime_environment(self._codex_home)
        try:
            process_or_awaitable = self._subprocess_exec(
                self._command[0],
                *SANDBOX_PROBE_ARGS,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            process = (
                await process_or_awaitable
                if inspect.isawaitable(process_or_awaitable)
                else process_or_awaitable
            )
        except OSError as exc:
            raise self._sandbox_unavailable() from exc
        self._preflight_process = process
        try:
            await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as exc:
            await self._cleanup_process()
            raise self._sandbox_unavailable() from exc
        except BaseException:
            await self._cleanup_process()
            raise
        if self._preflight_process is process:
            self._preflight_process = None
        if process.returncode != 0:
            raise self._sandbox_unavailable()
        self._sandbox_validated = True

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
        outcome = await run_persistent_cleanup(
            self._close_owned_resources,
            task_name="codex-supervisor-close",
        )
        self._raise_cleanup_outcome(outcome)

    async def _close_owned_resources(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            await self._cleanup_process_once()
            for fd in self._lock_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._lock_fds = ()
            self._closed = True

    async def _launch(self) -> CodexRpc:
        await self._preflight_version()
        generation = self._generation_factory()
        environment = _runtime_environment(self._codex_home)
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
        self._process = process
        self._generation = generation
        self._started_at = self._clock()
        if process.stdin is None or process.stdout is None or process.stderr is None:
            await self._cleanup_process()
            raise _unavailable()

        rpc = CodexRpc(process.stdout, process.stdin, generation=generation)
        self._rpc = rpc
        self._stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
        try:
            await rpc.start()
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
        except BaseException:
            await self._cleanup_process()
            raise
        return rpc

    async def _preflight_version(self) -> None:
        if self._version_validated:
            return
        environment = _runtime_environment(self._codex_home)
        try:
            process_or_awaitable = self._subprocess_exec(
                *self._version_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            process = (
                await process_or_awaitable
                if inspect.isawaitable(process_or_awaitable)
                else process_or_awaitable
            )
        except OSError as exc:
            raise _unavailable() from exc
        self._preflight_process = process
        try:
            stdout, _stderr = await process.communicate()
        except BaseException:
            await self._cleanup_process()
            raise
        if self._preflight_process is process:
            self._preflight_process = None
        if (
            process.returncode != 0
            or stdout.decode(errors="replace").strip() != EXPECTED_CODEX_VERSION
        ):
            raise RuntimeErrorInfo("incompatible", "versão do Codex incompatível", False)
        self._version_validated = True

    def _validate_initialize(self, response: dict[str, Any]) -> None:
        required_text = ("codexHome", "platformFamily", "platformOs", "userAgent")
        if any(
            not isinstance(response.get(field), str) or not response[field]
            for field in required_text
        ):
            raise RuntimeErrorInfo("incompatible", "Codex App Server incompatível", False)

        if not os.path.isabs(response["codexHome"]):
            raise RuntimeErrorInfo("incompatible", "Codex App Server incompatível", False)
        if response["codexHome"] != self._codex_home:
            raise RuntimeErrorInfo("incompatible", "Codex App Server incompatível", False)

    @staticmethod
    def _sandbox_unavailable() -> RuntimeErrorInfo:
        return RuntimeErrorInfo("unavailable", "sandbox do runtime indisponível", False)

    async def _cleanup_process(self) -> None:
        outcome = await run_persistent_cleanup(
            self._cleanup_process_once,
            task_name="codex-supervisor-process-cleanup",
        )
        self._raise_cleanup_outcome(outcome)

    async def _cleanup_process_once(self) -> None:
        rpc, process, stderr_task = self._rpc, self._process, self._stderr_task
        preflight_process = self._preflight_process
        errors = (
            await self._close_rpc(rpc),
            await self._reap_process(process),
            await self._reap_process(preflight_process),
            await self._finish_stderr(stderr_task),
        )

        process_reaped = process is None or process.returncode is not None
        preflight_reaped = preflight_process is None or preflight_process.returncode is not None
        if preflight_reaped and self._preflight_process is preflight_process:
            self._preflight_process = None
        if process_reaped:
            if self._rpc is rpc:
                self._rpc = None
            if self._process is process:
                self._process = None
            if self._stderr_task is stderr_task:
                self._stderr_task = None
            self._generation = None
            self._started_at = None
        first_error = next((error for error in errors if error is not None), None)
        if first_error is not None:
            raise first_error

    @staticmethod
    async def _close_rpc(rpc: CodexRpc | None) -> BaseException | None:
        if rpc is None:
            return None
        try:
            await rpc.aclose()
        except BaseException as exc:  # noqa: BLE001 - reap ainda precisa ser tentado
            return exc
        return None

    async def _reap_process(
        self, process: asyncio.subprocess.Process | None
    ) -> BaseException | None:
        if process is None:
            return None
        try:
            await self._terminate_and_reap(process)
        except BaseException as exc:  # noqa: BLE001 - ownership fica retido para retry
            return exc
        return None

    @staticmethod
    async def _finish_stderr(stderr_task: asyncio.Task[None] | None) -> BaseException | None:
        if stderr_task is None:
            return None
        if not stderr_task.done():
            stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            return None
        except BaseException as exc:  # noqa: BLE001 - publica após tentar todos recursos
            return exc
        return None

    @staticmethod
    def _raise_cleanup_outcome(outcome: PersistentCleanupOutcome) -> None:
        if outcome.error is not None:
            raise outcome.error
        if outcome.cancellation is not None:
            raise outcome.cancellation

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


def _runtime_environment(codex_home: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _AUTH_ENVIRONMENT_KEYS
        and not key.endswith(("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN", "_SESSION_TOKEN"))
    }
    environment["CODEX_HOME"] = codex_home
    return environment
