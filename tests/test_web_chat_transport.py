from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kairos_integration import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionSelectionSnapshot,
    InteractionToolResult,
)
from kairos_providers import (
    CanonicalToolCall,
    ProviderModelRef,
    SelectionReason,
    TokenUsage,
)
from kairos_web import server
from kairos_web.chat_transport import interaction_event_to_json


class FakeInteractionService:
    def __init__(self, events: tuple[InteractionEvent, ...] = ()) -> None:
        self.events = events
        self.envelopes: list[InteractionEnvelope] = []
        self.close_calls = 0

    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        for event in self.events:
            yield event

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.fixture
def protocol_events() -> tuple[InteractionEvent, ...]:
    snapshot = InteractionSelectionSnapshot(
        ref=ProviderModelRef("openai", "gpt-4.1"),
        reason=SelectionReason.MESSAGE_OVERRIDE,
        parameters={"temperature": 0.2},
    )
    return (
        InteractionEvent.turn_start(snapshot, "s1"),
        InteractionEvent(kind="delta", text="olá"),
        InteractionEvent(kind="reasoning_delta", reasoning="penso"),
        InteractionEvent(
            kind="tool_call",
            tool_call=CanonicalToolCall("call-1", "weather", '{"city":"Recife"}'),
        ),
        InteractionEvent.from_tool_result(
            InteractionToolResult("call-1", "28°C", is_error=False), "s1"
        ),
        InteractionEvent(
            kind="usage",
            usage=TokenUsage(
                input_tokens=4,
                output_tokens=3,
                cache_read_tokens=2,
                reasoning_tokens=1,
            ),
        ),
        InteractionEvent(
            kind="turn_error",
            error="limite de requisições atingido",
            error_kind="rate_limit",
            retryable=True,
        ),
        InteractionEvent.turn_end("stop"),
    )


@pytest.fixture
def fake_service(protocol_events: tuple[InteractionEvent, ...]) -> FakeInteractionService:
    return FakeInteractionService(protocol_events)


@pytest.fixture
def auth_client(fake_service: FakeInteractionService):
    state = server.app.state
    previous = getattr(state, "interaction_service", None)
    had_previous = hasattr(state, "interaction_service")
    state.interaction_service = fake_service
    try:
        with TestClient(
            server.app,
            headers={server.TOKEN_HEADER: server.SESSION_TOKEN},
        ) as client:
            yield client
    finally:
        if had_previous:
            state.interaction_service = previous
        elif hasattr(state, "interaction_service"):
            del state.interaction_service


def test_websocket_emite_todo_protocolo_v1_e_preserva_campos_compativeis(
    auth_client: TestClient,
    fake_service: FakeInteractionService,
) -> None:
    with auth_client.websocket_connect("/ws/chat") as ws:
        ws.send_json(
            {
                "type": "message",
                "protocol": 1,
                "session_id": "s1",
                "content": "  oi  ",
                "profile": "main",
                "activity": "chat",
                "provider": "openai",
                "model": "gpt-4.1",
                "parameters": {"temperature": 0.2},
            }
        )
        messages = [ws.receive_json() for _ in range(8)]

    assert messages == [
        {
            "type": "turn_start",
            "protocol": 1,
            "session_id": "s1",
            "provider": "openai",
            "model": "gpt-4.1",
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
            "tool_call": {
                "id": "call-1",
                "name": "weather",
                "arguments": '{"city":"Recife"}',
            },
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "weather",
                    "arguments": '{"city":"Recife"}',
                }
            ],
        },
        {
            "type": "tool_result",
            "protocol": 1,
            "session_id": "s1",
            "tool_result": {
                "tool_call_id": "call-1",
                "content": "28°C",
                "is_error": False,
            },
        },
        {
            "type": "usage",
            "protocol": 1,
            "session_id": "s1",
            "usage": {
                "input_tokens": 4,
                "output_tokens": 3,
                "cache_read_tokens": 2,
                "reasoning_tokens": 1,
                "total_tokens": 8,
            },
        },
        {
            "type": "turn_error",
            "protocol": 1,
            "session_id": "s1",
            "error": "limite de requisições atingido",
            "error_kind": "rate_limit",
            "retryable": True,
        },
        {
            "type": "turn_end",
            "protocol": 1,
            "session_id": "s1",
            "finish_reason": "stop",
        },
    ]
    assert fake_service.envelopes == [
        InteractionEnvelope(
            conversation_id="s1",
            source="web",
            content="oi",
            profile="main",
            activity="chat",
            override=ProviderModelRef("openai", "gpt-4.1"),
            parameters={"temperature": 0.2},
        )
    ]


def test_websocket_reusa_servico_injetado_e_aceita_mensagem_legada_sem_protocol(
    auth_client: TestClient,
    fake_service: FakeInteractionService,
) -> None:
    fake_service.events = (InteractionEvent.turn_end("stop"),)

    with auth_client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "message", "session_id": "s1", "content": "um"})
        assert ws.receive_json()["type"] == "turn_end"
        ws.send_json({"type": "message", "session_id": "s1", "content": "dois"})
        assert ws.receive_json()["type"] == "turn_end"

    assert [envelope.content for envelope in fake_service.envelopes] == ["um", "dois"]
    assert fake_service.close_calls == 0


def test_tradutor_recusa_evento_canonico_sem_payload_obrigatorio() -> None:
    with pytest.raises(ValueError, match="tool_call"):
        interaction_event_to_json(InteractionEvent(kind="tool_call"), conversation_id="s1")


def test_lifecycle_constroi_uma_vez_com_home_fornecido_e_fecha_servico_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeInteractionService()
    built_homes: list[Path] = []

    def build(home: Path) -> FakeInteractionService:
        built_homes.append(home)
        return fake

    state = server.app.state
    monkeypatch.setattr(server, "build_interaction_service", build)
    monkeypatch.setattr(state, "kairos_home", tmp_path, raising=False)
    if hasattr(state, "interaction_service"):
        del state.interaction_service

    with TestClient(server.app):
        assert server.app.state.interaction_service is fake

    assert built_homes == [tmp_path]
    assert fake.close_calls == 1
    assert not hasattr(state, "interaction_service")
