from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kairos_integration import (
    InteractionCost,
    InteractionEnvelope,
    InteractionEvent,
    InteractionSelectionSnapshot,
    InteractionToolResult,
)
from kairos_integration.interaction_contract import InteractionServiceUnavailableError
from kairos_integration.turn_ownership import TurnLeaseLostError
from kairos_providers import (
    CanonicalToolCall,
    CatalogOrigin,
    ModelCatalog,
    ProviderEvent,
    ProviderModelRef,
    SelectionReason,
    TokenUsage,
    curated_models,
)
from kairos_runtime import RuntimeErrorInfo, RuntimeEvent, public_error, runtime_event_to_json
from kairos_web import server
from kairos_web.chat_transport import (
    interaction_envelope_from_json,
    interaction_event_to_json,
)


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_search_option_rejects_non_boolean(value):
    with pytest.raises(TypeError, match="web_search"):
        interaction_envelope_from_json({"content": "oi", "web_search": value})


def test_search_option_is_explicit_and_separate_from_provider_parameters():
    envelope = interaction_envelope_from_json({"content": "pesquise", "web_search": True})
    assert envelope.web_search is True
    assert "web_search" not in envelope.parameters
    assert interaction_envelope_from_json({"content": "oi"}).web_search is False


class FakeInteractionService:
    def __init__(self, events: tuple[InteractionEvent | RuntimeEvent, ...] = ()) -> None:
        self.events = events
        self.envelopes: list[InteractionEnvelope] = []
        self.close_calls = 0

    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        for event in self.events:
            yield event

    async def aclose(self) -> None:
        self.close_calls += 1


class FailingInteractionService(FakeInteractionService):
    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        raise RuntimeError("segredo-interno")
        yield  # pragma: no cover - mantém a assinatura de async generator


class UnavailableInteractionService(FakeInteractionService):
    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        raise InteractionServiceUnavailableError() from RuntimeError("database-internal")
        yield  # pragma: no cover - mantém a assinatura de async generator


class RuntimeUnavailableInteractionService(FakeInteractionService):
    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        raise RuntimeErrorInfo("unavailable", "private-runtime-sentinel", True)
        yield  # pragma: no cover - mantém a assinatura de async generator


class CleanupInteractionService(FakeInteractionService):
    def __init__(self) -> None:
        super().__init__()
        self.stream_closed = threading.Event()

    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        try:
            yield InteractionEvent.turn_end("first")
            await asyncio.sleep(0.05)
            yield InteractionEvent.turn_end("after-client-close")
        finally:
            self.stream_closed.set()


class BoundaryLeaseLossService(FakeInteractionService):
    def __init__(self) -> None:
        super().__init__()
        self.lease_lost = asyncio.Event()
        self.stream_closed = asyncio.Event()

    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        self.envelopes.append(envelope)
        queue: asyncio.Queue[BaseException] = asyncio.Queue()

        async def signal_loss() -> None:
            await self.lease_lost.wait()
            await queue.put(TurnLeaseLostError("detalhe-interno"))

        producer = asyncio.create_task(signal_loss())
        try:
            yield InteractionEvent.turn_end("first")
            raise await queue.get()
        finally:
            producer.cancel()
            self.stream_closed.set()


class BlockingSendWebSocket:
    def __init__(self, service: FakeInteractionService) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(interaction_service=service),
        )
        self.send_started = asyncio.Event()
        self.allow_send = asyncio.Event()
        self.closed_code: int | None = None
        self.closed_reason = ""
        self.close_code: int | None = None
        self.close_reason = ""
        self.accepted = False
        self._received = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if not self._received:
            self._received = True
            return '{"type":"message","protocol":1,"content":"oi"}'
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send_text(self, _payload: str) -> None:
        self.send_started.set()
        await self.allow_send.wait()

    async def close(self, code: int, reason: str = "") -> None:
        self.closed_code = code
        self.closed_reason = reason
        self.close_code = code
        self.close_reason = reason


class PersistingAdapter:
    async def stream(self, _request):
        yield ProviderEvent(kind="text_delta", text="resposta")
        yield ProviderEvent(kind="finish", finish_reason="stop")


