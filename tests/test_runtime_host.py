from __future__ import annotations

import asyncio
import fcntl
import functools
import json
import multiprocessing
import os
import shutil
import socket
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairos_runtime.client import RuntimeClient
from kairos_runtime.host import _Config, _Host, _monitor_runtime, serve_runtime
from kairos_runtime.wire import MAX_MESSAGE_BYTES, runtime_event_to_json

FIXTURE = Path(__file__).parent / "fixtures" / "codex_app_server.py"


def _run_host(home: str) -> None:
    asyncio.run(serve_runtime(Path(home)))


def _run_second_host(home: str, results) -> None:
    try:
        asyncio.run(serve_runtime(Path(home)))
    except BaseException as exc:  # noqa: BLE001 - boundary reports public attributes
        results.put((getattr(exc, "code", None), getattr(exc, "message", None)))


def _decide(socket_path: str, session_id: str, approval_id: str, results) -> None:
    async def run() -> None:
        client = RuntimeClient(socket_path)
        try:
            await client.decide(session_id, approval_id, "decline")
            results.put("decided")
        finally:
            await client.aclose()

    asyncio.run(run())


def _write_codex_wrapper(path: Path, mode: str) -> Path:
    wrapper = path / f"codex-{mode}"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  exec "{sys.executable}" "{FIXTURE}" version\n'
        "fi\n"
        f'exec "{sys.executable}" "{FIXTURE}" {mode}\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return wrapper


def _write_container_probe_wrapper(
    path: Path, sandbox_exit: int, started: Path, probed: Path | None = None
) -> Path:
    wrapper = path / f"codex-sandbox-{sandbox_exit}"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "sandbox" ]; then '
        + (f'touch "{probed}"; ' if probed is not None else "")
        + "exit "
        f"{sandbox_exit}; fi\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  exec "{sys.executable}" "{FIXTURE}" version\n'
        "fi\n"
        f'touch "{started}"\n'
        f'exec "{sys.executable}" "{FIXTURE}" adapter\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return wrapper


