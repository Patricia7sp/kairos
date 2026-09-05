from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

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
async def test_restart_uses_exponential_backoff_and_stops_after_five_attempts(
    tmp_path: Path,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
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
async def test_stderr_diagnostic_never_logs_raw_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
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
