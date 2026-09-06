"""Host Unix local que possui o serviço e o subprocesso Codex compartilhados."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from kairos_container import CONTAINER_MODE_FILENAME

from .auth import RuntimeAuth
from .codex_adapter import CodexAppServerAdapter
from .errors import RuntimeErrorInfo
from .redaction import public_error
from .service import AgentRuntimeService
from .store import RuntimeStore
from .supervisor import CodexSupervisor
from .wire import MAX_MESSAGE_BYTES, encode_message, read_message, runtime_event_to_json

__all__ = ["serve_runtime"]

_METHODS = frozenset(
    {
        "session.create",
        "session.get",
        "session.end",
        "turn.submit",
        "turn.cancel",
        "events.subscribe",
        "approval.decide",
        "runtime.status",
        "account.status",
        "account.login",
        "account.cancel",
        "account.logout",
    }
)

_SAFE_DETAILED_ERRORS = frozenset({("invalid_event", "mensagem de runtime excede o limite", False)})

# An uncertain cleanup must keep both the original flock and the supervisor
# object reachable. Process supervision can then fail closed instead of
# silently allowing a second host beside a possibly-live child.
_RETAINED_CLEANUPS: list[tuple[int, object, object]] = []


@dataclass(frozen=True)
class _Config:
    enabled: bool
    executable: str
    allowed_directories: tuple[str, ...]
    broad_enabled: bool


class _Host:
    def __init__(
        self,
        *,
        config: _Config | None,
        service=None,
        supervisor=None,
        auth=None,
        startup_error=None,
    ) -> None:
        self.config = config
        self.service = service
        self.supervisor = supervisor
        self.auth = auth
        self.startup_error = startup_error
        self.accepting = True
        self.running = True
        self.mutations = asyncio.Lock()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self._check_peer(writer)
            request = await read_message(reader)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params")
            if (
                not isinstance(request_id, str)
                or not request_id
                or method not in _METHODS
                or not isinstance(params, dict)
            ):
                raise RuntimeErrorInfo("invalid_event", "requisição de runtime inválida", False)
            if method == "events.subscribe":
                await self._subscribe(params, reader, writer)
                return
            result = await self._dispatch(method, params)
            writer.write(encode_message({"id": request_id, "result": result}))
            await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - public IPC error boundary
            error = _public_error(exc)
            request_id = locals().get("request_id")
            try:
                writer.write(
                    encode_message(
                        {
                            "id": request_id if isinstance(request_id, str) else "invalid",
                            "error": {
                                "code": error.code,
                                "message": error.message,
                                "retryable": error.retryable,
                            },
                        }
                    )
                )
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "runtime.status":
            if self.config is not None and not self.config.enabled:
                return {
                    "enabled": False,
                    "state": "disabled",
                    "authorized_projects": [],
                    "sandbox_profiles": [],
                }
            choices = {
                "authorized_projects": list(self.config.allowed_directories)
                if self.config is not None
                else [],
                "sandbox_profiles": [
                    "read_only",
                    "workspace_write",
                    *(
                        ["broad_access"]
                        if self.config is not None and self.config.broad_enabled
                        else []
                    ),
                ]
                if self.config is not None
                else [],
            }
            if not self._runtime_ready():
                return {"enabled": True, "state": "unavailable", **choices}
            return {"enabled": True, "state": "ready", **choices}
        if method.startswith("account."):
            return await self._dispatch_account(method, params)
        # Fail immediately while recovery owns the mutation fence, then verify
        # readiness again after acquiring it to close the child-exit race.
        self._available_service()
        async with self.mutations:
            service = self._available_service()
            if method == "session.create":
                return await service.create(
                    **_exact(
                        params,
                        {"cwd", "sandbox"},
                        {"consent", "source", "parent_session_id", "session_id"},
                    )
                )
            if method == "session.get":
                return await service.get(**_exact(params, {"session_id"}))
            if method == "session.end":
                await service.end(**_exact(params, {"session_id"}))
                return None
            if method == "turn.submit":
                return await service.submit(
                    **_exact(params, {"session_id", "content", "idempotency_key"})
                )
            if method == "turn.cancel":
                await service.cancel(**_exact(params, {"session_id", "turn_id"}))
                return None
            if method == "approval.decide":
                await service.decide(**_exact(params, {"session_id", "approval_id", "decision"}))
                return None
        raise RuntimeErrorInfo("invalid_event", "requisição de runtime inválida", False)

    async def _dispatch_account(self, method: str, params: dict[str, Any]) -> Any:
        self._available_service()
        async with self.mutations:
            service = self._available_service()
            auth = self._available_auth()
            if method == "account.status":
                return await auth.status(**_exact(params, set()))
            if method == "account.login":
                return await auth.login(**_exact(params, {"mode"}, {"api_key"}))
            if method == "account.cancel":
                await auth.cancel(**_exact(params, {"login_id"}))
                return None
            if method == "account.logout":
                _exact(params, set())
                await service.ensure_account_idle()
                await auth.logout()
                return None
        raise RuntimeErrorInfo("invalid_event", "requisição de runtime inválida", False)

    async def _subscribe(
        self,
        params: dict[str, Any],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        service = self._available_service()
        values = _exact(params, {"session_id"}, {"cursor"})
        subscription = service.subscribe(**values)

        async def forward_events() -> None:
            async for event in subscription:
                writer.write(encode_message({"event": runtime_event_to_json(event)}))
                await writer.drain()

        forward = asyncio.create_task(forward_events(), name="runtime-subscription-forward")
        disconnected = asyncio.create_task(reader.read(1), name="runtime-subscription-eof")
        try:
            done, _pending = await asyncio.wait(
                {forward, disconnected}, return_when=asyncio.FIRST_COMPLETED
            )
            if forward in done:
                await forward
        finally:
            for task in (forward, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(forward, disconnected, return_exceptions=True)
            close = getattr(subscription, "aclose", None)
            if close is not None:
                await close()

    def _available_service(self):
        if not self._runtime_ready():
            raise RuntimeErrorInfo("unavailable", "host de runtime indisponível", True)
        return self.service

    def _available_auth(self):
        if self.auth is None:
            raise RuntimeErrorInfo("unavailable", "operações de conta indisponíveis", False)
        return self.auth

    def _runtime_ready(self) -> bool:
        if not self.accepting or self.service is None:
            return False
        if self.supervisor is None:
            return True
        process = self.supervisor.process
        return process is not None and process.returncode is None

    @staticmethod
    def _check_peer(writer: asyncio.StreamWriter) -> None:
        if not hasattr(socket, "SO_PEERCRED"):
            return
        peer = writer.get_extra_info("socket")
        if peer is None:
            raise RuntimeErrorInfo("unavailable", "peer de runtime inválido", False)
        credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        uid = int.from_bytes(credentials[4:8], byteorder="little", signed=True)
        if uid != os.getuid():
            raise RuntimeErrorInfo("unavailable", "peer de runtime inválido", False)


async def serve_runtime(home: Path) -> None:  # noqa: PLR0912, PLR0915
    """Own one canonical-home host until cancellation; never autostarted by clients."""
    canonical_home = _canonical_home(home)
    run_dir = _secure_directory(canonical_home / "run")
    lock_fd = _acquire_lock(run_dir / "runtime.lock")
    socket_path = run_dir / "runtime.sock"
    try:
        _remove_stale_socket(socket_path)
    except BaseException:
        os.close(lock_fd)
        raise
    supervisor = None
    service = None
    auth = None
    server = None
    monitor = None
    config = None
    startup_error = None
    try:
        try:
            config = _load_config(canonical_home)
            if config.enabled:
                codex_home = _secure_directory(canonical_home / "codex-runtime")
                inherited_fd = os.dup(lock_fd)
                try:
                    supervisor = CodexSupervisor(
                        codex_home=str(codex_home),
                        executable=config.executable,
                        lock_fds=(inherited_fd,),
                    )
                except BaseException:
                    os.close(inherited_fd)
                    raise
                if (canonical_home / CONTAINER_MODE_FILENAME).is_file():
                    await supervisor.probe_sandbox()
                await supervisor.start()
                runtime = CodexAppServerAdapter(supervisor)
                auth = RuntimeAuth(supervisor.rpc)
                service = AgentRuntimeService(
                    RuntimeStore(canonical_home / "state.db"),
                    runtime,
                    allowed_directories=config.allowed_directories,
                    broad_enabled=config.broad_enabled,
                    inactivity_confirmed=lambda generation: _attests_inactive(
                        lock_fd, runtime, generation
                    ),
                )
                await service.recover()
        except Exception as exc:  # noqa: BLE001 - status evita restart loop
            startup_error = _public_error(exc)
            if auth is not None:
                await auth.aclose()
                auth = None
            if service is not None:
                await service.aclose()
                service = None
            elif supervisor is not None:
                await supervisor.aclose()
                supervisor = None
        host = _Host(
            config=config,
            service=service,
            supervisor=supervisor,
            auth=auth,
            startup_error=startup_error,
        )
        if service is not None and supervisor is not None:
            monitor = asyncio.create_task(
                _monitor_runtime(host, supervisor, service), name="runtime-host-monitor"
            )
        server = await asyncio.start_unix_server(
            host.handle, path=str(socket_path), limit=MAX_MESSAGE_BYTES + 2
        )
        os.chmod(socket_path, 0o600, follow_symlinks=False)
        async with server:
            await server.serve_forever()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        if "host" in locals():
            host.running = False
            host.accepting = False
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        close_error = None
        try:
            if auth is not None:
                await auth.aclose()
            if service is not None:
                await service.aclose()
            elif supervisor is not None:
                await supervisor.aclose()
        except BaseException as exc:  # noqa: BLE001 - ownership incerta deve ficar retida
            close_error = exc
        if close_error is None:
            _unlink_owned_socket(socket_path)
            os.close(lock_fd)
        if close_error is not None:
            _RETAINED_CLEANUPS.append((lock_fd, service, supervisor))
            raise close_error


def _canonical_home(home: Path) -> Path:
    path = Path(home).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


async def _monitor_runtime(host: _Host, supervisor, service) -> None:
    """Restart a dead child and finish durable recovery before reopening admission."""
    while host.running:
        process = supervisor.process
        if process is None:
            host.accepting = False
            return
        generation = supervisor.generation
        await process.wait()
        if not host.running:
            return
        host.accepting = False
        try:
            async with host.mutations:
                await service.pause_runtime(generation)
                await supervisor.restart()
                if host.auth is not None:
                    await host.auth.replace_rpc(supervisor.rpc)
                await service.recover()
                service.resume_runtime()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - status remains safely unavailable
            return
        if host.running:
            host.accepting = True


def _secure_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeErrorInfo("invalid_directory", "diretório de runtime inseguro", False)
    os.chmod(path, 0o700, follow_symlinks=False)
    return path


def _acquire_lock(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeErrorInfo("unavailable", "host de runtime já está ativo", True) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeErrorInfo("invalid_directory", "lock de runtime inseguro", False)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise RuntimeErrorInfo("unavailable", "host de runtime já está ativo", True) from exc
        raise RuntimeErrorInfo("invalid_directory", "lock de runtime inseguro", False) from exc
    except BaseException:
        os.close(fd)
        raise
    return fd


def _remove_stale_socket(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeErrorInfo("invalid_directory", "socket de runtime inseguro", False)
    path.unlink()


def _unlink_owned_socket(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
        path.unlink()


def _load_config(home: Path) -> _Config:
    path = home / "config.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeErrorInfo("invalid_policy", "configuração de runtime inválida", False) from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RuntimeErrorInfo("invalid_policy", "configuração de runtime inválida", False)
    section = raw.get("agent_runtime", {})
    if not isinstance(section, dict):
        raise RuntimeErrorInfo("invalid_policy", "configuração de runtime inválida", False)
    enabled = section.get("enabled", False)
    broad = section.get("broad_access_enabled", False)
    binary = section.get("codex_binary", "codex")
    roots = section.get("allowed_directories", [])
    if (
        type(enabled) is not bool
        or type(broad) is not bool
        or not isinstance(binary, str)
        or not binary.strip()
        or not isinstance(roots, list)
        or any(not isinstance(root, str) or not root.strip() for root in roots)
    ):
        raise RuntimeErrorInfo("invalid_policy", "configuração de runtime inválida", False)
    return _Config(enabled, binary, tuple(roots), broad)


def _attests_inactive(lock_fd: int, runtime: CodexAppServerAdapter, generation: str) -> bool:
    """Combine exclusive host ownership with confirmed supervisor generations."""
    try:
        fcntl.fcntl(lock_fd, fcntl.F_GETFD)
        current_generation = runtime.generation
    except (OSError, RuntimeErrorInfo):
        return False
    return generation != current_generation


def _exact(
    params: dict[str, Any], required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    allowed = required | (optional or set())
    if not required.issubset(params) or not set(params).issubset(allowed):
        raise RuntimeErrorInfo("invalid_event", "requisição de runtime inválida", False)
    return params


def _public_error(exc: BaseException) -> RuntimeErrorInfo:
    if (
        isinstance(exc, RuntimeErrorInfo)
        and (
            exc.code,
            exc.message,
            exc.retryable,
        )
        in _SAFE_DETAILED_ERRORS
    ):
        return exc
    code = exc.code if isinstance(exc, RuntimeErrorInfo) else "runtime_internal"
    safe = public_error(code)
    return RuntimeErrorInfo(safe["code"], safe["message"], safe["retryable"])
