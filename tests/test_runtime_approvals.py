from __future__ import annotations

import asyncio

import pytest
from test_codex_adapter import async_test
from test_runtime_service import setup_service

from kairos_runtime.errors import RuntimeErrorInfo


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
