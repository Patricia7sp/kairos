from __future__ import annotations

import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_service import InteractionService
from kairos_providers import (
    CanonicalMessage,
    CanonicalToolCall,
    ContentPart,
    ModelSelectionContext,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
    TokenUsage,
)
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository


def envelope(content: str = "oi") -> InteractionEnvelope:
    return InteractionEnvelope(
        conversation_id="s1",
        source="web",
        content=content,
        parameters={"temperature": 0.2},
    )


class CountingResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, context: ModelSelectionContext) -> ResolvedModelSelection:
        self.calls += 1
        assert context.global_default == ProviderModelRef("fake", "chat-1")
        return ResolvedModelSelection(
            ref=ProviderModelRef("fake", "chat-1"),
            reason=SelectionReason.GLOBAL_DEFAULT,
        )


class FakeContextLoader:
    def load(self, _envelope: InteractionEnvelope) -> ModelSelectionContext:
        return ModelSelectionContext(global_default=ProviderModelRef("fake", "chat-1"))


class FakeAdapter:
    def __init__(self, events: list[ProviderEvent | ProviderError]) -> None:
        self._events = events
        self.requests = []

    def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)

        async def events() -> AsyncIterator[ProviderEvent]:
            for event in self._events:
                if isinstance(event, ProviderError):
                    raise event
                yield event

        return events()


class FakeGateway:
    def __init__(self, adapter: FakeAdapter) -> None:
        self._adapter = adapter
        self.refs: list[ProviderModelRef] = []

    def create_adapter(self, ref: ProviderModelRef) -> FakeAdapter:
        self.refs.append(ref)
        return self._adapter


def service_with_fake_adapter(
    db, events: list[ProviderEvent | ProviderError]
) -> tuple[InteractionService, CountingResolver, FakeAdapter, UsageRepository]:
    resolver = CountingResolver()
    adapter = FakeAdapter(events)
    usage = UsageRepository(db)
    service = InteractionService(
        gateway=FakeGateway(adapter),
        resolver=resolver,
        context_loader=FakeContextLoader(),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=usage,
    )
    return service, resolver, adapter, usage


class InteractionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self._tmp.name) / "state.db")
        initialize_schema(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    async def test_turno_resolve_uma_vez_streama_e_persiste(self) -> None:
        """Resolver de novo ou não persistir o turno quebraria a próxima execução."""
        service, resolver, adapter, usage = service_with_fake_adapter(
            self.db,
            events=[
                ProviderEvent(kind="text_delta", text="olá"),
                ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=3, output_tokens=2)),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ],
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "delta", "usage", "turn_end"])
        self.assertEqual(resolver.calls, 1)
        self.assertEqual(
            [row["role"] for row in MessageRepository(self.db).for_api("s1")],
            ["user", "assistant"],
        )
        self.assertEqual(adapter.requests[0].model, ProviderModelRef("fake", "chat-1"))
        self.assertEqual(
            adapter.requests[0].messages,
            (CanonicalMessage(role="user", content=(ContentPart(kind="text", value="oi"),)),),
        )
        self.assertEqual(adapter.requests[0].parameters, {"temperature": 0.2})
        assistant = self.db.execute(
            "SELECT content, finish_reason FROM messages WHERE session_id = ? AND role = 'assistant'",
            ("s1",),
        ).fetchone()
        self.assertEqual((assistant["content"], assistant["finish_reason"]), ("olá", "stop"))
        self.assertEqual(usage.pending_count(), 1)

    async def test_erro_persiste_resposta_parcial_e_nao_fecha_turno(self) -> None:
        """Propagar erro apagaria a resposta parcial e deixaria o cliente sem evento final."""
        service, _resolver, _adapter, usage = service_with_fake_adapter(
            self.db,
            events=[
                ProviderEvent(kind="text_delta", text="parcial"),
                ProviderError(ProviderErrorKind.NETWORK, retryable=True),
            ],
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "delta", "turn_error"])
        row = self.db.execute(
            "SELECT content, finish_reason, display_kind, display_metadata "
            "FROM messages WHERE session_id = ? AND role = 'assistant'",
            ("s1",),
        ).fetchone()
        self.assertEqual((row["content"], row["finish_reason"], row["display_kind"]), ("parcial", "error", "error"))
        self.assertIn('"error_kind": "network"', row["display_metadata"])
        self.assertEqual(usage.pending_count(), 1)

    async def test_proximo_turno_reidrata_tool_calls_e_resultado_vinculado(self) -> None:
        """Descartar IDs de tools faria o próximo provider rejeitar o histórico do turno."""
        first_service, _resolver, _adapter, _usage = service_with_fake_adapter(
            self.db,
            events=[
                ProviderEvent(
                    kind="tool_call",
                    tool_call=CanonicalToolCall(id="call-1", name="soma", arguments='{"a": 1}'),
                ),
                ProviderEvent(kind="finish", finish_reason="tool_calls"),
            ],
        )
        _ = [event async for event in first_service.stream(envelope())]
        MessageRepository(self.db).append(
            "s1",
            "tool",
            content="2",
            api_content="2",
            tool_call_id="call-1",
        )
        second_service, _resolver, adapter, _usage = service_with_fake_adapter(
            self.db,
            events=[ProviderEvent(kind="finish", finish_reason="stop")],
        )

        _ = [event async for event in second_service.stream(envelope("continue"))]

        self.assertEqual(
            adapter.requests[0].messages,
            (
                CanonicalMessage(role="user", content=(ContentPart(kind="text", value="oi"),)),
                CanonicalMessage(
                    role="assistant",
                    content=(ContentPart(kind="text", value=""),),
                    tool_calls=(
                        CanonicalToolCall(id="call-1", name="soma", arguments='{"a": 1}'),
                    ),
                ),
                CanonicalMessage(
                    role="tool",
                    content=(ContentPart(kind="text", value="2"),),
                    tool_call_id="call-1",
                ),
                CanonicalMessage(role="user", content=(ContentPart(kind="text", value="continue"),)),
            ),
        )
