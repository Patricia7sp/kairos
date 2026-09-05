from __future__ import annotations

import asyncio

import pytest
from test_codex_adapter import async_test
from test_runtime_service import FakeRuntime, setup_service, terminal

from kairos_runtime.contracts import RuntimeObservation
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.service import AgentRuntimeService


@async_test
async def test_lost_start_response_is_uncertain_and_never_resent(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    runtime.failure = RuntimeErrorInfo("transport", "unavailable", True)
    try:
        turn = await service.submit("s1", "hello", "key")
        event = await terminal(service)
        assert event.payload["state"] == "interrupted"
        assert (await store.get_turn(turn))["send_state"] == "uncertain"
        assert await service.submit("s1", "hello", "key") == turn
        async with asyncio.timeout(3):
            while service._tasks:
                await asyncio.sleep(0)
        assert runtime.inspections >= 1
        with pytest.raises(RuntimeErrorInfo, match="session_busy"):
            await service.submit("s1", "another", "other")
        await service.recover()
        assert len(runtime.starts) == 1
    finally:
        await service.aclose()


@async_test
async def test_recovery_snapshot_replaces_partial_text_and_requires_inactivity(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    turn = await service.submit("s1", "hello", "key")
    await runtime.started.wait()
    await runtime.events.put({"kind": "text", "payload": {"itemId": "i1", "delta": "par"}})
    await service.aclose()
    recovered = FakeRuntime()
    recovered.generation = "process-2"
    recovered.snapshot = RuntimeObservation(
        "completed",
        "external-1",
        ({"id": "i1", "type": "agentMessage", "text": "partial final"},),
        (),
    )
    next_service = AgentRuntimeService(store, recovered, allowed_directories=(str(tmp_path),))
    try:
        await next_service.recover()
        assert (await next_service.get("s1"))["state"] == "interrupted"
        assert recovered.starts == []
        events = await store.events_after("s1", None)
        assert any(e.kind == "reconciled" for e in events)
        assert (await store.get_turn(turn))["inactive_confirmed_at"] is None
    finally:
        await next_service.aclose()


@async_test
async def test_unknown_dispatch_never_projects_an_unrelated_external_turn(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    runtime.failure = RuntimeErrorInfo("transport", "unavailable", True)
    try:
        await service.submit("s1", "hello", "key")
        await terminal(service)
        runtime.snapshot = RuntimeObservation(
            "completed",
            "unrelated-turn",
            ({"id": "i1", "type": "agentMessage", "text": "not ours"},),
            (),
        )
        await service.recover()
        events = await store.events_after("s1", None)
        assert not any(e.kind == "reconciled" for e in events)
        assert events[-1].payload.get("content", "") != "not ours"
    finally:
        await service.aclose()


@async_test
async def test_active_recovery_adopts_fenced_lease_and_streams_without_resend(tmp_path):
    service, store, _runtime = await setup_service(tmp_path)
    turn = await service.submit("s1", "hello", "key")
    async for event in service.subscribe("s1"):
        if event.kind == "turn_start":
            break
    await service.aclose()
    recovered = FakeRuntime()
    recovered.generation = "process-2"
    recovered.snapshot = RuntimeObservation("active", "external-1", (), ())
    next_service = AgentRuntimeService(
        store,
        recovered,
        allowed_directories=(str(tmp_path),),
        inactivity_confirmed=lambda generation: generation == "process-1",
    )
    before = await store.get_turn(turn)
    try:
        await next_service.recover()
        await next_service.recover()
        await recovered.events.put(
            {"kind": "text", "payload": {"itemId": "i1", "delta": "recovered"}}
        )
        await recovered.events.put({"kind": "turn", "payload": {"state": "completed"}})
        async with asyncio.timeout(3):
            async for event in next_service.subscribe("s1"):
                if event.kind == "turn_end" and event.payload["state"] == "completed":
                    assert event.payload["content"] == "recovered"
                    break
        assert recovered.starts == []
        after = await store.get_turn(turn)
        assert after["lease_generation"] > before["lease_generation"]
        assert after["send_state"] == "confirmed"
    finally:
        await next_service.aclose()


@async_test
async def test_active_inspection_then_terminal_attachment_snapshot_finalizes_without_notification(
    tmp_path, monkeypatch
):
    service, store, _runtime = await setup_service(tmp_path)
    turn = await service.submit("s1", "hello", "key")
    async for event in service.subscribe("s1"):
        if event.kind == "turn_start":
            break
    await service.aclose()
    recovered = FakeRuntime()
    recovered.generation = "process-2"
    recovered.snapshot = RuntimeObservation("active", "external-1", (), ())
    completed = RuntimeObservation(
        "completed",
        "external-1",
        ({"id": "i1", "type": "agentMessage", "text": "completed while attaching"},),
        (),
    )

    async def completed_attach(session, turn_id, external_turn_id):
        assert external_turn_id == "external-1"
        await recovered.events.put(
            {"kind": "text", "payload": {"itemId": "i1", "delta": "completed"}}
        )
        await recovered.events.put(
            {"kind": "snapshot", "payload": {"state": completed.state, "items": completed.items}}
        )
        return completed

    monkeypatch.setattr(recovered, "attach_turn", completed_attach)
    next_service = AgentRuntimeService(
        store,
        recovered,
        allowed_directories=(str(tmp_path),),
        inactivity_confirmed=lambda generation: generation == "process-1",
    )
    try:
        await next_service.recover()
        async with asyncio.timeout(0.3):
            async for event in next_service.subscribe("s1"):
                if event.kind == "turn_end" and event.payload["state"] == "completed":
                    assert event.payload["content"] == "completed while attaching"
                    break
            await asyncio.gather(*tuple(next_service._tasks.values()))
        assert (await store.get_turn(turn))["inactive_confirmed_at"] is not None
        assert (await next_service.get("s1"))["state"] == "ready"
        assert recovered.starts == []
        events = await store.events_after("s1", None)
        assert [event.kind for event in events[-3:]] == ["text", "reconciled", "turn_end"]
    finally:
        await next_service.aclose()


@async_test
async def test_takeover_rejects_changed_owner_and_old_owner_cannot_write(tmp_path):
    service, store, _runtime = await setup_service(tmp_path)
    turn = await service.submit("s1", "hello", "key")
    async for event in service.subscribe("s1"):
        if event.kind == "turn_start":
            break
    await service.aclose()
    before = await store.get_turn(turn)
    assert (
        await service.leases.adopt(
            turn, "wrong-owner", before["lease_generation"], "next", confirmed_inactive=True
        )
        is None
    )
    generation = await service.leases.adopt(
        turn, before["holder"], before["lease_generation"], "next", confirmed_inactive=True
    )
    assert generation == before["lease_generation"] + 1
    with pytest.raises(RuntimeErrorInfo, match="lease_lost"):
        await store.owned(
            turn,
            before["holder"],
            before["lease_generation"],
            "append",
            turn,
            "stale",
            "text",
            {"delta": "stale"},
        )
    assert not any(e.event_id == "stale" for e in await store.events_after("s1", None))
    assert not await store.lose_turn(
        turn, before["holder"], before["lease_generation"], "stale partial", None
    )
    assert (await store.get_turn(turn))["state"] == "running"
    with pytest.raises(RuntimeErrorInfo, match="cancel_partial"):
        await service.cancel("s1", turn)
    assert (await store.get_turn(turn))["state"] == "running"
    service.runtime.snapshot = RuntimeObservation(
        "completed",
        "external-1",
        ({"id": "i1", "type": "agentMessage", "text": "stale snapshot"},),
        (),
    )
    await service._inspect_loss(before)
    assert not any(e.kind == "reconciled" for e in await store.events_after("s1", None))


@async_test
async def test_recovery_repairs_legacy_terminal_missing_journal_event(tmp_path):
    from kairos_state import connect

    service, store, _runtime = await setup_service(tmp_path)
    turn = await store.admit("s1", "legacy", "hello")
    db = connect(tmp_path / "state.db")
    db.execute("UPDATE runtime_turns SET state='cancelled' WHERE id=?", (turn,))
    db.commit()
    db.close()
    try:
        await service.recover()
        assert (await store.events_after("s1", None))[-1].kind == "turn_end"
    finally:
        await service.aclose()


@async_test
async def test_missing_thread_marks_unavailable_without_recreation(tmp_path, monkeypatch):
    service, store, runtime = await setup_service(tmp_path)
    await service.submit("s1", "hello", "key")
    async for event in service.subscribe("s1"):
        if event.kind == "turn_start":
            break
    await service.aclose()
    recovered = FakeRuntime()

    async def missing(*args):
        raise RuntimeErrorInfo("thread_missing", "thread de runtime ausente", False)

    monkeypatch.setattr(recovered, "inspect_turn", missing)
    next_service = AgentRuntimeService(store, recovered, allowed_directories=(str(tmp_path),))
    try:
        await next_service.recover()
        assert (await next_service.get("s1"))["state"] == "unavailable"
        assert recovered.creates == 0
        assert recovered.starts == []
        assert len(runtime.starts) == 1
    finally:
        await next_service.aclose()