class PersistingGateway:
    def __init__(self) -> None:
        self.catalog = ModelCatalog()
        self.catalog.merge(curated_models(), origin=CatalogOrigin.CURATED)
        self.closed = False

    def create_adapter(self, _ref):
        return PersistingAdapter()

    async def aclose(self) -> None:
        self.closed = True


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
                cache_write_tokens=1,
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
                "cache_write_tokens": 1,
                "total_tokens": 7,
            },
            "cost": {
                "estimated_usd": None,
                "actual_usd": None,
                "status": "unknown",
                "source": None,
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


def test_web_protocol_serializa_custo_sem_inventar_uso_de_tokens() -> None:
    event = InteractionEvent(
        kind="usage",
        usage=None,
        cost=InteractionCost(
            estimated_usd=0.01,
            status="estimated",
            source="catalog:test",
        ),
    )

    assert interaction_event_to_json(event, conversation_id="s1") == {
        "type": "usage",
        "protocol": 1,
        "session_id": "s1",
        "usage": None,
        "cost": {
            "estimated_usd": 0.01,
            "actual_usd": None,
            "status": "estimated",
            "source": "catalog:test",
        },
    }


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


def test_websocket_emite_erro_de_persistencia_seguro(auth_client: TestClient, fake_service) -> None:
    """O transporte deve manter o erro de durabilidade canônico, sem detalhes internos."""
    fake_service.events = (
        InteractionEvent(
            kind="turn_error",
            error="não foi possível persistir a contabilidade do turno",
            error_kind="persistence",
            retryable=True,
        ),
    )

    with auth_client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "message", "protocol": 1, "session_id": "s1", "content": "oi"})
        assert ws.receive_json() == {
            "type": "turn_error",
            "protocol": 1,
            "session_id": "s1",
            "error": "não foi possível persistir a contabilidade do turno",
            "error_kind": "persistence",
            "retryable": True,
        }


@pytest.mark.parametrize("frame", [[], "texto", 7, None])
def test_websocket_ignora_json_que_nao_e_objeto_sem_encerrar_conexao(
    auth_client: TestClient,
    fake_service: FakeInteractionService,
    frame: object,
) -> None:
    with auth_client.websocket_connect("/ws/chat") as ws:
        ws.send_json(frame)
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert fake_service.envelopes == []


def test_websocket_chat_serializes_runtime_events_with_canonical_wire(
    auth_client: TestClient,
    fake_service: FakeInteractionService,
) -> None:
    event = RuntimeEvent(
        1,
        "runtime-event-1",
        "runtime-session",
        "runtime-turn",
        1,
        "v1:runtime-session:1",
        "text",
        {"delta": "olá do runtime"},
    )
    fake_service.events = (event,)

    with auth_client.websocket_connect("/ws/chat") as ws:
        ws.send_json(
            {
                "type": "message",
                "protocol": 1,
                "session_id": "runtime-session",
                "content": "execute",
                "idempotency_key": "durable-key",
            }
        )
        assert ws.receive_json() == runtime_event_to_json(event)


def test_websocket_chat_redacts_runtime_errors() -> None:
    from starlette.websockets import WebSocketDisconnect

    state = server.app.state
    previous = getattr(state, "interaction_service", None)
    had_previous = hasattr(state, "interaction_service")
    state.interaction_service = RuntimeUnavailableInteractionService()
    try:
        with (
            TestClient(
                server.app,
                headers={server.TOKEN_HEADER: server.SESSION_TOKEN},
            ) as client,
            client.websocket_connect("/ws/chat") as ws,
        ):
            ws.send_json({"type": "message", "protocol": 1, "content": "execute"})
            payload = ws.receive_json()
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    finally:
        if had_previous:
            state.interaction_service = previous
        elif hasattr(state, "interaction_service"):
            del state.interaction_service

    assert payload == {"error": public_error("unavailable")}
    assert closed.value.code == 1012
    assert "private-runtime-sentinel" not in str(payload)
    assert "private-runtime-sentinel" not in str(closed.value)


