from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_codex_adapter import async_test

from kairos_runtime.contracts import RuntimeCapabilities, RuntimeObservation
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.service import AgentRuntimeService
from kairos_runtime.store import RuntimeStore
from kairos_state import connect


class FakeRuntime:
    generation = "process-1"

    def __init__(self):
        self.starts = []
        self.creates = 0
        self.replies = []
        self.events = asyncio.Queue()
        self.started = asyncio.Event()
        self.snapshot = RuntimeObservation("unknown", None, (), ())
        self.failure = None
        self.inspections = 0

    async def capabilities(self):
        return RuntimeCapabilities(1, frozenset({"text", "approvals", "resume"}))

    async def create_thread(self, session):
        self.creates += 1
        return "thread-" + session.session_id

    async def start_turn(self, session, turn_id, content):
        self.starts.append((session, turn_id, content))
        self.started.set()
        if self.failure:
            raise self.failure
        return "external-1"

    async def observe(self, session, turn_id):
        while True:
            event = await self.events.get()
            if isinstance(event, Exception):
                raise event
            yield {
                "generation": self.generation,
                "external_thread_id": session.external_thread_id,
                "external_turn_id": "external-1",
                **event,
            }

    async def inspect_turn(self, session, external_turn_id):
        self.inspections += 1
        return self.snapshot

    async def attach_turn(self, session, turn_id, external_turn_id):
        return self.snapshot

    async def cancel_turn(self, session, external_turn_id):
        pass

    async def respond_approval(self, request_id, decision):
        self.replies.append((request_id, decision))

    async def end_thread(self, session):
        pass

    async def aclose(self):
        pass


async def setup_service(tmp_path, **kwargs):
    store = RuntimeStore(tmp_path / "state.db")
    runtime = FakeRuntime()
    service = AgentRuntimeService(store, runtime, allowed_directories=(str(tmp_path),), **kwargs)
    details = await service.create(str(tmp_path), "workspace_write", session_id="s1")
    assert details["session_id"] == "s1"
    return service, store, runtime


async def terminal(service, session_id="s1"):
    async with asyncio.timeout(3):
        async for event in service.subscribe(session_id):
            if event.kind == "turn_end":
                return event


