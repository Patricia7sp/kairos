from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kairos_runtime.codex_adapter import CodexAppServerAdapter
from kairos_runtime.contracts import RuntimeSession
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.supervisor import CodexSupervisor

FIXTURE = Path(__file__).parent / "fixtures" / "codex_app_server.py"


def async_test(function: Callable[..., Any]) -> Callable[..., None]:
    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(function(*args, **kwargs))

    return run


@async_test
async def test_start_handshakes_with_dedicated_home_and_new_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dedicated_home = tmp_path / "codex-runtime"
    dedicated_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "personal-codex-home"))
    supervisor = CodexSupervisor(
        codex_home=str(dedicated_home),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "adapter"),
        generation_factory=iter(("generation-a", "generation-b")).__next__,
    )
    try:
        rpc = await supervisor.start()
        assert rpc is supervisor.rpc
        assert supervisor.generation == "generation-a"
        await supervisor.restart()
        assert supervisor.generation == "generation-b"
    finally:
        await supervisor.aclose()


@async_test
async def test_start_rejects_mismatched_binary_version_before_initialize(
    tmp_path: Path,
) -> None:
    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "bad-version"),
        server_args=(str(FIXTURE), "adapter"),
    )

    with pytest.raises(RuntimeErrorInfo) as raised:
        await supervisor.start()

    assert raised.value.code == "incompatible"
    assert supervisor.process is None
    await supervisor.aclose()


@async_test
async def test_cancelled_version_preflight_reaps_its_process(tmp_path: Path) -> None:
    communicate_started = asyncio.Event()

    class FakeVersionProcess:
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            communicate_started.set()
            await asyncio.Future()
            raise AssertionError("unreachable")

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            assert self.returncode is not None
            return self.returncode

    process = FakeVersionProcess()

    async def create_process(*args: object, **kwargs: object) -> FakeVersionProcess:
        del args, kwargs
        return process

    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable="fake-codex",
        subprocess_exec=create_process,  # type: ignore[arg-type]
    )
    start = asyncio.create_task(supervisor.start())
    await communicate_started.wait()
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start

    assert process.returncode is not None
    await supervisor.aclose()


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex binary is unavailable")
@async_test
async def test_real_binary_initialize_start_and_resume_compatibility_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex-home"
    workspace = tmp_path / "workspace"
    codex_home.mkdir()
    workspace.mkdir()
    supervisor = CodexSupervisor(codex_home=str(codex_home))
    runtime = CodexAppServerAdapter(supervisor)
    session = RuntimeSession(
        session_id="compatibility-probe",
        runtime_kind="codex",
        cwd=str(workspace),
        sandbox="workspace_write",
        external_thread_id=None,
    )
    process = None
    try:
        await supervisor.start()
        process = supervisor.process
        thread_id = await runtime.create_thread(session)
        await supervisor.restart()
        resume_rpc = supervisor.rpc
        rpc_error_codes: list[object] = []
        dispatch = resume_rpc._dispatch

        def capture_error_code(message: dict[str, Any]) -> None:
            error = message.get("error")
            if isinstance(error, dict):
                rpc_error_codes.append(error.get("code"))
            dispatch(message)

        monkeypatch.setattr(resume_rpc, "_dispatch", capture_error_code)
        with pytest.raises(RuntimeErrorInfo) as raised:
            await runtime.resume_thread(
                RuntimeSession(
                    session_id=session.session_id,
                    runtime_kind=session.runtime_kind,
                    cwd=session.cwd,
                    sandbox=session.sandbox,
                    external_thread_id=thread_id,
                )
            )
        assert raised.value.code == "thread_missing"
        assert rpc_error_codes == [-32600]
        assert supervisor.process is not None
        assert supervisor.process.returncode is None
    finally:
        await runtime.aclose()

    assert process is not None
    assert process.returncode is not None


@async_test
async def test_restart_uses_exponential_backoff_and_stops_after_five_attempts(
    tmp_path: Path,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "bad-initialize"),
        sleep=fake_sleep,
    )
    with pytest.raises(RuntimeErrorInfo):
        await supervisor.restart()
    assert sleeps == [1, 2, 4, 8, 16]
    await supervisor.aclose()


