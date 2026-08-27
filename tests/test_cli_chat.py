from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairos_cli import chat
from kairos_cli.handlers import ExitCode, cmd_run
from kairos_cli.main import main
from kairos_integration import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionSelectionSnapshot,
    InteractionToolResult,
)
from kairos_integration.interaction_contract import InteractionServiceUnavailableError
from kairos_providers import (
    CanonicalToolCall,
    ProviderModelRef,
    SelectionReason,
    TokenUsage,
)


class FakeInteractionService:
    def __init__(
        self,
        events: tuple[InteractionEvent, ...] = (),
        *,
        stream_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.stream_error = stream_error
        self.envelopes: list[InteractionEnvelope] = []
        self.close_calls = 0
        self.stream_started = asyncio.Event()

    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        self.stream_started.set()
        if self.stream_error is not None:
            raise self.stream_error
        for event in self.events:
            yield event

    async def aclose(self) -> None:
        self.close_calls += 1


class BlockingInteractionService(FakeInteractionService):
    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        self.stream_started.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover - mantém a assinatura de async generator


def delta(text: str) -> InteractionEvent:
    return InteractionEvent(kind="delta", text=text)


def turn_end() -> InteractionEvent:
    return InteractionEvent.turn_end("stop")


def install_service(monkeypatch, tmp_path: Path, fake: FakeInteractionService) -> list[Path]:
    homes: list[Path] = []
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))

    def build(home: Path) -> FakeInteractionService:
        homes.append(home)
        return fake

    monkeypatch.setattr(chat, "build_interaction_service", build)
    return homes


def test_cli_chat_uses_public_service_and_human_stdout(monkeypatch, tmp_path, capsys):
    fake = FakeInteractionService((delta("olá"), turn_end()))
    homes = install_service(monkeypatch, tmp_path, fake)

    code = main(["chat", "--session", "s1", "oi"])

    captured = capsys.readouterr()
    assert code == ExitCode.OK
    assert captured.out == "olá\n"
    assert captured.err == ""
    assert homes == [tmp_path]
    assert fake.close_calls == 1
    assert fake.envelopes == [InteractionEnvelope(conversation_id="s1", source="cli", content="oi")]


def test_cli_maps_only_complete_provider_model_override(monkeypatch, tmp_path, capsys):
    fake = FakeInteractionService((turn_end(),))
    install_service(monkeypatch, tmp_path, fake)

    code = main(
        [
            "chat",
            "--session",
            "s1",
            "--provider",
            "openrouter",
            "--model",
            "acme/chat",
            "oi",
        ]
    )

    assert code == ExitCode.OK
    assert fake.envelopes[0].override == ProviderModelRef("openrouter", "acme/chat")
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--provider", "openrouter"), ("--model", "acme/chat")],
)
def test_cli_rejects_partial_override_before_composition(
    monkeypatch, tmp_path, capsys, flag: str, value: str
):
    fake = FakeInteractionService((turn_end(),))
    homes = install_service(monkeypatch, tmp_path, fake)

    code = main(["chat", "--session", "s1", flag, value, "oi"])

    captured = capsys.readouterr()
    assert code == ExitCode.USAGE
    assert captured.out == ""
    assert "--provider e --model" in captured.err
    assert homes == []
    assert fake.close_calls == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["chat", "--session", "s1", "--json", "oi"],
        ["--json", "chat", "--session", "s1", "oi"],
    ],
    ids=["chat-local", "root-before-command"],
)
def test_cli_json_is_versioned_canonical_ndjson_without_credential_metadata(
    monkeypatch, tmp_path, capsys, argv
):
    snapshot = InteractionSelectionSnapshot(
        ref=ProviderModelRef("openrouter", "acme/chat"),
        reason=SelectionReason.MESSAGE_OVERRIDE,
        parameters={"temperature": 0.2},
        credential_id="vault-primary-secret",
    )
    events = (
        InteractionEvent.turn_start(snapshot, "s1"),
        delta("olá"),
        InteractionEvent(kind="reasoning_delta", reasoning="penso"),
        InteractionEvent(
            kind="tool_call",
            tool_call=CanonicalToolCall(id="c1", name="tempo", arguments='{"cidade":"SP"}'),
        ),
        InteractionEvent.from_tool_result(
            InteractionToolResult(tool_call_id="c1", content="sol", is_error=False)
        ),
        InteractionEvent(kind="usage", usage=TokenUsage(input_tokens=2, output_tokens=3)),
        turn_end(),
    )
    fake = FakeInteractionService(events)
    install_service(monkeypatch, tmp_path, fake)

    code = main(argv)

    captured = capsys.readouterr()
    payloads = [json.loads(line) for line in captured.out.splitlines()]
    assert code == ExitCode.OK
    assert payloads == [
        {
            "type": "turn_start",
            "protocol": 1,
            "session_id": "s1",
            "provider": "openrouter",
            "model": "acme/chat",
            "selection_reason": "message_override",
            "parameters": {"temperature": 0.2},
        },
        {"type": "delta", "protocol": 1, "session_id": "s1", "text": "olá"},
        {
            "type": "reasoning_delta",
            "protocol": 1,
            "session_id": "s1",
            "text": "penso",
            "reasoning": "penso",
        },
        {
            "type": "tool_call",
            "protocol": 1,
            "session_id": "s1",
            "tool_call": {"id": "c1", "name": "tempo", "arguments": '{"cidade":"SP"}'},
            "tool_calls": [{"id": "c1", "name": "tempo", "arguments": '{"cidade":"SP"}'}],
        },
        {
            "type": "tool_result",
            "protocol": 1,
            "session_id": "s1",
            "tool_result": {"tool_call_id": "c1", "content": "sol", "is_error": False},
        },
        {
            "type": "usage",
            "protocol": 1,
            "session_id": "s1",
            "cost": {
                "actual_usd": None,
                "estimated_usd": None,
                "source": None,
                "status": "unknown",
            },
            "usage": {
                "input_tokens": 2,
                "output_tokens": 3,
                "cache_read_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 5,
            },
        },
        {"type": "turn_end", "protocol": 1, "session_id": "s1", "finish_reason": "stop"},
    ]
    assert all(payload["protocol"] == 1 for payload in payloads)
    assert "credential" not in captured.out
    assert "vault-primary-secret" not in captured.out
    assert captured.err == ""