@async_test
async def test_idempotent_submit_publishes_only_committed_atomic_terminal(tmp_path: Path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        turn = await service.submit("s1", "hello", "key")
        assert await service.submit("s1", "hello", "key") == turn
        await runtime.started.wait()
        for delta in ("ha", "ha"):
            await runtime.events.put({"kind": "text", "payload": {"itemId": "i1", "delta": delta}})
        await runtime.events.put({"kind": "turn", "payload": {"state": "completed"}})
        async with asyncio.timeout(3):
            async for event in service.subscribe("s1"):
                assert event in await store.events_after("s1", None)
                if event.kind == "turn_end":
                    db = connect(tmp_path / "state.db")
                    row = db.execute(
                        "SELECT content,token_count,display_metadata FROM messages WHERE role='assistant'"
                    ).fetchone()
                    assert row["content"] == "haha"
                    assert row["token_count"] is None
                    assert json.loads(row["display_metadata"])["usage"]["status"] == "unknown"
                    assert db.execute("SELECT state FROM runtime_sessions").fetchone()[0] == "ready"
                    db.close()
                    break
        assert len(runtime.starts) == 1
    finally:
        await service.aclose()


@async_test
async def test_subscription_disconnect_does_not_cancel_owned_execution(tmp_path):
    service, _store, runtime = await setup_service(tmp_path)
    try:
        await service.submit("s1", "hello", "key")
        subscriber = service.subscribe("s1")
        await anext(subscriber)
        await subscriber.aclose()
        await runtime.events.put({"kind": "turn", "payload": {"state": "completed"}})
        assert (await terminal(service)).payload["state"] == "completed"
        assert len(runtime.starts) == 1
    finally:
        await service.aclose()


@async_test
async def test_same_thread_bind_preserves_ended_and_create_retry_never_restarts(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        await service.end("s1")
        await store.bind_thread("s1", "thread-s1", await runtime.capabilities())
        assert (await service.get("s1"))["state"] == "ended"
        await service.create(str(tmp_path), "workspace_write", session_id="s1")
        assert runtime.creates == 1
    finally:
        await service.aclose()


@async_test
async def test_revoked_allowlist_blocks_start_and_has_durable_outcome(tmp_path):
    roots = [str(tmp_path)]
    service, _store, runtime = await setup_service(tmp_path, policy_roots=lambda: tuple(roots))
    try:
        roots.clear()
        with pytest.raises(RuntimeErrorInfo, match="invalid_directory"):
            await service.submit("s1", "hello", "key")
        assert runtime.starts == []
    finally:
        await service.aclose()


@async_test
async def test_duplicate_event_does_not_duplicate_projection(tmp_path):
    service, _store, runtime = await setup_service(tmp_path)
    try:
        await service.submit("s1", "hello", "key")
        event = {
            "event_id": "same-event",
            "kind": "text",
            "payload": {"itemId": "i1", "delta": "one"},
        }
        await runtime.events.put(event)
        await runtime.events.put(event)
        await runtime.events.put({"kind": "turn", "payload": {"state": "completed"}})
        assert (await terminal(service)).payload["content"] == "one"
    finally:
        await service.aclose()


@async_test
async def test_cancel_timeout_keeps_quarantine_and_session_cannot_end(tmp_path):
    service, store, _runtime = await setup_service(tmp_path, cancel_timeout=0.01)
    try:
        turn = await service.submit("s1", "hello", "key")
        async for event in service.subscribe("s1"):
            if event.kind == "turn_start":
                break
        with pytest.raises(RuntimeErrorInfo, match="cancel_partial"):
            await service.cancel("s1", turn)
        assert (await store.get_turn(turn))["state"] == "interrupted"
        with pytest.raises(RuntimeErrorInfo, match="cancel_partial"):
            await service.end("s1")
        assert (await service.get("s1"))["state"] != "ended"
    finally:
        await service.aclose()


@pytest.mark.parametrize(
    "interrupt_behavior", ["missing_response", "transport_error", "terminal_without_response"]
)
@async_test
async def test_cancel_bounds_interrupt_delivery_and_uses_independent_terminal_evidence(
    tmp_path, monkeypatch, interrupt_behavior
):
    service, store, runtime = await setup_service(tmp_path, cancel_timeout=0.03)
    interrupts = []

    async def interrupt(session, external_turn_id):
        interrupts.append(external_turn_id)
        if interrupt_behavior == "transport_error":
            raise RuntimeErrorInfo("transport", "interrupt response lost", True)
        if interrupt_behavior == "terminal_without_response":
            await runtime.events.put({"kind": "turn", "payload": {"state": "interrupted"}})
        await asyncio.Future()

    monkeypatch.setattr(runtime, "cancel_turn", interrupt)
    try:
        turn = await service.submit("s1", "hello", "key")
        async for event in service.subscribe("s1"):
            if event.kind == "turn_start":
                break
        async with asyncio.timeout(0.3):
            if interrupt_behavior == "terminal_without_response":
                await service.cancel("s1", turn)
                assert (await store.get_turn(turn))["state"] == "cancelled"
                assert (await store.get_turn(turn))["inactive_confirmed_at"] is not None
                assert (await service.get("s1"))["state"] == "ready"
            else:
                with pytest.raises(RuntimeErrorInfo, match="cancel_partial"):
                    await service.cancel("s1", turn)
                assert (await store.get_turn(turn))["state"] == "interrupted"
                assert (await store.get_turn(turn))["inactive_confirmed_at"] is None
                assert await service.submit("s1", "hello", "key") == turn
        assert interrupts == ["external-1"]
        if interrupt_behavior == "transport_error":
            assert runtime.inspections >= 1
        elif interrupt_behavior == "missing_response":
            assert runtime.inspections == 0
    finally:
        await service.aclose()


@async_test
async def test_cancel_shares_one_deadline_between_delivery_and_terminal_wait(tmp_path, monkeypatch):
    service, store, runtime = await setup_service(tmp_path, cancel_timeout=0.2)

    async def delayed_interrupt(session, external_turn_id):
        await asyncio.sleep(0.12)

    monkeypatch.setattr(runtime, "cancel_turn", delayed_interrupt)
    try:
        turn = await service.submit("s1", "hello", "key")
        async for event in service.subscribe("s1"):
            if event.kind == "turn_start":
                break
        async with asyncio.timeout(0.28):
            with pytest.raises(RuntimeErrorInfo, match="cancel_partial"):
                await service.cancel("s1", turn)
        assert (await store.get_turn(turn))["state"] == "interrupted"
    finally:
        await service.aclose()


@async_test
async def test_cancel_waits_for_terminal_evidence_and_sets_cancelled(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        turn = await service.submit("s1", "hello", "key")
        async for event in service.subscribe("s1"):
            if event.kind == "turn_start":
                break
        cancelling = asyncio.create_task(service.cancel("s1", turn))
        async for event in service.subscribe("s1"):
            if event.kind == "turn_state" and event.payload["state"] == "cancelling":
                break
        assert not cancelling.done()
        await runtime.events.put({"kind": "turn", "payload": {"state": "interrupted"}})
        await cancelling
        assert (await store.get_turn(turn))["state"] == "cancelled"
        assert (await service.get("s1"))["state"] == "ready"
    finally:
        await service.aclose()


@async_test
async def test_slow_subscriber_gets_resumable_gap_without_blocking_execution(tmp_path):
    service, store, runtime = await setup_service(tmp_path, subscriber_backlog=2)
    try:
        turn = await service.submit("s1", "hello", "key")
        subscription = service.subscribe("s1")
        first = await anext(subscription)
        for index in range(5):
            await runtime.events.put(
                {"kind": "text", "payload": {"itemId": "i1", "delta": str(index)}}
            )
        await runtime.events.put({"kind": "turn", "payload": {"state": "completed"}})
        await terminal(service)
        with pytest.raises(RuntimeErrorInfo, match="sequence_gap"):
            while True:
                await anext(subscription)
        assert (await store.get_turn(turn))["state"] == "completed"
        assert (await store.events_after("s1", first.cursor))[-1].kind == "turn_end"
        resumed = service.subscribe("s1", first.cursor)
        assert (await anext(resumed)).sequence > first.sequence
        await resumed.aclose()
    finally:
        await service.aclose()


@async_test
async def test_start_failure_after_thread_response_keeps_unbound_identity_without_retry(
    tmp_path, monkeypatch
):
    store = RuntimeStore(tmp_path / "state.db")
    runtime = FakeRuntime()
    service = AgentRuntimeService(store, runtime, allowed_directories=(str(tmp_path),))

    async def fail_bind(*args):
        raise RuntimeErrorInfo("runtime_internal", "binding failed", False)

    monkeypatch.setattr(store, "bind_thread", fail_bind)
    try:
        with pytest.raises(RuntimeErrorInfo, match="runtime_internal"):
            await service.create(str(tmp_path), "workspace_write", session_id="uncertain")
        details = await service.create(str(tmp_path), "workspace_write", session_id="uncertain")
        assert details["external_thread_id"] is None
        assert details["state"] == "interrupted"
        assert runtime.creates == 1
        await service.recover()
        assert runtime.creates == 1
    finally:
        await service.aclose()


@async_test
async def test_terminal_projection_and_state_rollback_if_journal_insert_fails(tmp_path):
    import sqlite3

    service, store, runtime = await setup_service(tmp_path)
    try:
        turn = await service.submit("s1", "hello", "key")
        async for event in service.subscribe("s1"):
            if event.kind == "turn_start":
                break
        db = connect(tmp_path / "state.db")
        db.execute(
            "CREATE TRIGGER fail_end BEFORE INSERT ON runtime_events WHEN NEW.kind='turn_end' BEGIN SELECT RAISE(ABORT,'injected terminal failure'); END"
        )
        db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            await store.finish(turn, "completed", "final", None)
        assert (await store.get_turn(turn))["state"] == "running"
        assert db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_end")
        db.commit()
        db.close()
        await runtime.events.put({"kind": "turn", "payload": {"state": "completed"}})
        await terminal(service)
    finally:
        await service.aclose()


@async_test
async def test_queued_cancel_commits_terminal_and_allows_successor_without_dispatch(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        await service.submit("s1", "blocking", "one")
        await runtime.started.wait()
        await service.create(str(tmp_path), "workspace_write", session_id="s2")
        queued = await service.submit("s2", "waiting", "two")
        await service.cancel("s2", queued)
        assert (await store.get_turn(queued))["state"] == "cancelled"
        assert (await store.events_after("s2", None))[-1].kind == "turn_end"
        assert len(runtime.starts) == 1
    finally:
        await service.aclose()


@async_test
async def test_generation_change_after_dispatch_commit_prevents_wrong_process_start(
    tmp_path, monkeypatch
):
    service, store, runtime = await setup_service(tmp_path)
    original = store.owned

    async def changing_owned(turn_id, holder, generation, operation, *args):
        result = await original(turn_id, holder, generation, operation, *args)
        if operation == "dispatch":
            runtime.generation = "process-2"
        return result

    monkeypatch.setattr(store, "owned", changing_owned)
    try:
        await service.submit("s1", "hello", "key")
        async with asyncio.timeout(3):
            async for event in service.subscribe("s1"):
                if event.kind in {"turn_end", "turn_start"}:
                    assert event.kind == "turn_end"
                    break
        assert runtime.starts == []
    finally:
        await service.aclose()


@async_test
async def test_heartbeat_ownership_loss_stops_reader_and_keeps_quarantine(tmp_path, monkeypatch):
    service, store, runtime = await setup_service(tmp_path, renew_interval=0.01)
    original = service.leases.renew
    renewed = []

    async def lose_renewal(*args):
        renewed.append(args)
        return await original(*args) if len(renewed) == 1 else False

    monkeypatch.setattr(service.leases, "renew", lose_renewal)
    try:
        turn = await service.submit("s1", "hello", "key")
        assert (await terminal(service)).payload["state"] == "interrupted"
        assert len(renewed) == 2
        assert len(runtime.starts) == 1
        assert (await store.get_turn(turn))["inactive_confirmed_at"] is None
    finally:
        await service.aclose()


@async_test
async def test_create_rejects_existing_model_session_identity(tmp_path):
    service, store, _runtime = await setup_service(tmp_path)
    db = connect(tmp_path / "state.db")
    db.execute("INSERT INTO sessions(id,source,started_at) VALUES ('model-session','web',1)")
    db.commit()
    db.close()
    try:
        with pytest.raises(RuntimeErrorInfo, match="idempotency_conflict"):
            await service.create(str(tmp_path), "workspace_write", session_id="model-session")
        assert await store.get_session("s1")
    finally:
        await service.aclose()


@async_test
async def test_explicit_empty_session_identity_is_not_silently_replaced(tmp_path):
    service, _store, runtime = await setup_service(tmp_path)
    try:
        with pytest.raises(RuntimeErrorInfo, match="invalid_event"):
            await service.create(str(tmp_path), "workspace_write", session_id="")
        assert runtime.creates == 1
    finally:
        await service.aclose()


@async_test
async def test_known_usage_is_kept_in_terminal_without_provider_ledger(tmp_path):
    service, _store, runtime = await setup_service(tmp_path)
    try:
        await service.submit("s1", "hello", "key")
        await runtime.events.put(
            {
                "kind": "usage",
                "payload": {
                    "tokenUsage": {"total": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5}}
                },
            }
        )
        await runtime.events.put({"kind": "turn", "payload": {"state": "completed"}})
        event = await terminal(service)
        assert event.payload["usage"]["status"] == "known"
        assert event.payload["usage"]["value"]["tokenUsage"]["total"]["totalTokens"] == 5
        db = connect(tmp_path / "state.db")
        assert db.execute("SELECT COUNT(*) FROM session_model_usage").fetchone()[0] == 0
        db.close()
    finally:
        await service.aclose()


@async_test
async def test_shutdown_preserves_queued_input_for_first_dispatch_after_recovery(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    await service.submit("s1", "blocking", "one")
    await runtime.started.wait()
    await service.create(str(tmp_path), "workspace_write", session_id="s2")
    queued = await service.submit("s2", "durable waiting", "two")
    # Let the service-owned worker enter its directory-lease wait.
    await asyncio.sleep(0.06)
    await service.aclose()
    saved = await store.get_turn(queued)
    assert saved["state"] == "queued"
    assert saved["send_state"] == "not_sent"
    assert saved["content"] == "durable waiting"
    assert saved["idempotency_key"] == "two"