@async_test
async def test_aclose_reaps_child_even_when_cleanup_caller_is_cancelled(tmp_path: Path) -> None:
    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "adapter"),
    )
    await supervisor.start()
    process = supervisor.process
    assert process is not None

    cleanup = asyncio.create_task(supervisor.aclose())
    await asyncio.sleep(0)
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup

    assert process.returncode is not None


@async_test
async def test_cancelled_restart_retains_child_ownership_until_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "adapter"),
    )
    await supervisor.start()
    process = supervisor.process
    rpc = supervisor.rpc
    assert process is not None
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    original_close = rpc.aclose

    async def blocked_close() -> None:
        close_started.set()
        await allow_close.wait()
        await original_close()

    monkeypatch.setattr(rpc, "aclose", blocked_close)
    restart = asyncio.create_task(supervisor.restart())
    try:
        await close_started.wait()
        restart.cancel()
        await asyncio.sleep(0)
        assert supervisor.process is process
        assert process.returncode is None
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await restart
        assert process.returncode is not None
    finally:
        allow_close.set()
        if not restart.done():
            restart.cancel()
            await asyncio.gather(restart, return_exceptions=True)
        if process.returncode is None:
            await CodexSupervisor._terminate_and_reap(process)
        await supervisor.aclose()


@async_test
async def test_cancelled_internal_cleanup_can_be_retried_without_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "adapter"),
    )
    await supervisor.start()
    process = supervisor.process
    rpc = supervisor.rpc
    assert process is not None
    close_started = asyncio.Event()
    attempts = 0
    original_close = rpc.aclose

    async def first_close_blocks() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            close_started.set()
            await asyncio.Future()
        await original_close()

    monkeypatch.setattr(rpc, "aclose", first_close_blocks)
    close = asyncio.create_task(supervisor.aclose())
    try:
        await close_started.wait()
        internal = next(
            task
            for task in asyncio.all_tasks()
            if task is not close
            and (
                task.get_name() == "codex-supervisor-close"
                or getattr(task.get_coro(), "__qualname__", "").endswith(
                    "CodexSupervisor._close_owned_resources"
                )
            )
        )
        internal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close

        await supervisor.aclose()
        assert process.returncode is not None
    finally:
        if process.returncode is None:
            await CodexSupervisor._terminate_and_reap(process)


def test_global_asyncio_shutdown_does_not_orphan_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.new_event_loop()
    owned: dict[str, Any] = {}

    async def start_and_schedule_close() -> None:
        supervisor = CodexSupervisor(
            codex_home=str(tmp_path),
            executable=sys.executable,
            version_args=(str(FIXTURE), "version"),
            server_args=(str(FIXTURE), "adapter"),
        )
        await supervisor.start()
        process = supervisor.process
        rpc = supervisor.rpc
        assert process is not None
        original_close = rpc.aclose

        async def delayed_close() -> None:
            await asyncio.sleep(0.01)
            await original_close()

        monkeypatch.setattr(rpc, "aclose", delayed_close)
        close_task = asyncio.create_task(supervisor.aclose())
        await asyncio.sleep(0)
        owned.update(supervisor=supervisor, process=process, close_task=close_task)

    try:
        loop.run_until_complete(start_and_schedule_close())
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        process = owned["process"]
        assert process.returncode is not None
    finally:
        process = owned.get("process")
        if process is not None and process.returncode is None:
            loop.run_until_complete(CodexSupervisor._terminate_and_reap(process))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


@async_test
async def test_stderr_diagnostic_never_logs_raw_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "stderr"),
    )
    try:
        rpc = await supervisor.start()
        await rpc.call("trigger", {})
        await asyncio.sleep(0)
    finally:
        await supervisor.aclose()
    assert "secret-auth-token" not in caplog.text
    assert "codex_stderr" in caplog.text


@async_test
async def test_ownership_lock_fd_is_inherited_without_closing_parent_copy(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "adapter"),
        lock_fds=(read_fd,),
    )
    try:
        await supervisor.start()
        os.fstat(read_fd)
    finally:
        await supervisor.aclose()
        os.close(write_fd)
    with pytest.raises(OSError):
        os.fstat(read_fd)
