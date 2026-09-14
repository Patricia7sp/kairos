"""Fluxo de aprovação por turno das ferramentas mutadoras do Chat (WS-2)."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from test_chat_search_loop import RoundGateway, answer_round, collect, make_service, turn

from kairos_integration import InteractionToolResult
from kairos_providers import (
    CanonicalToolCall,
    ProviderEvent,
    TokenUsage,
)


def mutator_round(*calls):
    return [
        *(ProviderEvent(kind="tool_call", tool_call=call) for call in calls),
        ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=3, output_tokens=2)),
        ProviderEvent(kind="finish", finish_reason="tool_calls"),
    ]


def bash_call(call_id, arguments='{"command":"echo oi"}'):
    return CanonicalToolCall(id=call_id, name="bash", arguments=arguments)


@pytest.fixture
def db(tmp_path):
    from kairos_state import connect, initialize_schema

    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    yield connection
    connection.close()


async def wait_for_kind(events, kind, *, timeout=2):
    async def waiter():
        while not any(event.kind == kind for event in events):
            await asyncio.sleep(0.01)
        return next(event for event in events if event.kind == kind)

    return await asyncio.wait_for(waiter(), timeout)


def test_approved_mutator_executes_and_continues(db, monkeypatch):
    executed = AsyncMock(return_value=InteractionToolResult("m1", '{"status":"ok"}'))
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", executed)
    gateway = RoundGateway([mutator_round(bash_call("m1")), answer_round()])
    service = make_service(db, gateway)
    events = []

    async def scenario():
        driver = asyncio.create_task(collect_with(events, service, turn(tools=True)))
        approval = await wait_for_kind(events, "tool_approval_request")
        service.decide_tool_approval(
            approval_id=approval.tool_approval_id, session_id="search", decision="allow"
        )
        await asyncio.wait_for(driver, 5)

    asyncio.run(scenario())
    assert events[-1].kind == "turn_end"
    assert executed.await_count == 1
    results = [e.tool_result for e in events if e.kind == "tool_result"]
    assert len(results) == 1 and not results[0].is_error
    approvals = [e for e in events if e.kind == "tool_approval_request"]
    assert approvals[0].tool_call.id == "m1"
    assert approvals[0].tool_approval_id


async def collect_with(events, service, envelope):
    appended = []
    async for event in service.stream(envelope):
        events.append(event)
        appended.append(event)
    return appended


def test_denied_mutator_never_executes(db, monkeypatch):
    executed = AsyncMock()
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", executed)
    gateway = RoundGateway([mutator_round(bash_call("m1")), answer_round()])
    service = make_service(db, gateway)
    events = []

    async def scenario():
        driver = asyncio.create_task(collect_with(events, service, turn(tools=True)))
        approval = await wait_for_kind(events, "tool_approval_request")
        service.decide_tool_approval(
            approval_id=approval.tool_approval_id, session_id="search", decision="deny"
        )
        await asyncio.wait_for(driver, 5)

    asyncio.run(scenario())
    assert events[-1].kind == "turn_end"
    executed.assert_not_awaited()
    results = [e.tool_result for e in events if e.kind == "tool_result"]
    assert len(results) == 1 and results[0].is_error
    assert json.loads(results[0].content)["status"] == "denied"


def test_approval_timeout_denies_by_fail_closed(db, monkeypatch):
    monkeypatch.setattr(
        "kairos_integration.interaction_service.TOOL_APPROVAL_TIMEOUT_SECONDS", 0.05
    )
    executed = AsyncMock()
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", executed)
    gateway = RoundGateway([mutator_round(bash_call("m1")), answer_round()])
    events = []

    async def scenario():
        driver = asyncio.create_task(
            collect_with(events, make_service(db, gateway), turn(tools=True))
        )
        await asyncio.wait_for(driver, 5)

    asyncio.run(scenario())
    assert events[-1].kind == "turn_end"
    executed.assert_not_awaited()
    results = [e.tool_result for e in events if e.kind == "tool_result"]
    assert len(results) == 1 and results[0].is_error
    assert json.loads(results[0].content)["status"] == "denied"


def test_late_decision_after_timeout_is_not_found(db, monkeypatch):
    monkeypatch.setattr(
        "kairos_integration.interaction_service.TOOL_APPROVAL_TIMEOUT_SECONDS", 0.05
    )
    executed = AsyncMock()
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", executed)
    gateway = RoundGateway([mutator_round(bash_call("m1")), answer_round()])
    service = make_service(db, gateway)
    events = []

    async def scenario():
        driver = asyncio.create_task(collect_with(events, service, turn(tools=True)))
        approval = await wait_for_kind(events, "tool_approval_request")
        await asyncio.wait_for(driver, 5)
        with pytest.raises(Exception) as exc:
            service.decide_tool_approval(
                approval_id=approval.tool_approval_id, session_id="search", decision="allow"
            )
        assert exc.value.error_kind == "approval_not_found"

    asyncio.run(scenario())


def test_decision_for_other_conversation_is_rejected(db, monkeypatch):
    executed = AsyncMock(return_value=InteractionToolResult("m1", '{"status":"ok"}'))
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", executed)
    gateway = RoundGateway([mutator_round(bash_call("m1")), answer_round()])
    service = make_service(db, gateway)
    events = []

    async def scenario():
        driver = asyncio.create_task(collect_with(events, service, turn(tools=True)))
        approval = await wait_for_kind(events, "tool_approval_request")
        with pytest.raises(Exception) as exc:
            service.decide_tool_approval(
                approval_id=approval.tool_approval_id, session_id="other", decision="allow"
            )
        assert exc.value.error_kind == "approval_session_mismatch"
        service.decide_tool_approval(
            approval_id=approval.tool_approval_id, session_id="search", decision="allow"
        )
        await asyncio.wait_for(driver, 5)

    asyncio.run(scenario())
    assert executed.await_count == 1
    assert events[-1].kind == "turn_end"


def test_second_decision_is_a_conflict(db, monkeypatch):
    executed = AsyncMock(return_value=InteractionToolResult("m1", '{"status":"ok"}'))
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", executed)
    gateway = RoundGateway([mutator_round(bash_call("m1")), answer_round()])
    service = make_service(db, gateway)
    events = []

    async def scenario():
        driver = asyncio.create_task(collect_with(events, service, turn(tools=True)))
        approval = await wait_for_kind(events, "tool_approval_request")
        service.decide_tool_approval(
            approval_id=approval.tool_approval_id, session_id="search", decision="allow"
        )
        with pytest.raises(Exception) as exc:
            service.decide_tool_approval(
                approval_id=approval.tool_approval_id, session_id="search", decision="allow"
            )
        assert exc.value.error_kind == "approval_already_decided"
        await asyncio.wait_for(driver, 5)

    asyncio.run(scenario())


def test_tools_turn_exposes_chat_definitions_and_web_search_needs_flag(db, monkeypatch):
    gateway = RoundGateway([answer_round()])
    collected = collect(make_service(db, gateway), turn(tools=True))
    events = asyncio.run(collected)
    assert events[-1].kind == "turn_end"
    tools = {t["function"]["name"] for t in gateway.requests[0].tools}
    assert "web_search" not in tools
    assert tools == {
        "bash",
        "write_file",
        "edit_file",
        "patch",
        "read_file",
        "list_dir",
        "search_files",
        "web_extract",
    }


def test_legacy_web_search_turn_only_exposes_web_search(db, monkeypatch):
    gateway = RoundGateway([answer_round()])
    asyncio.run(collect(make_service(db, gateway), turn(web_search=True)))
    tools = {t["function"]["name"] for t in gateway.requests[0].tools}
    assert tools == {"web_search"}


def test_no_flags_means_no_tools(db, monkeypatch):
    gateway = RoundGateway([answer_round()])
    asyncio.run(collect(make_service(db, gateway), turn()))
    assert gateway.requests[0].tools == ()


def test_unsolicited_mutator_never_triggers_approval_or_execution(db, monkeypatch):
    executed = AsyncMock()
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", executed)
    gateway = RoundGateway([mutator_round(bash_call("m1")), answer_round()])
    events = []

    async def scenario():
        await asyncio.wait_for(
            asyncio.create_task(
                collect_with(events, make_service(db, gateway), turn(web_search=True))
            ),
            5,
        )

    asyncio.run(scenario())
    assert events[-1].kind == "turn_end"
    executed.assert_not_awaited()
    assert not any(e.kind == "tool_approval_request" for e in events)
    results = [e.tool_result for e in events if e.kind == "tool_result"]
    assert len(results) == 1 and results[0].is_error
    assert "não habilitada" in json.loads(results[0].content)["error"]
