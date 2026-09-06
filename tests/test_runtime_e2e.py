from __future__ import annotations

import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import CancelledError
from pathlib import Path

from fastapi.testclient import TestClient

from kairos_runtime.host import serve_runtime
from kairos_web import server

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "tests" / "fixtures" / "codex_app_server.py"


def _host_process(home: str, audit: str) -> None:
    import asyncio

    os.environ["KAIROS_FAKE_AUDIT"] = audit

    async def run() -> None:
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopped.set)
        task = asyncio.create_task(serve_runtime(Path(home)))
        try:
            await stopped.wait()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def _stop(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(10)
    if process.is_alive():
        process.kill()
        process.join(5)
    assert not process.is_alive()


def _start(home: Path, audit: Path) -> multiprocessing.Process:
    process = multiprocessing.get_context("spawn").Process(
        target=_host_process, args=(str(home), str(audit))
    )
    process.start()
    socket = home / "run" / "runtime.sock"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not socket.exists() and process.is_alive():
        time.sleep(0.02)
    assert process.is_alive() and socket.exists()
    return process


def _cli(home: Path, *args: str) -> dict:
    environment = {**os.environ, "KAIROS_HOME": str(home)}
    result = subprocess.run(
        [str(REPO / ".venv" / "bin" / "kairos"), *args, "--json"],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _events_until_end(web: TestClient, session_id: str, cursor: str | None = None) -> list[dict]:
    events: list[dict] = []
    context = web.websocket_connect("/ws/runtime")
    websocket = context.__enter__()
    terminal = False
    try:
        subscription = {"type": "subscribe", "session_id": session_id}
        if cursor is not None:
            subscription["cursor"] = cursor
        websocket.send_json(subscription)
        for _ in range(20):
            event = _receive_json(websocket)
            events.append(event)
            if event["kind"] == "turn_end":
                terminal = True
                break
    finally:
        try:
            context.__exit__(None, None, None)
        except CancelledError:
            # The forwarding side can finish at the same time as TestClient's
            # synthetic disconnect; both owned tasks have already been drained.
            pass
    if not terminal:
        raise AssertionError("journal não chegou a turn_end em 20 eventos")
    return events


def _receive_json(websocket, timeout: float = 5.0) -> dict:
    """Bound TestClient's otherwise-unbounded synchronous receive on Linux."""
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        raise TimeoutError(f"WebSocket sem evento após {timeout}s")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return websocket.receive_json()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _approval_event(web: TestClient, session_id: str, cursor: str | None = None) -> dict:
    context = web.websocket_connect("/ws/runtime")
    websocket = context.__enter__()
    approval = None
    try:
        subscription = {"type": "subscribe", "session_id": session_id}
        if cursor is not None:
            subscription["cursor"] = cursor
        websocket.send_json(subscription)
        for _ in range(20):
            event = _receive_json(websocket)
            if event["kind"] == "approval_request":
                approval = event
                break
    finally:
        try:
            context.__exit__(None, None, None)
        except CancelledError:
            pass
    assert approval is not None
    return approval


def test_fake_web_cli_restart_preserva_journal_sem_duplicar_turn_start(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    audit = tmp_path / "audit.json"
    executable = tmp_path / "codex-fake"
    executable.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then exec "{sys.executable}" "{FAKE}" version; fi\n'
        f'exec "{sys.executable}" "{FAKE}" persistent-e2e\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    (home / "config.yaml").write_text(
        "agent_runtime:\n"
        "  enabled: true\n"
        f"  codex_binary: {executable}\n"
        f"  allowed_directories: [{project}]\n"
        "  broad_access_enabled: true\n",
        encoding="utf-8",
    )

    process = _start(home, audit)
    state = server.app.state
    monkeypatch.setattr(state, "kairos_home", home, raising=False)
    from kairos_runtime import RuntimeClient

    runtime_client = RuntimeClient(home / "run" / "runtime.sock")
    monkeypatch.setattr(state, "runtime_client", runtime_client, raising=False)
    try:
        with TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}) as web:
            created = web.post(
                "/api/runtime/sessions",
                json={"cwd": str(project), "sandbox": "broad_access", "consent": True},
            ).json()
            session_id = created["session_id"]
            accepted = web.post(
                f"/api/runtime/sessions/{session_id}/turns",
                json={"content": "primeiro", "idempotency_key": "first"},
            )
            assert accepted.status_code == 202

            approval = _approval_event(web, session_id)
            decided = _cli(
                home,
                "runtime",
                "approve",
                "--session",
                session_id,
                "--approval",
                approval["payload"]["approval_id"],
                "--decision",
                "accept",
            )
            assert decided["status"] == "decided"
            tail = _events_until_end(web, session_id, approval["cursor"])
            assert tail[-1]["payload"]["state"] == "completed"
            first_journal = _events_until_end(web, session_id)

        _stop(process)
        process = _start(home, audit)
        runtime_client = RuntimeClient(home / "run" / "runtime.sock")
        monkeypatch.setattr(state, "runtime_client", runtime_client, raising=False)
        with TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}) as web:
            assert _events_until_end(web, session_id) == first_journal
            assert json.loads(audit.read_text(encoding="utf-8"))["turn_start"] == 1

            second = web.post(
                f"/api/runtime/sessions/{session_id}/turns",
                json={"content": "segundo", "idempotency_key": "second"},
            )
            assert second.status_code == 202
            approval = _approval_event(web, session_id, first_journal[-1]["cursor"])
            _cli(
                home,
                "runtime",
                "approve",
                "--session",
                session_id,
                "--approval",
                approval["payload"]["approval_id"],
                "--decision",
                "accept",
            )
            _events_until_end(web, session_id, approval["cursor"])
            counts = json.loads(audit.read_text(encoding="utf-8"))
            assert counts == {"thread_id": "thread-e2e", "thread_start": 1, "turn_start": 2}
    finally:
        _stop(process)
