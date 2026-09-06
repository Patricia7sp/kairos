from __future__ import annotations

import asyncio
import copy
import sqlite3

import pytest
from test_codex_adapter import async_test
from test_runtime_service import setup_service

from kairos_runtime.errors import RuntimeErrorInfo
from kairos_state import connect


async def pending_approval(service, runtime):
    await service.submit("s1", "hello", "key")
    await runtime.events.put(
        {
            "kind": "approval",
            "payload": {
                "request_id": "process-1:string:7",
                "rpc_request_id": "7",
                "item_id": "item-1",
                "request_kind": "item/commandExecution/requestApproval",
                "details": {
                    "threadId": "thread-s1",
                    "turnId": "external-1",
                    "itemId": "item-1",
                    "startedAtMs": 1,
                },
            },
        }
    )
    async with asyncio.timeout(3):
        async for event in service.subscribe("s1"):
            if event.kind == "approval_request":
                return event.payload["approval_id"]


@async_test
async def test_restarted_generation_cannot_receive_stale_approval(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)
        runtime.generation = "process-2"
        with pytest.raises(RuntimeErrorInfo, match="approval_stale"):
            await service.decide("s1", approval_id, "decline")
        assert runtime.replies == []
        assert (await store.get_approval(approval_id))["decision"] is None
    finally:
        await service.aclose()


@async_test
async def test_restricted_accept_is_rejected_before_delivery_and_audited(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)
        with pytest.raises(RuntimeErrorInfo, match="invalid_policy"):
            await service.decide("s1", approval_id, "accept")
        assert runtime.replies == []
        assert (await store.get_approval(approval_id))["decision"] is None
        events = await store.events_after("s1", None)
        rejected = [event for event in events if event.kind == "approval_rejected"]
        assert rejected[-1].payload == {
            "approval_id": approval_id,
            "requested_decision": "accept",
            "effective_decision": None,
            "code": "invalid_policy",
        }
        await service.decide("s1", approval_id, "decline")
        assert runtime.replies == [("process-1:string:7", "decline")]
    finally:
        await service.aclose()


@async_test
async def test_reply_failure_keeps_decision_undelivered_and_never_resends(tmp_path, monkeypatch):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)

        async def lost_reply(request_id, decision):
            assert (await store.get_approval(approval_id))["decision"] == "decline"
            runtime.replies.append((request_id, decision))
            raise RuntimeErrorInfo("transport", "reply uncertain", True)

        monkeypatch.setattr(runtime, "respond_approval", lost_reply)
        with pytest.raises(RuntimeErrorInfo, match="transport"):
            await service.decide("s1", approval_id, "decline")
        await service.decide("s1", approval_id, "decline")
        assert len(runtime.replies) == 1
        assert (await store.get_approval(approval_id))["delivery_state"] == "decided"
    finally:
        await service.aclose()


@async_test
async def test_approval_is_durable_session_scoped_and_decline_is_idempotent(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        await service.submit("s1", "hello", "key")
        await runtime.events.put(
            {
                "kind": "approval",
                "payload": {
                    "request_id": "process-1:integer:7",
                    "rpc_request_id": 7,
                    "item_id": "item-1",
                    "request_kind": "item/commandExecution/requestApproval",
                    "details": {
                        "threadId": "thread-s1",
                        "turnId": "external-1",
                        "itemId": "item-1",
                        "startedAtMs": 1,
                    },
                },
            }
        )
        async with asyncio.timeout(3):
            async for event in service.subscribe("s1"):
                if event.kind == "approval_request":
                    approval_id = event.payload["approval_id"]
                    break
        row = await store.get_approval(approval_id)
        assert row["decision"] is None
        assert row["request"]["rpc_request_id"] == 7
        with pytest.raises(RuntimeErrorInfo, match="approval_stale"):
            await service.decide("another", approval_id, "decline")
        await service.decide("s1", approval_id, "decline")
        await service.decide("s1", approval_id, "decline")
        with pytest.raises(RuntimeErrorInfo, match="approval_stale"):
            await service.decide("s1", approval_id, "accept")
        assert runtime.replies == [("process-1:integer:7", "decline")]
        assert (await store.get_approval(approval_id))["delivery_state"] == "delivered"
        assert (await service.get("s1"))["state"] == "running"
    finally:
        await service.aclose()


@pytest.mark.parametrize("reuse_directory", [False, True])
@async_test
async def test_successful_reply_ack_survives_terminal_release(
    tmp_path, monkeypatch, reuse_directory
):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)
        approval = await store.get_approval(approval_id)
        turn_id = approval["turn_id"]

        async def reply_then_finish(request_id, decision):
            runtime.replies.append((request_id, decision))
            runner = service._tasks[turn_id]
            await runtime.events.put({"kind": "turn", "payload": {"state": "completed"}})
            await asyncio.wait_for(asyncio.shield(runner), 3)
            assert (await store.get_turn(turn_id))["state"] == "completed"
            if reuse_directory:
                await service.create(str(tmp_path), "workspace_write", session_id="s2")
                await service.submit("s2", "next turn", "next-key")
                async with asyncio.timeout(3):
                    async for event in service.subscribe("s2"):
                        if event.kind == "turn_start":
                            break

        monkeypatch.setattr(runtime, "respond_approval", reply_then_finish)
        await service.decide("s1", approval_id, "decline")
        row = await store.get_approval(approval_id)
        assert row["delivery_state"] == "delivered"
        assert row["decision_receipt"]["request"]["rpc_request_id"] == "7"
        await store.acknowledge_approval(approval_id, row["decision_receipt"])
        await service.decide("s1", approval_id, "decline")
        events = await store.events_after("s1", None)
        assert [event.kind for event in events[-2:]] == ["turn_end", "approval_delivery"]
        assert sum(event.kind == "approval_delivery" for event in events) == 1
        assert (await service.get("s1"))["state"] == "ready"
        assert (await store.get_turn(turn_id))["state"] == "completed"
        assert runtime.replies == [("process-1:string:7", "decline")]
        if reuse_directory:
            assert (await service.get("s2"))["state"] == "running"
    finally:
        await service.aclose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_id", "other"),
        ("session_id", "other"),
        ("turn_id", "other"),
        ("external_turn_id", "other"),
        ("process_generation", "process-2"),
        ("external_request_id", "process-1:integer:7"),
        ("external_item_id", "other"),
        ("decision", "accept"),
        ("holder", "other"),
        ("lease_generation", 2),
        ("request", {"rpc_request_id": 7}),
    ],
)
@async_test
async def test_delivery_ack_rejects_mismatched_receipt(tmp_path, monkeypatch, field, value):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)

        async def check_receipt(request_id, decision):
            row = await store.get_approval(approval_id)
            receipt = copy.deepcopy(row["decision_receipt"])
            if field == "request":
                receipt["request"]["rpc_request_id"] = value["rpc_request_id"]
            else:
                receipt[field] = value
            with pytest.raises(RuntimeErrorInfo, match="approval_stale"):
                await store.acknowledge_approval(approval_id, receipt)
            assert (await store.get_approval(approval_id))["delivery_state"] == "decided"
            runtime.replies.append((request_id, decision))

        monkeypatch.setattr(runtime, "respond_approval", check_receipt)
        await service.decide("s1", approval_id, "decline")
        assert (await store.get_approval(approval_id))["delivery_state"] == "delivered"
    finally:
        await service.aclose()


