"""Real turns/jobs produce only approved operational counters, never content."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_chat_search_loop import RoundGateway, answer_round, collect, make_service, tool_round

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler
from kairos_integration import InteractionEnvelope, InteractionEvent, InteractionToolResult
from kairos_observability.service_events import read_service_events
from kairos_providers import ProviderError, ProviderErrorKind
from kairos_state import connect, initialize_schema


@pytest.mark.parametrize("search_failed", [False, True])
def test_search_loop_records_model_and_search_outcomes_without_content(
    tmp_path, monkeypatch, search_failed
):
    async def scenario():
        connection = connect(tmp_path / "state.db")
        initialize_schema(connection)
        try:
            service = make_service(
                connection, RoundGateway([tool_round("s1"), answer_round()]), event_home=tmp_path
            )

            async def search(_call):
                return InteractionToolResult(
                    "s1", '{"results":[{"snippet":"private-result"}]}', is_error=search_failed
                )

            monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", search)
            envelope = InteractionEnvelope(
                conversation_id="conv-001",
                source="web",
                content="private-prompt",
                web_search=True,
            )
            events = await collect(service, envelope)
            assert events[-1].kind == "turn_end"
            journal = read_service_events(tmp_path)
            assert [e["code"] for e in reversed(journal["events"])] == [
                "chat.completed",
                "search.failed" if search_failed else "search.completed",
                "chat.completed",
            ]
            assert sum(e["counters"].get("api_calls", 0) for e in journal["events"]) == 2
            assert sum(e["counters"].get("input_tokens", 0) for e in journal["events"]) == 10
            raw = (tmp_path / "logs" / "service-events.jsonl").read_text()
            assert "private" not in raw
            assert "test/model" not in raw
            assert "private" not in json.dumps(journal)
        finally:
            connection.close()

    asyncio.run(scenario())


def test_journal_write_failure_does_not_change_completed_turn(tmp_path, monkeypatch):
    async def scenario():
        connection = connect(tmp_path / "state.db")
        initialize_schema(connection)
        try:
            service = make_service(connection, RoundGateway([answer_round()]), event_home=tmp_path)
            monkeypatch.setattr(
                "kairos_observability.service_events._append",
                lambda *_: (_ for _ in ()).throw(OSError("secret-disk-detail")),
            )
            events = await collect(
                service, InteractionEnvelope(conversation_id="s1", source="web", content="oi")
            )
            assert events[-1].kind == "turn_end"
            assert (
                connection.execute("SELECT api_call_count FROM session_model_usage").fetchone()[0]
                == 1
            )
        finally:
            connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("success", [True, False])
def test_scheduler_records_real_job_outcomes_without_prompt(tmp_path, success):
    class Service:
        async def stream(self, _envelope):
            yield (
                InteractionEvent.turn_end("stop")
                if success
                else InteractionEvent.turn_error(RuntimeError("private-failure"))
            )

    async def scenario():
        store = JobStore(tmp_path)
        store.create(
            name="private-name",
            prompt="private-prompt",
            schedule={
                "kind": "once",
                "run_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            },
        )
        report = await Scheduler(tmp_path, Service()).tick()
        assert report["executed"] == 1 and report["failed"] == (0 if success else 1)
        journal = read_service_events(tmp_path)
        assert [e["code"] for e in journal["events"]] == [
            "cron.completed" if success else "cron.failed"
        ]
        assert "private" not in (tmp_path / "logs" / "service-events.jsonl").read_text()

    asyncio.run(scenario())


def test_provider_failure_produces_safe_failed_event(tmp_path):
    async def scenario():
        connection = connect(tmp_path / "state.db")
        initialize_schema(connection)
        try:
            gateway = RoundGateway(
                [[ProviderError(ProviderErrorKind.AUTH, "private-upstream-error", retryable=False)]]
            )
            service = make_service(connection, gateway, event_home=tmp_path)
            events = await collect(
                service,
                InteractionEnvelope(conversation_id="s1", source="web", content="private-prompt"),
            )
            assert events[-1].kind == "turn_error"
            journal = read_service_events(tmp_path)
            assert journal["events"][0]["code"] == "chat.failed"
            assert journal["events"][0]["counters"] == {"api_calls": 1}
            assert "private" not in json.dumps(journal)
        finally:
            connection.close()

    asyncio.run(scenario())


def test_cancelled_provider_produces_safe_cancelled_event(tmp_path):
    async def scenario():
        started = asyncio.Event()

        class BlockingGateway(RoundGateway):
            async def stream(self, request):
                started.set()
                await asyncio.Event().wait()
                yield  # pragma: no cover

        connection = connect(tmp_path / "state.db")
        initialize_schema(connection)
        try:
            service = make_service(connection, BlockingGateway([]), event_home=tmp_path)
            task = asyncio.create_task(
                collect(
                    service,
                    InteractionEnvelope(
                        conversation_id="s1", source="web", content="private-prompt"
                    ),
                )
            )
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            journal = read_service_events(tmp_path)
            assert journal["events"][0]["code"] == "chat.cancelled"
            assert journal["events"][0]["counters"] == {"api_calls": 1}
            assert "private" not in json.dumps(journal)
        finally:
            connection.close()

    asyncio.run(scenario())


def test_unexpected_provider_failure_is_not_reported_as_cancellation(tmp_path):
    async def scenario():
        connection = connect(tmp_path / "state.db")
        initialize_schema(connection)
        try:
            service = make_service(
                connection,
                RoundGateway([[RuntimeError("private-internal-failure")]]),
                event_home=tmp_path,
            )
            with pytest.raises(RuntimeError, match="private-internal-failure"):
                await collect(
                    service,
                    InteractionEnvelope(conversation_id="s1", source="web", content="private"),
                )
            journal = read_service_events(tmp_path)
            assert [event["code"] for event in journal["events"]] == ["chat.failed"]
            assert journal["events"][0]["counters"] == {"api_calls": 1}
            assert "private" not in json.dumps(journal)
        finally:
            connection.close()

    asyncio.run(scenario())
