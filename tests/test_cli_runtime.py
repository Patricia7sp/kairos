from __future__ import annotations

import asyncio
import json
from typing import ClassVar

import pytest

from kairos_cli import chat, runtime
from kairos_cli.handlers import ExitCode
from kairos_cli.main import build_parser, main
from kairos_runtime import RuntimeErrorInfo, RuntimeEvent


class FakeClient:
    instances: ClassVar[list] = []

    def __init__(self, socket):
        self.socket = socket
        self.calls = []
        self.__class__.instances.append(self)

    async def status(self):
        return {"enabled": True, "state": "ready"}

    async def account_login(self, mode, api_key=None):
        self.calls.append(("login", mode, api_key))
        return {"mode": mode, "state": "succeeded"}

    async def create(self, cwd, sandbox, **kwargs):
        self.calls.append(("create", cwd, sandbox, kwargs))
        return {"session_id": "s1", "sandbox_profile": sandbox}

    async def subscribe(self, session_id, cursor=None):
        self.calls.append(("watch", session_id, cursor))
        yield RuntimeEvent(
            1,
            "e1",
            session_id,
            "t1",
            1,
            f"v1:{session_id}:1",
            "approval_request",
            {"approval_id": "a1"},
        )

    async def aclose(self):
        self.calls.append(("close",))


def test_runtime_parser_has_explicit_three_level_session_branch():
    parser = build_parser()
    create = parser.parse_args(
        ["runtime", "session", "create", "--cwd", "/work", "--sandbox", "read_only"]
    )
    end = parser.parse_args(["runtime", "session", "end", "--session", "s1"])
    assert (create.command, create.runtime_command, create.runtime_session_command) == (
        "runtime",
        "session",
        "create",
    )
    assert end.runtime_session_command == "end"


def test_runtime_api_key_comes_only_from_hidden_prompt(monkeypatch, tmp_path, capsys):
    FakeClient.instances.clear()
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "RuntimeClient", FakeClient)
    monkeypatch.setattr(runtime.getpass, "getpass", lambda _prompt: "hidden-secret")
    code = main(["runtime", "login", "--method", "apiKey", "--json"])
    assert code == ExitCode.OK
    assert FakeClient.instances[0].calls[0] == ("login", "apiKey", "hidden-secret")
    assert "hidden-secret" not in capsys.readouterr().out


def test_runtime_serve_reports_only_public_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))

    async def unavailable(_home):
        raise RuntimeErrorInfo("unavailable", "private startup detail", True)

    monkeypatch.setattr(runtime, "serve_runtime", unavailable)
    assert main(["runtime", "serve"]) == ExitCode.ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "runtime indisponível\n"


def test_runtime_watch_emits_canonical_ndjson_and_never_decides(monkeypatch, tmp_path, capsys):
    FakeClient.instances.clear()
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "RuntimeClient", FakeClient)
    code = main(["runtime", "watch", "--session", "s1", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == ExitCode.OK
    assert payload["execution_kind"] == "agent_runtime"
    assert payload["event_id"] == "e1"
    assert all(call[0] != "decide" for call in FakeClient.instances[0].calls)


class CancellationClient:
    def __init__(self):
        self.cancelled = []

    async def cancel(self, session_id, turn_id):
        self.cancelled.append((session_id, turn_id))

    async def subscribe(self, session_id, cursor=None):
        yield RuntimeEvent(
            1,
            "end",
            session_id,
            "turn-1",
            2,
            f"v1:{session_id}:2",
            "turn_end",
            {"state": "cancelled"},
        )


class InterruptibleService:
    def __init__(self):
        self.runtime_client = CancellationClient()
        self.waiting = asyncio.Event()

    async def stream(self, _envelope):
        yield RuntimeEvent(
            1,
            "start",
            "s1",
            "turn-1",
            1,
            "v1:s1:1",
            "turn_state",
            {"state": "running"},
        )
        self.waiting.set()
        await asyncio.Event().wait()


class AcceptedBeforeEventService:
    def __init__(self):
        self.runtime_client = CancellationClient()
        self.waiting = asyncio.Event()

    async def stream(self, _envelope, *, on_runtime_accepted=None):
        if on_runtime_accepted is not None:
            on_runtime_accepted("turn-1")
        self.waiting.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover - mantém a assinatura de async generator


@pytest.mark.anyio
async def test_runtime_chat_cancellation_sends_explicit_cancel_and_watches_confirmation(capsys):
    service = InterruptibleService()
    task = asyncio.create_task(
        chat._run_turn(
            service,
            session_id="s1",
            content="execute",
            override=None,
            as_json=True,
            idempotency_key="durable",
        )
    )
    await service.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.runtime_client.cancelled == [("s1", "turn-1")]
    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [payload["event_id"] for payload in payloads] == ["start", "end"]


@pytest.mark.anyio
async def test_runtime_chat_cancels_after_acceptance_before_first_event(capsys):
    service = AcceptedBeforeEventService()
    task = asyncio.create_task(
        chat._run_turn(
            service,
            session_id="s1",
            content="execute",
            override=None,
            as_json=True,
            idempotency_key="durable",
            runtime_session=True,
        )
    )
    await service.waiting.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.runtime_client.cancelled == [("s1", "turn-1")]
    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [payload["event_id"] for payload in payloads] == ["end"]


@pytest.mark.anyio
async def test_runtime_interactive_eof_exits_without_cancelling(monkeypatch):
    service = InterruptibleService()
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(EOFError))
    result = await chat._run_interactive(
        service,
        session_id="s1",
        override=None,
        as_json=True,
        quiet=True,
        runtime_session=True,
    )
    assert result == 0
    assert service.runtime_client.cancelled == []
