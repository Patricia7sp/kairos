"""Real persistence and multi-round behavior at the provider/search boundaries."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kairos_integration import InteractionEnvelope, InteractionToolResult
from kairos_integration.interaction_service import InteractionService
from kairos_providers import (
    CanonicalToolCall,
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ModelPrice,
    ModelSelectionContext,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    TokenUsage,
)
from kairos_providers.gateway import ProviderBillingMetadata
from kairos_providers.selection import ModelSelectionResolver
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository


class RoundGateway:
    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.requests = []
        self.preparations = 0

    def prepare(self, ref):
        self.preparations += 1
        return SimpleNamespace(
            credential_id="test-key",
            price=ModelPrice(
                prompt=Decimal("0.001"), completion=Decimal("0.002"), request=Decimal("0.01")
            ),
            cost_source="catalog",
            create_adapter=lambda: self,
            billing=ProviderBillingMetadata(ref.provider, "https://example.test", "api_key"),
        )

    async def stream(self, request):
        self.requests.append(request)
        for event in next(self.rounds):
            if isinstance(event, Exception):
                raise event
            yield event


def tool_round(*ids):
    return [
        *(
            ProviderEvent(
                kind="tool_call",
                tool_call=CanonicalToolCall(
                    id=call_id, name="web_search", arguments='{"query":"Kairos"}'
                ),
            )
            for call_id in ids
        ),
        ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=3, output_tokens=2)),
        ProviderEvent(kind="finish", finish_reason="tool_calls"),
    ]


def answer_round():
    return [
        ProviderEvent(kind="text_delta", text="Resposta com fonte."),
        ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=7, output_tokens=4)),
        ProviderEvent(kind="finish", finish_reason="stop"),
    ]


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    yield connection
    connection.close()


def make_service(db, gateway):
    catalog = ModelCatalog()
    catalog.merge(
        [
            CatalogModel(
                ref=ProviderModelRef("openrouter", "test/model"),
                display_name="Test",
                capabilities=ModelCapabilities(chat=True, tools=True),
            )
        ],
        origin=CatalogOrigin.CURATED,
    )
    return InteractionService(
        gateway=gateway,
        resolver=ModelSelectionResolver(catalog),
        context_loader=SimpleNamespace(
            load=lambda _: ModelSelectionContext(
                global_default=ProviderModelRef("openrouter", "test/model")
            )
        ),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=UsageRepository(db),
    )


def turn(**kwargs):
    return InteractionEnvelope(
        conversation_id="search",
        source="web",
        content="Pesquise Kairos",
        parameters={"routing": {"data_collection": "deny"}},
        **kwargs,
    )


async def collect(service, envelope):
    return [event async for event in service.stream(envelope)]


def test_search_round_preserves_policy_persists_results_and_counts_every_call(db, monkeypatch):
    async def scenario():
        search = AsyncMock(return_value=InteractionToolResult("s1", '{"results":["fonte"]}'))
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)
        gateway = RoundGateway([tool_round("s1"), answer_round(), answer_round()])
        events = await collect(make_service(db, gateway), turn(web_search=True))
        assert [e.kind for e in events].count("turn_start") == 1
        assert events[-1].kind == "turn_end"
        assert gateway.preparations == 1
        assert len(gateway.requests) == 2
        assert all(
            r.model == ProviderModelRef("openrouter", "test/model") for r in gateway.requests
        )
        assert all(r.parameters["routing"]["data_collection"] == "deny" for r in gateway.requests)
        assert all(r.tools[0]["function"]["name"] == "web_search" for r in gateway.requests)
        assert gateway.requests[1].messages[-1].role == "tool"
        assert gateway.requests[1].messages[-1].tool_call_id == "s1"
        assert json.loads(gateway.requests[1].messages[-1].content[0].value)["results"] == ["fonte"]
        usage = [e for e in events if e.kind == "usage"][-1]
        assert usage.usage == TokenUsage(input_tokens=10, output_tokens=6)
        assert usage.cost.estimated_usd == pytest.approx(0.042)
        row = db.execute(
            "SELECT api_call_count,input_tokens,output_tokens,estimated_cost_usd FROM session_model_usage"
        ).fetchone()
        assert tuple(row) == pytest.approx((2, 10, 6, 0.042))
        rows = MessageRepository(db).for_api("search")
        assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"]
        await collect(make_service(db, gateway), turn())
        assert gateway.requests[-1].tools == ()
        assert any(
            m.role == "tool" and m.tool_call_id == "s1" for m in gateway.requests[-1].messages
        )
        search.assert_awaited_once()

    asyncio.run(scenario())


def test_disabled_search_never_executes_and_closes_unsolicited_calls(db, monkeypatch):
    async def scenario():
        search = AsyncMock()
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)
        gateway = RoundGateway([tool_round("s1")])
        events = await collect(make_service(db, gateway), turn())
        search.assert_not_awaited()
        assert gateway.requests[0].tools == ()
        assert events[-1].kind == "turn_error"
        assert events[-1].error_kind == "tools_disabled"
        results = [e.tool_result for e in events if e.kind == "tool_result"]
        assert len(results) == 1 and results[0].is_error
        assert MessageRepository(db).for_api("search")[-1]["role"] == "tool"

    asyncio.run(scenario())


def test_round_budget_stops_repeated_searches_with_durable_errors(db, monkeypatch):
    async def scenario():
        async def search(call):
            return InteractionToolResult(call.id, '{"results":[]}')

        mock = AsyncMock(side_effect=search)
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", mock)
        gateway = RoundGateway([tool_round(f"s{i}") for i in range(5)])
        events = await collect(make_service(db, gateway), turn(web_search=True))
        assert mock.await_count == 4
        assert len(gateway.requests) == 5
        assert gateway.requests[-1].tools == ()
        assert events[-1].kind == "turn_error"
        assert events[-1].error_kind == "tool_limit"
        assert [e for e in events if e.kind == "tool_result"][-1].tool_result.is_error
        assert db.execute("SELECT api_call_count FROM session_model_usage").fetchone()[0] == 5

    asyncio.run(scenario())


def test_cancel_search_completes_pending_tool_history_without_followup(db, monkeypatch):
    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def search(call):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)
        gateway = RoundGateway([tool_round("s1", "s2")])
        task = asyncio.create_task(collect(make_service(db, gateway), turn(web_search=True)))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert len(gateway.requests) == 1
        rows = MessageRepository(db).for_api("search")
        assert [r["role"] for r in rows] == ["user", "assistant", "tool", "tool"]
        assert [r["tool_call_id"] for r in rows[-2:]] == ["s1", "s2"]
        assert all("interrompida" in r["payload"] for r in rows[-2:])

    asyncio.run(scenario())


def test_eight_calls_allow_final_answer_but_remove_tool_definitions(db, monkeypatch):
    async def scenario():
        async def search(call):
            return InteractionToolResult(call.id, '{"results":[]}')

        mock = AsyncMock(side_effect=search)
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", mock)
        gateway = RoundGateway([tool_round(*(f"s{i}" for i in range(8))), answer_round()])
        events = await collect(make_service(db, gateway), turn(web_search=True))
        assert mock.await_count == 8
        assert gateway.requests[-1].tools == ()
        assert events[-1].kind == "turn_end"

    asyncio.run(scenario())


def test_generated_call_ids_may_repeat_between_distinct_provider_rounds(db, monkeypatch):
    async def scenario():
        search = AsyncMock(return_value=InteractionToolResult("local-call-1", '{"results":[]}'))
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)
        gateway = RoundGateway(
            [tool_round("local-call-1"), tool_round("local-call-1"), answer_round()]
        )
        events = await collect(make_service(db, gateway), turn(web_search=True))
        assert events[-1].kind == "turn_end"
        assert search.await_count == 2
        assert all(not e.tool_result.is_error for e in events if e.kind == "tool_result")
        assert len(gateway.requests) == 3

    asyncio.run(scenario())


def test_cancel_during_transcript_write_finishes_accounting_once(db, monkeypatch):
    async def scenario():
        gateway = RoundGateway([answer_round()])
        service = make_service(db, gateway)
        persisted, release = asyncio.Event(), asyncio.Event()
        original = service._persist_assistant

        async def blocked_persist(*args, **kwargs):
            await original(*args, **kwargs)
            persisted.set()
            await release.wait()

        monkeypatch.setattr(service, "_persist_assistant", blocked_persist)
        task = asyncio.create_task(collect(service, turn()))
        await asyncio.wait_for(persisted.wait(), 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0] == 1
        usage = db.execute(
            "SELECT api_call_count,input_tokens,output_tokens FROM session_model_usage"
        ).fetchone()
        assert tuple(usage) == (1, 7, 4)

    asyncio.run(scenario())


def test_followup_provider_failure_keeps_first_round_accounting_and_search(db, monkeypatch):
    async def scenario():
        search = AsyncMock(return_value=InteractionToolResult("s1", '{"results":[]}'))
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)
        gateway = RoundGateway(
            [tool_round("s1"), [ProviderError(ProviderErrorKind.AUTH, retryable=False)]]
        )
        events = await collect(make_service(db, gateway), turn(web_search=True))
        assert events[-1].kind == "turn_error" and events[-1].error_kind == "auth"
        usage = [e for e in events if e.kind == "usage"][-1]
        assert usage.usage == TokenUsage(input_tokens=3, output_tokens=2)
        assert usage.cost.estimated_usd == pytest.approx(0.027)
        assert db.execute("SELECT api_call_count FROM session_model_usage").fetchone()[0] == 2
        assert MessageRepository(db).for_api("search")[-2]["role"] == "tool"
        search.assert_awaited_once()

    asyncio.run(scenario())


def test_reopen_after_crash_adds_missing_result_before_new_user_message(db, monkeypatch):
    async def scenario():
        SessionRepository(db).ensure("search", source="web")
        MessageRepository(db).append(
            "search",
            "assistant",
            content="",
            tool_calls=json.dumps(
                [
                    {"id": "orphan", "name": "web_search", "arguments": '{"query":"Kairos"}'},
                ]
            ),
        )
        search = AsyncMock()
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)
        gateway = RoundGateway([answer_round()])
        await collect(make_service(db, gateway), turn())
        messages = gateway.requests[0].messages
        assert [m.role for m in messages] == ["assistant", "tool", "user"]
        assert messages[1].tool_call_id == "orphan"
        assert "interrompida" in messages[1].content[0].value
        search.assert_not_awaited()

    asyncio.run(scenario())


def test_cancel_during_followup_still_counts_started_provider_call(db, monkeypatch):
    async def scenario():
        started = asyncio.Event()

        class BlockingFollowup(RoundGateway):
            async def stream(self, request):
                if self.requests:
                    self.requests.append(request)
                    started.set()
                    await asyncio.Event().wait()
                async for event in super().stream(request):
                    yield event

        search = AsyncMock(return_value=InteractionToolResult("s1", '{"results":[]}'))
        monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)
        gateway = BlockingFollowup([tool_round("s1")])
        task = asyncio.create_task(collect(make_service(db, gateway), turn(web_search=True)))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(gateway.requests) == 2
        assert db.execute("SELECT api_call_count FROM session_model_usage").fetchone()[0] == 2
        row = db.execute(
            "SELECT display_metadata FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        metadata = json.loads(row[0])
        assert metadata["error_kind"] == "cancelled"
        assert metadata["turn_cost"]["estimated_usd"] == pytest.approx(0.027)
        assert metadata["turn_usage"]["total_tokens"] == 5

    asyncio.run(scenario())
