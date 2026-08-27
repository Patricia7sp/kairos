from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

from kairos_integration import InteractionEnvelope, InteractionEvent
from kairos_integration.interaction_service import InteractionService
from kairos_integration.selection_context import SelectionContextLoader
from kairos_integration.turn_ownership import SQLiteAsyncTurnLeaseBackend
from kairos_providers import (
    CanonicalMessage,
    CanonicalToolCall,
    CatalogModel,
    CatalogOrigin,
    ContentPart,
    ModelCapabilities,
    ModelPrice,
    ModelSelectionContext,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
    TokenUsage,
)
from kairos_providers.catalog import ModelCatalog
from kairos_providers.gateway import ProviderBillingMetadata
from kairos_providers.selection import ModelSelectionResolver
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


class CostPreparedGateway:
    def __init__(self, adapter: FakeAdapter) -> None:
        self._adapter = adapter

    def prepare(self, ref: ProviderModelRef):
        adapter = self._adapter

        class Prepared:
            credential_id = None
            billing = ProviderBillingMetadata("custom", "https://models.example.test/v1", "api_key")
            price = ModelPrice(
                prompt=Decimal("0.001"),
                completion=Decimal("0.002"),
                request=Decimal("0.01"),
            )
            cost_source = "catalog"
            pricing_version = "snapshot-1"

            def create_adapter(self):
                return adapter

        return Prepared()


class OpenRouterCostPreparedGateway(CostPreparedGateway):
    def prepare(self, ref: ProviderModelRef):
        adapter = self._adapter

        class Prepared:
            credential_id = None
            billing = ProviderBillingMetadata(
                "openrouter", "https://openrouter.ai/api/v1", "api_key"
            )
            price = ModelPrice(
                prompt=Decimal("0.000001"),
                completion=Decimal("0.000002"),
                request=Decimal("0"),
            )
            cost_source = "catalog"
            pricing_version = "snapshot-2"

            def create_adapter(self):
                return adapter

        return Prepared()


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


def service_with_failing_usage_flush(
    db, events: list[ProviderEvent | ProviderError]
) -> tuple[InteractionService, UsageRepository]:
    """Create a real service whose terminal accounting flush fails safely."""
    service, _resolver, _adapter, usage = service_with_fake_adapter(db, events)

    def failing_flush() -> int:
        raise RuntimeError("database credential detail")

    usage.flush = failing_flush
    return service, usage