def test_cli_human_turn_error_goes_to_stderr(monkeypatch, tmp_path, capsys):
    fake = FakeInteractionService(
        (
            InteractionEvent(
                kind="turn_error",
                error="não foi possível persistir a contabilidade do turno",
                error_kind="persistence",
                retryable=True,
            ),
        )
    )
    install_service(monkeypatch, tmp_path, fake)

    code = main(["chat", "--session", "s1", "oi"])

    captured = capsys.readouterr()
    assert code == ExitCode.ERROR
    assert captured.out == ""
    assert captured.err == "não foi possível persistir a contabilidade do turno\n"
    assert fake.close_calls == 1


def test_cli_json_turn_error_stays_in_event_stream(monkeypatch, tmp_path, capsys):
    fake = FakeInteractionService(
        (
            InteractionEvent(
                kind="turn_error",
                error="não foi possível persistir a contabilidade do turno",
                error_kind="persistence",
                retryable=True,
            ),
        )
    )
    install_service(monkeypatch, tmp_path, fake)

    code = main(["chat", "--session", "s1", "--json", "oi"])

    captured = capsys.readouterr()
    assert code == ExitCode.ERROR
    assert json.loads(captured.out) == {
        "type": "turn_error",
        "protocol": 1,
        "session_id": "s1",
        "error": "não foi possível persistir a contabilidade do turno",
        "error_kind": "persistence",
        "retryable": True,
    }
    assert captured.err == ""


def test_cli_closes_owned_service_when_stream_raises(monkeypatch, tmp_path, capsys):
    fake = FakeInteractionService(stream_error=RuntimeError("provider indisponível"))
    install_service(monkeypatch, tmp_path, fake)

    code = main(["chat", "--session", "s1", "oi"])

    captured = capsys.readouterr()
    assert code == ExitCode.ERROR
    assert captured.out == ""
    assert "provider indisponível" in captured.err
    assert fake.close_calls == 1


def test_cli_prints_normalized_unavailable_error(monkeypatch, tmp_path, capsys):
    """Falling through the global handler would expose the exception type/prefix."""
    fake = FakeInteractionService(stream_error=InteractionServiceUnavailableError())
    install_service(monkeypatch, tmp_path, fake)

    cli_result = main(["chat", "--session", "s1", "oi"])

    captured = capsys.readouterr()
    assert cli_result == 1
    assert captured.out == ""
    assert captured.err == "serviço de interação indisponível\n"
    assert fake.close_calls == 1


@pytest.mark.anyio
async def test_cli_closes_owned_service_when_caller_cancels(monkeypatch, tmp_path):
    fake = BlockingInteractionService()
    install_service(monkeypatch, tmp_path, fake)
    task = asyncio.create_task(
        chat.run_chat(
            home=tmp_path,
            session_id="s1",
            prompt="oi",
            provider=None,
            model=None,
            as_json=False,
        )
    )
    await fake.stream_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake.close_calls == 1


def test_interactive_chat_composes_once_for_multiple_turns(monkeypatch, tmp_path, capsys):
    fake = FakeInteractionService((delta("ok"), turn_end()))
    homes = install_service(monkeypatch, tmp_path, fake)
    answers = iter(["primeiro", "segundo", "sair"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = main(["chat", "--session", "s1", "--quiet"])

    assert code == ExitCode.OK
    assert homes == [tmp_path]
    assert [envelope.content for envelope in fake.envelopes] == ["primeiro", "segundo"]
    assert fake.close_calls == 1
    assert capsys.readouterr().out == "ok\nok\n"


def test_run_alias_uses_same_interaction_service(monkeypatch, tmp_path, capsys):
    fake = FakeInteractionService((delta("ok"), turn_end()))
    homes = install_service(monkeypatch, tmp_path, fake)

    code = main(["run", "oi"])

    assert code == ExitCode.OK
    assert homes == [tmp_path]
    assert fake.envelopes[0].conversation_id == "cli-default"
    assert fake.close_calls == 1
    assert capsys.readouterr().out == "ok\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["chat", "oi"],
        ["chat", "--session", "", "oi"],
        ["chat", "--session", "   ", "oi"],
    ],
    ids=["missing", "empty", "blank"],
)
def test_cli_chat_requires_nonblank_session_before_composition(monkeypatch, tmp_path, argv):
    fake = FakeInteractionService((turn_end(),))
    homes = install_service(monkeypatch, tmp_path, fake)

    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == ExitCode.USAGE
    assert homes == []
    assert fake.close_calls == 0


@pytest.mark.parametrize("session_id", ["", "   "])
def test_chat_handler_defends_nonblank_session_before_composition(
    monkeypatch, tmp_path, capsys, session_id: str
):
    fake = FakeInteractionService((turn_end(),))
    homes = install_service(monkeypatch, tmp_path, fake)
    args = SimpleNamespace(
        command="chat",
        prompt=["oi"],
        quiet=False,
        session=session_id,
        provider=None,
        model=None,
        json=False,
    )

    code = cmd_run(args)

    assert code == ExitCode.USAGE
    assert "--session" in capsys.readouterr().err
    assert homes == []
    assert fake.close_calls == 0