def async_test(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


async def _wait_for_socket(socket_path: Path) -> None:
    for _ in range(300):
        if socket_path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("runtime socket was not created")


@async_test
async def test_container_sandbox_failure_keeps_status_unavailable_without_app_server(
    tmp_path: Path,
    caplog,
) -> None:
    started = tmp_path / "app-server-started"
    binary = _write_container_probe_wrapper(tmp_path, 1, started)
    (tmp_path / ".container-mode").write_text("runtime=s6\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        f"agent_runtime:\n  enabled: true\n  codex_binary: {binary}\n  allowed_directories: []\n",
        encoding="utf-8",
    )
    task = asyncio.create_task(serve_runtime(tmp_path))
    client = RuntimeClient(tmp_path / "run" / "runtime.sock")
    try:
        await _wait_for_socket(tmp_path / "run" / "runtime.sock")
        assert await client.status() == {
            "enabled": True,
            "state": "unavailable",
            "authorized_projects": [],
            "sandbox_profiles": ["read_only", "workspace_write"],
        }
        assert not started.exists()
        assert "sandbox do runtime indisponível" in caplog.text
    finally:
        await client.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@async_test
async def test_container_sandbox_success_allows_ready_and_disabled_skips_probe(
    tmp_path: Path,
) -> None:
    enabled_home = tmp_path / "enabled"
    enabled_home.mkdir()
    started = enabled_home / "app-server-started"
    probed = enabled_home / "sandbox-probed"
    binary = _write_container_probe_wrapper(enabled_home, 0, started, probed)
    (enabled_home / ".container-mode").write_text("runtime=s6\n", encoding="utf-8")
    (enabled_home / "config.yaml").write_text(
        f"agent_runtime:\n  enabled: true\n  codex_binary: {binary}\n  allowed_directories: []\n",
        encoding="utf-8",
    )
    task = asyncio.create_task(serve_runtime(enabled_home))
    client = RuntimeClient(enabled_home / "run" / "runtime.sock")
    try:
        await _wait_for_socket(enabled_home / "run" / "runtime.sock")
        assert (await client.status())["state"] == "ready"
        assert probed.exists()
        assert started.exists()
    finally:
        await client.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    disabled_home = tmp_path / "disabled"
    disabled_home.mkdir()
    sentinel = disabled_home / "must-not-run"
    (disabled_home / ".container-mode").write_text("runtime=s6\n", encoding="utf-8")
    (disabled_home / "config.yaml").write_text(
        f"agent_runtime:\n  enabled: false\n  codex_binary: {sentinel}\n",
        encoding="utf-8",
    )
    task = asyncio.create_task(serve_runtime(disabled_home))
    client = RuntimeClient(disabled_home / "run" / "runtime.sock")
    try:
        await _wait_for_socket(disabled_home / "run" / "runtime.sock")
        assert (await client.status())["state"] == "disabled"
        assert not sentinel.exists()
    finally:
        await client.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _wait_for_child(parent_pid: int) -> int:
    children = Path("/proc") / str(parent_pid) / "task" / str(parent_pid) / "children"
    for _ in range(300):
        if children.exists():
            values = children.read_text(encoding="utf-8").split()
            if values:
                return int(values[-1])
        await asyncio.sleep(0.01)
    raise AssertionError("Codex App Server child was not observed")


def _runtime_lock_fds(pid: int) -> list[str]:
    targets = []
    for fd in (Path("/proc") / str(pid) / "fd").iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        if target.endswith("/run/runtime.lock"):
            targets.append(target)
    return targets


@async_test
async def test_disabled_host_is_status_only_and_never_autostarts(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("agent_runtime:\n  enabled: false\n", encoding="utf-8")
    task = asyncio.create_task(serve_runtime(tmp_path))
    socket_path = tmp_path / "run" / "runtime.sock"
    try:
        for _ in range(100):
            if socket_path.exists():
                break
            await asyncio.sleep(0.01)
        client = RuntimeClient(socket_path)
        assert await client.status() == {
            "enabled": False,
            "state": "disabled",
            "authorized_projects": [],
            "sandbox_profiles": [],
        }
        with pytest.raises(Exception) as raised:
            await client.get("missing")
        assert getattr(raised.value, "code", None) == "unavailable"
        await client.aclose()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not socket_path.exists()


@async_test
async def test_status_exposes_only_host_configured_runtime_choices() -> None:
    host = _Host(
        config=_Config(
            enabled=True,
            executable="codex",
            allowed_directories=("/srv/project-a", "/srv/project-b"),
            broad_enabled=False,
        ),
        service=object(),
    )

    assert await host._dispatch("runtime.status", {}) == {
        "enabled": True,
        "state": "ready",
        "authorized_projects": ["/srv/project-a", "/srv/project-b"],
        "sandbox_profiles": ["read_only", "workspace_write"],
    }

    broad = _Host(config=_Config(True, "codex", ("/srv/project-a",), True), service=object())
    assert (await broad._dispatch("runtime.status", {}))["sandbox_profiles"] == [
        "read_only",
        "workspace_write",
        "broad_access",
    ]


@async_test
async def test_command_wire_is_typed_bounded_and_never_leaks_internal_exception(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.yaml").write_text("agent_runtime:\n  enabled: false\n", encoding="utf-8")
    task = asyncio.create_task(serve_runtime(tmp_path))
    socket_path = tmp_path / "run" / "runtime.sock"
    try:
        for _ in range(100):
            if socket_path.exists():
                break
            await asyncio.sleep(0.01)
        reader, writer = await asyncio.open_unix_connection(socket_path)
        writer.write(b'{"id":"r1","method":"not.allowed","params":{}}\n')
        await writer.drain()
        response = json.loads(await reader.readline())
        assert response == {
            "id": "r1",
            "error": {
                "code": "invalid_event",
                "message": "requisição de runtime inválida",
                "retryable": False,
            },
        }
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_unix_connection(socket_path)
        writer.write(b"x" * (MAX_MESSAGE_BYTES + 1) + b"\n")
        await writer.drain()
        oversized = json.loads(await reader.readline())
        assert oversized["error"]["code"] == "invalid_event"
        assert oversized["error"]["message"] == "mensagem de runtime excede o limite"
        writer.close()
        await writer.wait_closed()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_runtime_event_has_one_public_json_converter() -> None:
    from kairos_runtime import RuntimeEvent

    event = RuntimeEvent(
        1,
        "event-1",
        "session-1",
        "turn-1",
        1,
        "v1:session-1:1",
        "tool",
        {"nested": ({"ok": True},)},
    )
    assert runtime_event_to_json(event) == {
        "execution_kind": "agent_runtime",
        "protocol_version": 1,
        "event_id": "event-1",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "sequence": 1,
        "cursor": "v1:session-1:1",
        "kind": "tool",
        "payload": {"nested": [{"ok": True}]},
    }


def test_peer_credentials_accept_current_uid_and_reject_other_uid() -> None:
    if not hasattr(socket, "SO_PEERCRED"):
        pytest.skip("SO_PEERCRED is Linux-specific")

    class PeerSocket:
        def __init__(self, uid: int) -> None:
            self.uid = uid

        def getsockopt(self, level, option, size):
            assert (level, option, size) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            return struct.pack("3i", os.getpid(), self.uid, os.getgid())

    class Writer:
        def __init__(self, uid: int) -> None:
            self.peer = PeerSocket(uid)

        def get_extra_info(self, name):
            assert name == "socket"
            return self.peer

    _Host._check_peer(Writer(os.getuid()))

    with pytest.raises(Exception) as raised:
        _Host._check_peer(Writer(os.getuid() + 1))
    assert getattr(raised.value, "code", None) == "unavailable"
    assert getattr(raised.value, "message", None) == "peer de runtime inválido"
    assert getattr(raised.value, "retryable", None) is False


@async_test
async def test_quiet_subscription_disconnect_closes_only_its_handler(tmp_path: Path) -> None:
    subscribed = asyncio.Event()
    subscription_closed = asyncio.Event()
    keep_quiet = asyncio.Event()

    class Service:
        async def subscribe(self, session_id, cursor=None):
            assert (session_id, cursor) == ("s1", None)
            subscribed.set()
            try:
                await keep_quiet.wait()
                yield  # pragma: no cover - this subscription deliberately stays quiet
            finally:
                subscription_closed.set()

    host = _Host(config=SimpleNamespace(enabled=True), service=Service())
    handlers = []

    def connected(reader, writer):
        handlers.append(asyncio.create_task(host.handle(reader, writer)))

    socket_path = tmp_path / "subscription.sock"
    server = await asyncio.start_unix_server(connected, path=socket_path)
    try:
        _reader, writer = await asyncio.open_unix_connection(socket_path)
        writer.write(
            b'{"id":"subscribe","method":"events.subscribe","params":{"session_id":"s1"}}\n'
        )
        await writer.drain()
        await asyncio.wait_for(subscribed.wait(), 1)

        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(subscription_closed.wait(), 1)
        await asyncio.wait_for(asyncio.gather(*handlers), 1)
        assert all(task.done() and not task.cancelled() for task in handlers)
    finally:
        server.close()
        await server.wait_closed()
        for task in handlers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


@async_test
async def test_rejected_stale_socket_releases_newly_acquired_lock(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "runtime.sock").write_text("not a socket", encoding="utf-8")

    with pytest.raises(Exception) as raised:
        await serve_runtime(tmp_path)

    assert getattr(raised.value, "code", None) == "invalid_directory"
    contender = os.open(run_dir / "runtime.lock", os.O_RDWR)
    try:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(contender)


@async_test
async def test_child_exit_blocks_admission_until_restart_recovery_finishes() -> None:
    class Process:
        def __init__(self) -> None:
            self.returncode = None
            self.exited = asyncio.Event()

        async def wait(self):
            await self.exited.wait()
            return self.returncode

        def exit(self) -> None:
            self.returncode = 1
            self.exited.set()

    old_process = Process()
    new_process = Process()
    restarted = asyncio.Event()
    recovery_started = asyncio.Event()
    allow_recovery = asyncio.Event()
    paused = asyncio.Event()
    calls = []

    class Supervisor:
        process = old_process
        generation = "process-1"

        async def restart(self):
            calls.append("restart")
            self.process = new_process
            self.generation = "process-2"
            restarted.set()

    class Service:
        async def pause_runtime(self, generation):
            calls.append(("pause", generation))
            paused.set()

        async def recover(self):
            calls.append("recover")
            recovery_started.set()
            await allow_recovery.wait()

        def resume_runtime(self):
            calls.append("resume")

        async def get(self, session_id):
            calls.append(("get", session_id))
            return {"session_id": session_id}

    supervisor = Supervisor()
    service = Service()
    host = _Host(config=_Config(True, "codex", (), False), service=service, supervisor=supervisor)
    monitor = asyncio.create_task(_monitor_runtime(host, supervisor, service))
    try:
        old_process.exit()
        await asyncio.wait_for(paused.wait(), 1)
        await asyncio.wait_for(restarted.wait(), 1)
        await asyncio.wait_for(recovery_started.wait(), 1)

        assert await host._dispatch("runtime.status", {}) == {
            "enabled": True,
            "state": "unavailable",
            "authorized_projects": [],
            "sandbox_profiles": ["read_only", "workspace_write"],
        }
        with pytest.raises(Exception) as raised:
            await host._dispatch("session.get", {"session_id": "s1"})
        assert getattr(raised.value, "code", None) == "unavailable"
        assert calls == [("pause", "process-1"), "restart", "recover"]

        allow_recovery.set()
        for _ in range(100):
            if (await host._dispatch("runtime.status", {}))["state"] == "ready":
                break
            await asyncio.sleep(0)
        assert await host._dispatch("session.get", {"session_id": "s1"}) == {"session_id": "s1"}
        assert calls == [
            ("pause", "process-1"),
            "restart",
            "recover",
            "resume",
            ("get", "s1"),
        ]
    finally:
        host.running = False
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


@async_test
async def test_client_disconnect_during_approval_can_be_decided_by_other_process(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    binary = _write_codex_wrapper(tmp_path, "host-approval")
    (tmp_path / "config.yaml").write_text(
        "agent_runtime:\n"
        "  enabled: true\n"
        f"  codex_binary: {binary}\n"
        "  allowed_directories:\n"
        f"    - {project}\n"
        "  broad_access_enabled: false\n",
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    host = context.Process(target=_run_host, args=(str(tmp_path),))
    host.start()
    socket_path = tmp_path / "run" / "runtime.sock"
    try:
        await _wait_for_socket(socket_path)
        first = RuntimeClient(socket_path)
        session = await first.create(
            str(project), "workspace_write", source="test", session_id="runtime-processes"
        )
        turn_id = await first.submit(session["session_id"], "hello", "same-turn")
        cursor = None
        approval_id = None
        stream = first.subscribe(session["session_id"])
        async for event in stream:
            cursor = event.cursor
            if event.turn_id == turn_id and event.kind == "approval_request":
                approval_id = event.payload["approval_id"]
                break
        assert approval_id is not None
        await stream.aclose()
        await first.aclose()

        results = context.Queue()
        second = context.Process(
            target=_decide,
            args=(str(socket_path), session["session_id"], approval_id, results),
        )
        second.start()
        second.join(5)
        assert second.exitcode == 0
        assert results.get(timeout=1) == "decided"

        resumed = RuntimeClient(socket_path)
        terminal = None
        async for event in resumed.subscribe(session["session_id"], cursor):
            if event.turn_id == turn_id and event.kind == "turn_end":
                terminal = event
                break
        assert terminal is not None
        await resumed.aclose()
    finally:
        host.kill()
        host.join(5)
        assert not host.is_alive()


@async_test
async def test_second_host_is_refused_without_disturbing_first(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("agent_runtime:\n  enabled: false\n", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    first = context.Process(target=_run_host, args=(str(tmp_path),))
    first.start()
    socket_path = tmp_path / "run" / "runtime.sock"
    try:
        await _wait_for_socket(socket_path)
        results = context.Queue()
        second = context.Process(target=_run_second_host, args=(str(tmp_path), results))
        second.start()
        second.join(5)
        assert second.exitcode == 0
        assert results.get(timeout=1) == ("unavailable", "host de runtime já está ativo")
        client = RuntimeClient(socket_path)
        assert await client.status() == {
            "enabled": False,
            "state": "disabled",
            "authorized_projects": [],
            "sandbox_profiles": [],
        }
        await client.aclose()
    finally:
        first.kill()
        first.join(5)
        assert not first.is_alive()


@async_test
async def test_orphan_fake_app_server_retains_host_lock_after_abrupt_host_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _write_codex_wrapper(tmp_path, "hold-lock")
    pid_file = tmp_path / "fake-child.pid"
    monkeypatch.setenv("KAIROS_FAKE_PID_FILE", str(pid_file))
    (tmp_path / "config.yaml").write_text(
        f"agent_runtime:\n  enabled: true\n  codex_binary: {binary}\n  allowed_directories: []\n",
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    host = context.Process(target=_run_host, args=(str(tmp_path),))
    host.start()
    socket_path = tmp_path / "run" / "runtime.sock"
    child_pid = None
    try:
        await _wait_for_socket(socket_path)
        for _ in range(300):
            if pid_file.exists():
                child_pid = int(pid_file.read_text(encoding="utf-8"))
                break
            await asyncio.sleep(0.01)
        assert child_pid is not None
        host.kill()
        host.join(5)
        os.kill(child_pid, 0)
        inherited = _runtime_lock_fds(child_pid)
        assert inherited
        contender = os.open(tmp_path / "run" / "runtime.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)
    finally:
        if host.is_alive():
            host.kill()
            host.join(5)
        if child_pid is not None:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(shutil.which("codex") is None, reason="pinned codex is unavailable")
@async_test
async def test_pinned_codex_retains_lock_descriptor_after_abrupt_host_death(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.yaml").write_text(
        "agent_runtime:\n  enabled: true\n  codex_binary: codex\n  allowed_directories: []\n",
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    host = context.Process(target=_run_host, args=(str(tmp_path),))
    host.start()
    socket_path = tmp_path / "run" / "runtime.sock"
    child_pid = None
    try:
        await _wait_for_socket(socket_path)
        client = RuntimeClient(socket_path)
        assert await client.status() == {
            "enabled": True,
            "state": "ready",
            "authorized_projects": [],
            "sandbox_profiles": ["read_only", "workspace_write"],
        }
        await client.aclose()
        child_pid = await _wait_for_child(host.pid)
        host.kill()
        host.join(5)
        inherited = _runtime_lock_fds(child_pid)
        assert inherited
        contender = os.open(tmp_path / "run" / "runtime.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)
    finally:
        if host.is_alive():
            host.kill()
            host.join(5)
        if child_pid is not None:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize(
    "cause, expected",
    [
        ("configuration", "configuração de runtime inválida"),
        ("version", "versão ou protocolo do runtime incompatível"),
    ],
)
@async_test
async def test_startup_logs_static_diagnosis_without_private_details(
    tmp_path, caplog, cause, expected
):
    if cause == "configuration":
        config = "agent_runtime: [private-secret]"
    else:
        binary = tmp_path / "codex-private-secret"
        binary.write_text("#!/bin/sh\necho 'codex-cli 9.9.9 private-secret'\n")
        binary.chmod(0o700)
        config = f"agent_runtime:\n  enabled: true\n  codex_binary: {binary}\n"
    (tmp_path / "config.yaml").write_text(config)
    task = asyncio.create_task(serve_runtime(tmp_path))
    client = RuntimeClient(tmp_path / "run" / "runtime.sock")
    try:
        await _wait_for_socket(tmp_path / "run" / "runtime.sock")
        assert (await client.status())["state"] == "unavailable"
        assert expected in caplog.text
        assert "private-secret" not in caplog.text
        assert len([r for r in caplog.records if r.name == "kairos_runtime.host"]) == 1
    finally:
        await client.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