def service_with_adapters(db, adapters) -> InteractionService:
    db_path = Path(db.execute("PRAGMA database_list").fetchone()["file"])
    return InteractionService(
        gateway=SequenceGateway(adapters),
        resolver=CountingResolver(),
        context_loader=FakeContextLoader(),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=UsageRepository(db),
        turn_leases=SQLiteAsyncTurnLeaseBackend(db_path),
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
        self.assertEqual(usage.pending_count(), 0)

    async def test_snapshot_mescla_parametros_sem_tornar_override_de_mensagem_sticky(
        self,
    ) -> None:
        """Um override efêmero não pode substituir a seleção explícita da conversa."""
        conversation_ref = ProviderModelRef("fake", "conversation")
        message_ref = ProviderModelRef("fake", "message")
        catalog = ModelCatalog()
        catalog.merge(
            [
                CatalogModel(
                    ref=conversation_ref,
                    display_name="Conversation",
                    capabilities=ModelCapabilities(chat=True),
                ),
                CatalogModel(
                    ref=message_ref,
                    display_name="Message",
                    capabilities=ModelCapabilities(chat=True),
                ),
            ],
            origin=CatalogOrigin.CURATED,
        )
        self.db.execute("UPDATE sessions SET model = NULL WHERE id = 's1'")
        sessions = SessionRepository(self.db)
        sessions.ensure("s1", source="web")
        sessions.set_selection(
            "s1",
            conversation_ref,
            {"seed": 7, "routing": {"conversation": True, "shared": "conversation"}},
        )
        loader = SelectionContextLoader(
            sessions,
            profile_configs={
                "work": {
                    "provider": "fake",
                    "model": "conversation",
                    "parameters": {
                        "max_tokens": 128,
                        "routing": {"profile": True, "shared": "profile"},
                    },
                }
            },
            global_config={
                "provider": "fake",
                "model": "conversation",
                "parameters": {
                    "temperature": 0.1,
                    "routing": {"global": True, "shared": "global"},
                },
            },
        )
        adapter = FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        service = InteractionService(
            gateway=FakeGateway(adapter),
            resolver=ModelSelectionResolver(catalog),
            context_loader=loader,
            sessions=sessions,
            messages=MessageRepository(self.db),
            usage=UsageRepository(self.db),
        )
        turn = InteractionEnvelope(
            conversation_id="s1",
            source="web",
            content="temporário",
            profile="work",
            override=message_ref,
            parameters={"temperature": 0.9, "routing": {"message": True}},
        )

        events = [event async for event in service.stream(turn)]

        self.assertEqual(events[0].snapshot.ref, message_ref)
        self.assertEqual(
            dict(events[0].snapshot.parameters),
            {
                "max_tokens": 128,
                "seed": 7,
                "temperature": 0.9,
                "routing": {
                    "global": True,
                    "profile": True,
                    "conversation": True,
                    "message": True,
                    "shared": "conversation",
                },
            },
        )
        persisted = sessions.selection("s1")
        assert persisted is not None
        self.assertEqual(persisted.ref, conversation_ref)
        self.assertEqual(
            persisted.parameters,
            {"seed": 7, "routing": {"conversation": True, "shared": "conversation"}},
        )
        metadata = self.db.execute(
            "SELECT display_metadata FROM messages WHERE session_id = 's1' AND role = 'user'"
        ).fetchone()[0]
        self.assertIn('"model": "message"', metadata)

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
        self.assertEqual(usage.pending_count(), 0)

    async def test_flush_failure_replaces_normal_terminal_with_persistence_error(self) -> None:
        """Engolir o flush deixaria o cliente aceitar um turno sem uso durável."""
        service, usage = service_with_failing_usage_flush(
            self.db,
            [
                ProviderEvent(kind="text_delta", text="ok"),
                ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=2, output_tokens=1)),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ],
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "delta", "turn_error"])
        self.assertEqual(
            events[-1].error,
            "não foi possível persistir a contabilidade do turno",
        )
        self.assertEqual(events[-1].error_kind, "persistence")
        self.assertTrue(events[-1].retryable)
        self.assertEqual(usage.pending_count(), 1)

    async def test_flush_failure_takes_terminal_precedence_over_provider_error(self) -> None:
        """Expor o erro do provider esconderia que a contabilidade não foi durável."""
        service, usage = service_with_failing_usage_flush(
            self.db,
            [
                ProviderEvent(kind="text_delta", text="partial"),
                ProviderError(ProviderErrorKind.NETWORK, retryable=True),
            ],
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "delta", "turn_error"])
        self.assertEqual(events[-1].error_kind, "persistence")
        self.assertNotIn("database", events[-1].error.lower())
        self.assertEqual(usage.pending_count(), 1)

    async def test_turn_boundary_persiste_rotas_e_custos_conhecidos_antes_do_close(self) -> None:
        """Uso concluído deve ficar consultável sem esperar o lifecycle shutdown."""
        adapter = FakeAdapter(
            [
                ProviderEvent(
                    kind="usage",
                    usage=TokenUsage(
                        input_tokens=3,
                        output_tokens=2,
                        cache_read_tokens=7,
                        reasoning_tokens=11,
                    ),
                ),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ]
        )
        usage = UsageRepository(self.db)
        service = InteractionService(
            gateway=CostPreparedGateway(adapter),
            resolver=CountingResolver(),
            context_loader=FakeContextLoader(),
            sessions=SessionRepository(self.db),
            messages=MessageRepository(self.db),
            usage=usage,
        )

        events = [event async for event in service.stream(envelope())]

        usage_event = next(event for event in events if event.kind == "usage")
        self.assertEqual(usage_event.cost.status, "estimated")
        self.assertAlmostEqual(usage_event.cost.estimated_usd, 0.017)
        self.assertIsNone(usage_event.cost.actual_usd)
        self.assertEqual(usage.pending_count(), 0)
        row = self.db.execute(
            "SELECT billing_provider, billing_base_url, billing_mode, estimated_cost_usd, "
            "actual_cost_usd, cost_status, cost_source FROM session_model_usage"
        ).fetchone()
        self.assertEqual(tuple(row[:3]), ("custom", "https://models.example.test/v1", "api_key"))
        self.assertAlmostEqual(row["estimated_cost_usd"], 0.017)
        self.assertIsNone(row["actual_cost_usd"])
        self.assertEqual((row["cost_status"], row["cost_source"]), ("estimated", "catalog"))

        router_service = InteractionService(
            gateway=OpenRouterCostPreparedGateway(
                FakeAdapter(
                    [
                        ProviderEvent(
                            kind="usage", usage=TokenUsage(input_tokens=3, output_tokens=2)
                        ),
                        ProviderEvent(kind="finish", finish_reason="stop"),
                    ]
                )
            ),
            resolver=CountingResolver(),
            context_loader=FakeContextLoader(),
            sessions=SessionRepository(self.db),
            messages=MessageRepository(self.db),
            usage=usage,
        )
        router_events = [event async for event in router_service.stream(envelope("router"))]
        router_usage = next(event for event in router_events if event.kind == "usage")
        self.assertAlmostEqual(router_usage.cost.estimated_usd, 0.000007)
        rows = self.db.execute(
            "SELECT billing_provider, billing_base_url, estimated_cost_usd "
            "FROM session_model_usage ORDER BY billing_provider"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0:2], ("openrouter", "https://openrouter.ai/api/v1"))
        self.assertAlmostEqual(rows[1][2], 0.000007)

    async def test_request_only_cost_emite_usage_sem_tokens_no_terminal_de_sucesso(self) -> None:
        """O custo conhecido por requisição não pode ficar apenas no banco."""
        usage = UsageRepository(self.db)
        service = InteractionService(
            gateway=CostPreparedGateway(
                FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
            ),
            resolver=CountingResolver(),
            context_loader=FakeContextLoader(),
            sessions=SessionRepository(self.db),
            messages=MessageRepository(self.db),
            usage=usage,
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "usage", "turn_end"])
        self.assertIsNone(events[1].usage)
        self.assertEqual(events[1].cost.status, "estimated")
        self.assertEqual(events[1].cost.estimated_usd, 0.01)
        row = self.db.execute(
            "SELECT api_call_count, input_tokens, output_tokens, estimated_cost_usd "
            "FROM session_model_usage WHERE session_id = 's1'"
        ).fetchone()
        self.assertEqual(tuple(row), (1, 0, 0, 0.01))

    async def test_request_only_cost_emite_usage_sem_tokens_antes_do_erro_do_provider(self) -> None:
        """O terminal de erro também deve expor o custo durável da tentativa."""
        service = InteractionService(
            gateway=CostPreparedGateway(
                FakeAdapter([ProviderError(ProviderErrorKind.AUTH, retryable=False)])
            ),
            resolver=CountingResolver(),
            context_loader=FakeContextLoader(),
            sessions=SessionRepository(self.db),
            messages=MessageRepository(self.db),
            usage=UsageRepository(self.db),
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "usage", "turn_error"])
        self.assertIsNone(events[1].usage)
        self.assertEqual(events[1].cost.estimated_usd, 0.01)
        self.assertEqual(events[-1].error_kind, "auth")

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