@pytest.mark.parametrize("reuse_directory", [False, True])
@async_test
async def test_delivery_ack_rejects_same_turn_takeover(tmp_path, monkeypatch, reuse_directory):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)
        turn_id = (await store.get_approval(approval_id))["turn_id"]

        async def reply_after_takeover(request_id, decision):
            runner = service._tasks[turn_id]
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            before = await store.get_turn(turn_id)
            generation = await service.leases.adopt(
                turn_id,
                before["holder"],
                before["lease_generation"],
                "successor",
                confirmed_inactive=True,
            )
            assert generation == before["lease_generation"] + 1
            if reuse_directory:
                await store.owned(
                    turn_id, "successor", generation, "finish", turn_id, "completed", "", None
                )
                await service.leases.release(
                    turn_id, "successor", generation, confirmed_inactive=True
                )
                await service.create(str(tmp_path), "workspace_write", session_id="s2")
                await service.submit("s2", "next turn", "next-key")
                async with asyncio.timeout(3):
                    async for event in service.subscribe("s2"):
                        if event.kind == "turn_start":
                            break
            runtime.replies.append((request_id, decision))

        monkeypatch.setattr(runtime, "respond_approval", reply_after_takeover)
        with pytest.raises(RuntimeErrorInfo, match="approval_stale"):
            await service.decide("s1", approval_id, "decline")
        await service.decide("s1", approval_id, "decline")
        assert len(runtime.replies) == 1
        assert (await store.get_approval(approval_id))["delivery_state"] != "delivered"
        assert (await store.get_turn(turn_id))["holder"] == (
            None if reuse_directory else "successor"
        )
        assert not any(e.kind == "approval_delivery" for e in await store.events_after("s1", None))
    finally:
        await service.aclose()


@async_test
async def test_approval_continuation_cannot_use_successor_fence(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)
        await service.decide("s1", approval_id, "decline")
        approval = await store.get_approval(approval_id)
        turn = await store.get_turn(approval["turn_id"])
        await service.aclose()
        generation = await service.leases.adopt(
            turn["id"],
            turn["holder"],
            turn["lease_generation"],
            "successor",
            confirmed_inactive=True,
        )
        with pytest.raises(RuntimeErrorInfo, match="approval_stale"):
            await store.owned(
                turn["id"], "successor", generation, "continue_after_approval", approval_id
            )
    finally:
        await service.aclose()


@async_test
async def test_failed_ack_journal_write_rolls_back_delivery_and_never_replies_again(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)
        db = connect(tmp_path / "state.db")
        try:
            db.execute(
                "CREATE TRIGGER fail_ack BEFORE INSERT ON runtime_events WHEN NEW.kind='approval_delivery' "
                "BEGIN SELECT RAISE(ABORT,'injected acknowledgement failure'); END"
            )
            db.commit()
        finally:
            db.close()
        with pytest.raises(sqlite3.IntegrityError, match="injected acknowledgement failure"):
            await service.decide("s1", approval_id, "decline")
        assert (await store.get_approval(approval_id))["delivery_state"] == "decided"
        assert not any(e.kind == "approval_delivery" for e in await store.events_after("s1", None))
        await service.decide("s1", approval_id, "decline")
        assert runtime.replies == [("process-1:string:7", "decline")]
        assert (await service.get("s1"))["state"] == "waiting_approval"
    finally:
        await service.aclose()


@async_test
async def test_delivery_ack_without_durable_receipt_fails_closed(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval_id = await pending_approval(service, runtime)
        await store.decide_approval(approval_id, "decline")
        assert (await store.get_approval(approval_id))["decision_receipt"] is None
        with pytest.raises(RuntimeErrorInfo, match="approval_stale"):
            await store.acknowledge_approval(approval_id, None)
        assert (await store.get_approval(approval_id))["delivery_state"] == "decided"
    finally:
        await service.aclose()
