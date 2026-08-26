from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from kairos_integration import InteractionEnvelope, InteractionEvent
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
from kairos_state.repositories import (
    LeaseRepository,
    MessageRepository,
    SessionRepository,
    UsageRepository,
)


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


class SequenceGateway:
    def __init__(self, adapters) -> None:
        self._adapters = iter(adapters)

    def create_adapter(self, _ref):
        return next(self._adapters)


class BlockingAdapter(FakeAdapter):
    def __init__(self, events: list[ProviderEvent]) -> None:
        super().__init__(events)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)

        async def events() -> AsyncIterator[ProviderEvent]:
            self.started.set()
            await self.release.wait()
            for event in self._events:
                yield event

        return events()


class RaisingAdapter(FakeAdapter):
    def __init__(self, error: Exception) -> None:
        super().__init__([])
        self.error = error

    def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)

        async def events() -> AsyncIterator[ProviderEvent]:
            raise self.error
            yield  # pragma: no cover - mantém a assinatura de async generator

        return events()


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


def service_with_adapters(db, adapters) -> InteractionService:
    return InteractionService(
        gateway=SequenceGateway(adapters),
        resolver=CountingResolver(),
        context_loader=FakeContextLoader(),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=UsageRepository(db),
        turn_leases=LeaseRepository.turn_leases(db),
        turn_lease_poll_interval=0,
    )


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

        self.assertEqual(
            [event.kind for event in events], ["turn_start", "delta", "usage", "turn_end"]
        )
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
        self.assertEqual(
            (row["content"], row["finish_reason"], row["display_kind"]),
            ("parcial", "error", "error"),
        )
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
                    tool_calls=(CanonicalToolCall(id="call-1", name="soma", arguments='{"a": 1}'),),
                ),
                CanonicalMessage(
                    role="tool",
                    content=(ContentPart(kind="text", value="2"),),
                    tool_call_id="call-1",
                ),
                CanonicalMessage(
                    role="user", content=(ContentPart(kind="text", value="continue"),)
                ),
            ),
        )

    async def test_turnos_da_mesma_sessao_sao_fifo_e_segundo_ve_assistente_anterior(
        self,
    ) -> None:
        first = BlockingAdapter(
            [
                ProviderEvent(kind="text_delta", text="primeira resposta"),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ]
        )
        second = FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        service = service_with_adapters(self.db, [first, second])

        first_task = asyncio.create_task(_collect(service.stream(envelope("primeira pergunta"))))
        await first.started.wait()
        second_task = asyncio.create_task(_collect(service.stream(envelope("segunda pergunta"))))
        await asyncio.sleep(0)

        self.assertFalse(second_task.done())
        self.assertEqual(
            [(row["role"], row["payload"]) for row in MessageRepository(self.db).for_api("s1")],
            [("user", "primeira pergunta")],
        )
        self.assertIsNotNone(LeaseRepository.turn_leases(self.db).holder("s1"))

        first.release.set()
        await asyncio.gather(first_task, second_task)

        self.assertEqual(
            [(message.role, message.content[0].value) for message in second.requests[0].messages],
            [
                ("user", "primeira pergunta"),
                ("assistant", "primeira resposta"),
                ("user", "segunda pergunta"),
            ],
        )
        self.assertIsNone(LeaseRepository.turn_leases(self.db).holder("s1"))

    async def test_turnos_de_sessoes_diferentes_continuam_concorrentes(self) -> None:
        first = BlockingAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        second = FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        service = service_with_adapters(self.db, [first, second])

        first_task = asyncio.create_task(_collect(service.stream(envelope_for("s1", "um"))))
        await first.started.wait()
        second_events = await asyncio.wait_for(
            _collect(service.stream(envelope_for("s2", "dois"))), timeout=0.5
        )

        self.assertEqual([event.kind for event in second_events], ["turn_start", "turn_end"])
        self.assertFalse(first_task.done())
        first.release.set()
        await first_task

    async def test_lease_e_liberado_apos_provider_error_e_excecao_inesperada(self) -> None:
        service = service_with_adapters(
            self.db,
            [
                FakeAdapter([ProviderError(ProviderErrorKind.AUTH, retryable=False)]),
                RaisingAdapter(RuntimeError("boom")),
                FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")]),
            ],
        )

        first = await _collect(service.stream(envelope("erro provider")))
        self.assertEqual(first[-1].kind, "turn_error")
        self.assertIsNone(LeaseRepository.turn_leases(self.db).holder("s1"))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            await _collect(service.stream(envelope("erro inesperado")))
        self.assertIsNone(LeaseRepository.turn_leases(self.db).holder("s1"))

        final = await _collect(service.stream(envelope("depois")))
        self.assertEqual(final[-1].kind, "turn_end")

    async def test_cancelamento_libera_lease_para_turno_seguinte(self) -> None:
        first = BlockingAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        second = FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        service = service_with_adapters(self.db, [first, second])

        task = asyncio.create_task(_collect(service.stream(envelope("cancelar"))))
        await first.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertIsNone(LeaseRepository.turn_leases(self.db).holder("s1"))
        events = await asyncio.wait_for(_collect(service.stream(envelope("seguinte"))), timeout=0.5)
        self.assertEqual(events[-1].kind, "turn_end")


def envelope_for(conversation_id: str, content: str) -> InteractionEnvelope:
    return InteractionEnvelope(
        conversation_id=conversation_id,
        source="web",
        content=content,
        parameters={"temperature": 0.2},
    )


async def _collect(stream: AsyncIterator[InteractionEvent]) -> list[InteractionEvent]:
    return [event async for event in stream]