def test_websocket_fecha_1011_sem_expor_excecao_inesperada() -> None:
    from starlette.websockets import WebSocketDisconnect

    state = server.app.state
    state.interaction_service = FailingInteractionService()
    try:
        with (
            TestClient(
                server.app,
                headers={server.TOKEN_HEADER: server.SESSION_TOKEN},
            ) as client,
            client.websocket_connect("/ws/chat") as ws,
        ):
            ws.send_json({"type": "message", "protocol": 1, "content": "oi"})
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_json()
    finally:
        del state.interaction_service

    assert caught.value.code == 1011
    assert "segredo-interno" not in str(caught.value)


def test_websocket_fecha_stream_quando_cliente_desconecta() -> None:
    state = server.app.state
    fake = CleanupInteractionService()
    state.interaction_service = fake
    try:
        with (
            TestClient(
                server.app,
                headers={server.TOKEN_HEADER: server.SESSION_TOKEN},
            ) as client,
            client.websocket_connect("/ws/chat") as ws,
        ):
            ws.send_json({"type": "message", "protocol": 1, "content": "oi"})
            assert ws.receive_json()["finish_reason"] == "first"
    finally:
        del state.interaction_service

    assert fake.stream_closed.wait(timeout=1)


@pytest.mark.anyio
async def test_lease_loss_durante_send_bloqueado_fecha_1011_e_desfaz_stream() -> None:
    service = BoundaryLeaseLossService()
    websocket = BlockingSendWebSocket(service)
    handler = asyncio.create_task(server._chat_session(websocket))

    await websocket.send_started.wait()
    service.lease_lost.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not handler.done()
    websocket.allow_send.set()
    await handler

    assert websocket.accepted
    assert websocket.closed_code == 1011
    assert service.stream_closed.is_set()


@pytest.mark.anyio
async def test_websocket_fecha_1012_sem_expor_indisponibilidade_interna() -> None:
    """Mapping unavailable as 1011 or including its cause leaks an operational failure."""
    websocket = BlockingSendWebSocket(UnavailableInteractionService())

    await server._chat_session(websocket)

    assert websocket.close_code == 1012
    assert "database" not in websocket.close_reason.lower()


def test_tradutor_recusa_evento_canonico_sem_payload_obrigatorio() -> None:
    with pytest.raises(ValueError, match="tool_call"):
        interaction_event_to_json(InteractionEvent(kind="tool_call"), conversation_id="s1")


@pytest.mark.parametrize("protocol", [True, 1.0])
def test_tradutor_exige_protocol_inteiro_real(protocol: object) -> None:
    with pytest.raises(ValueError, match="protocolo"):
        interaction_envelope_from_json({"type": "message", "protocol": protocol, "content": "oi"})


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


def test_websocket_persiste_no_mesmo_home_que_rest_le(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kairos_integration import composition

    app_home = tmp_path / "app-home"
    environment_home = tmp_path / "environment-home"
    app_home.mkdir()
    environment_home.mkdir()
    (app_home / "config.yaml").write_text(
        "provider: openai\nmodel: gpt-4o\n",
        encoding="utf-8",
    )
    gateway = PersistingGateway()
    state = server.app.state
    monkeypatch.setenv("KAIROS_HOME", str(environment_home))
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    monkeypatch.setattr(state, "kairos_home", app_home, raising=False)
    if hasattr(state, "interaction_service"):
        del state.interaction_service

    with TestClient(
        server.app,
        headers={server.TOKEN_HEADER: server.SESSION_TOKEN},
    ) as client:
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "message", "protocol": 1, "session_id": "s1", "content": "oi"})
            assert [ws.receive_json()["type"] for _ in range(3)] == [
                "turn_start",
                "delta",
                "turn_end",
            ]

        assert client.get("/api/sessions/s1").status_code == 200
        response = client.get("/api/sessions/s1/messages")
        assert response.status_code == 200
        assert [(item["role"], item["content"]) for item in response.json()["messages"]] == [
            ("user", "oi"),
            ("assistant", "resposta"),
        ]

    assert (app_home / "state.db").exists()
    assert not (environment_home / "state.db").exists()
    assert gateway.closed
