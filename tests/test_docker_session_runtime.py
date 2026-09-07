"""Session ownership, policy, recovery and checkpoint publication boundary."""

import asyncio
import gc
import io
import json
import tarfile
import weakref
from dataclasses import replace

import pytest

from kairos_runtime.codex_rpc import CodexSubscription
from kairos_runtime.contracts import RuntimeSession
from kairos_runtime.docker_backend.model_relay import ModelRelay
from kairos_runtime.docker_backend.registry import SessionRegistry
from kairos_runtime.docker_backend.runtime import DockerSessionRuntime
from kairos_runtime.errors import RuntimeErrorInfo


def archive():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w"):
        pass
    return output.getvalue()


class FakeRpc:
    def __init__(self, worker):
        self.worker = worker
        self.generation = worker.generation
        self.subscriptions = []
        self.calls = []
        self.replies = []
        self.turn = None

    def subscribe(self, thread_id):
        subscription = CodexSubscription(self, self.generation, thread_id)
        self.subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription):
        if subscription in self.subscriptions:
            self.subscriptions.remove(subscription)

    async def call(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method in {"thread/start", "thread/resume", "thread/read"}:
            thread_id = params.get("threadId", "thread-" + self.worker.name)
            return {
                "thread": {
                    "id": thread_id,
                    "historyMode": "legacy",
                    "turns": [self.turn] if self.turn else [],
                    "status": {"type": "active"},
                },
                "cwd": "/workspace",
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "modelProvider": "kairos",
            }
        if method == "turn/start":
            self.turn = {"id": "turn-" + self.worker.name, "status": "inProgress", "items": []}
            return {"turn": self.turn}
        if method == "turn/interrupt":
            self.complete("interrupted")
            return {}
        if method == "thread/unsubscribe":
            return {"status": "unsubscribed"}
        if method == "thread/name/set":
            self.worker.log.append("persist")
            return {}
        raise AssertionError(method)

    async def reply(self, request_id, result):
        self.replies.append((request_id, result))

    async def reply_error(self, request_id, **kwargs):
        self.replies.append((request_id, "error"))

    def complete(self, state="completed"):
        self.turn["status"] = state
        for subscription in self.subscriptions:
            subscription.queue.put_nowait(
                {
                    "method": "turn/completed",
                    "params": {"threadId": subscription.thread_id, "turn": dict(self.turn)},
                }
            )


class FakeRelay:
    def __init__(self, log):
        self.log = log
        self.turn = None

    def allow_turn(self, turn_id):
        assert self.turn is None
        self.turn = turn_id
        self.log.append("allow")

    async def end_turn(self, turn_id):
        if self.turn == turn_id:
            self.log.append("revoke")
            self.turn = None


class Factory:
    def __init__(self, registry):
        self.registry = registry
        self.workers = []
        self.fail_close = False
        self.fail_checkpoint = False
        self.gate = None

    def __call__(self, project, **kwargs):
        worker = FakeWorker(self, project, kwargs)
        self.workers.append(worker)
        return worker


class FakeWorker:
    def __init__(self, factory, project, settings):
        self.factory = factory
        self.project = project
        self.settings = settings
        self.name = f"worker-{len(factory.workers)}"
        self.generation = "generation-" + self.name
        self.log = []
        self.relay = FakeRelay(self.log)
        self.rpc = FakeRpc(self)
        self.closed = False

    async def __aenter__(self):
        assert any(
            record.worker_name == self.name for record in self.factory.registry.list_workers()
        )
        self.log.append("enter")
        return self

    async def checkpoint(self):
        assert any(
            row.worker_name == self.name and row.worker_confirmed
            for row in self.factory.registry.list_workers()
        )
        self.log.append("checkpoint")
        if self.factory.gate:
            await self.factory.gate.wait()
        if self.factory.fail_checkpoint:
            raise RuntimeError("checkpoint failure")
        return archive(), archive()

    async def aclose(self):
        self.log.append("close")
        if self.factory.fail_close:
            raise RuntimeError("uncertain removal")
        self.closed = True


async def setup(tmp_path, *, max_workers=2):
    project = tmp_path / "project"
    project.mkdir()
    registry = SessionRegistry(tmp_path / "registry")
    factory = Factory(registry)

    async def remover(name, *, confirmed=False):
        pass

    runtime = DockerSessionRuntime(
        registry,
        image="test",
        transport=None,
        model="test-model",
        max_workers=max_workers,
        worker_factory=factory,
        worker_remover=remover,
    )
    await runtime.recover()
    session = RuntimeSession("session-1", "codex", str(project), "workspace_write")
    return runtime, registry, factory, session


def test_create_checkpoints_initial_thread_and_dispatch_restores_under_exact_policy(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            thread = await runtime.create_thread(session)
            session = replace(session, external_thread_id=thread)
            assert factory.workers[0].closed
            assert factory.workers[0].log == ["enter", "persist", "checkpoint", "close"]
            assert not registry.list_workers()
            assert registry.archives(session.session_id) is not None
            await runtime.start_turn(session, "local-turn", "hello")
            worker = factory.workers[-1]
            assert worker.settings["workspace_archive"] == archive()
            assert worker.settings["home_archive"] == archive()
            assert worker.settings["writable"] is True
            resume = next(
                params for method, params in worker.rpc.calls if method == "thread/resume"
            )
            start = next(params for method, params in worker.rpc.calls if method == "turn/start")
            assert resume["sandbox"] == "read-only"
            assert resume["cwd"] == start["cwd"] == "/workspace"
            assert start["sandboxPolicy"] == {
                "type": "externalSandbox",
                "networkAccess": "restricted",
            }
            worker.rpc.complete()
            events = [event async for event in runtime.observe(session, "local-turn")]
            assert events[-1]["generation"] == runtime.generation
            assert events[-1]["external_thread_id"] == thread
            assert worker.log[-3:] == ["revoke", "checkpoint", "close"]
            assert not registry.list_workers()
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_terminal_waits_for_durable_checkpoint_and_removal(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            await runtime.start_turn(session, "turn", "hello")
            worker = factory.workers[-1]
            factory.gate = asyncio.Event()
            worker.rpc.complete()
            observer = runtime.observe(session, "turn")
            terminal = asyncio.create_task(anext(observer))
            await asyncio.sleep(0.02)
            assert not terminal.done()
            assert registry.list_workers()
            factory.gate.set()
            assert (await terminal)["payload"]["state"] == "completed"
            assert worker.closed and not registry.list_workers()
            await observer.aclose()
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["fail_checkpoint", "fail_close"])
def test_failed_terminal_never_publishes_success_and_cleanup_failure_blocks_admission(
    tmp_path, failure
):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            await runtime.start_turn(session, "turn", "hello")
            setattr(factory, failure, True)
            factory.workers[-1].rpc.complete()
            with pytest.raises(RuntimeErrorInfo):
                await anext(runtime.observe(session, "turn"))
            if failure == "fail_close":
                assert registry.list_workers()
                assert not runtime.attests_inactive("old-generation")
                with pytest.raises(RuntimeErrorInfo, match="unavailable"):
                    await runtime.start_turn(session, "new-turn", "hello")
        finally:
            factory.fail_close = False
            factory.fail_checkpoint = False
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_spoofed_worker_generation_is_rejected_before_translation(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            await runtime.start_turn(session, "turn", "hello")
            worker = factory.workers[-1]
            worker.rpc.subscriptions[0].generation = "forged"
            worker.rpc.complete()
            with pytest.raises(RuntimeErrorInfo, match="invalid_event"):
                await anext(runtime.observe(session, "turn"))
            assert worker.closed
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_restart_reaps_recorded_workers_before_admission_without_replaying_turn(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        session = replace(session, external_thread_id=await runtime.create_thread(session))
        await runtime.aclose()
        registry.record_worker(session.session_id, "abandoned-worker")
        removed = []

        async def remove(name, *, confirmed):
            assert confirmed is False
            removed.append(name)

        replacement = DockerSessionRuntime(
            registry,
            image="test",
            transport=None,
            model="test-model",
            worker_factory=factory,
            worker_remover=remove,
        )
        try:
            assert not replacement.attests_inactive(runtime.generation)
            with pytest.raises(RuntimeErrorInfo, match="unavailable"):
                await replacement.start_turn(session, "must-not-replay", "hello")
            await replacement.recover()
            assert removed == ["abandoned-worker"]
            assert replacement.attests_inactive(runtime.generation)
            assert not registry.list_workers()
            observation = await replacement.inspect_turn(session, "abandoned-turn")
            assert observation.state == "unknown"
            assert not any(
                method == "turn/start"
                for worker in factory.workers
                for method, _ in worker.rpc.calls
            )
        finally:
            await replacement.aclose()
            registry.close()

    asyncio.run(run())


def test_rejects_broad_access_and_session_identity_rebinding(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            with pytest.raises(RuntimeErrorInfo, match="invalid_policy"):
                await runtime.create_thread(replace(session, sandbox="broad_access"))
            thread = await runtime.create_thread(session)
            with pytest.raises(RuntimeErrorInfo):
                await runtime.start_turn(
                    replace(session, external_thread_id=thread, sandbox="read_only"),
                    "turn",
                    "hello",
                )
            assert len(factory.workers) == 1
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_two_sessions_are_routed_and_worker_limit_waits_for_verified_release(tmp_path):
    async def run():
        runtime, registry, factory, first = await setup(tmp_path, max_workers=1)
        second = replace(first, session_id="session-2")
        try:
            first = replace(first, external_thread_id=await runtime.create_thread(first))
            second = replace(second, external_thread_id=await runtime.create_thread(second))
            await runtime.start_turn(first, "first-turn", "first")
            first_worker = factory.workers[-1]
            pending = asyncio.create_task(runtime.start_turn(second, "second-turn", "second"))
            await asyncio.sleep(0.02)
            assert not pending.done()
            assert len(registry.list_workers()) == 1
            first_worker.rpc.complete()
            first_events = [event async for event in runtime.observe(first, "first-turn")]
            await pending
            second_worker = factory.workers[-1]
            second_worker.rpc.complete()
            second_events = [event async for event in runtime.observe(second, "second-turn")]
            assert first_events[-1]["external_thread_id"] != second_events[-1]["external_thread_id"]
            assert first_events[-1]["external_turn_id"] != second_events[-1]["external_turn_id"]
            assert all(worker.closed for worker in factory.workers)
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_cancel_revokes_model_and_preserves_terminal_checkpoint(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            external = await runtime.start_turn(session, "turn", "hello")
            worker = factory.workers[-1]
            await runtime.cancel_turn(session, external)
            assert worker.relay.turn is None
            events = [event async for event in runtime.observe(session, "turn")]
            assert events[-1]["payload"]["state"] == "interrupted"
            assert worker.closed
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_cancel_interrupts_codex_before_relay_failure_can_finish_the_turn(tmp_path, monkeypatch):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        relay = None
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            external = await runtime.start_turn(session, "turn", "hello")
            worker = factory.workers[-1]
            original_call = worker.rpc.call

            async def call(method, params, **kwargs):
                # Codex cannot interrupt a turn already finished by a model error.
                if method == "turn/interrupt" and worker.rpc.turn["status"] != "inProgress":
                    return {}
                return await original_call(method, params, **kwargs)

            monkeypatch.setattr(worker.rpc, "call", call)
            started = asyncio.Event()

            class Writer:
                def write(self, data):
                    frame = json.loads(data)
                    if frame["type"] == "error" and worker.rpc.turn["status"] == "inProgress":
                        worker.rpc.complete("failed")

                async def drain(self):
                    await asyncio.sleep(0)

                def close(self):
                    pass

                async def wait_closed(self):
                    pass

            async def transport(body):
                yield b"data: first\n\n"
                started.set()
                await asyncio.Event().wait()

            reader = asyncio.StreamReader()
            reader.feed_data(b'{"type":"ready","protocol":1}\n')
            relay = worker.relay = ModelRelay(reader, Writer(), transport, "test-model")
            await relay.start()
            relay.allow_turn("turn")
            reader.feed_data(
                b'{"type":"request","id":1,"body":{"model":"test-model","stream":true}}\n'
            )
            async with asyncio.timeout(2):
                await started.wait()
                await runtime.cancel_turn(session, external)
                events = [event async for event in runtime.observe(session, "turn")]
            assert events[-1]["payload"]["state"] in {"interrupted", "cancelled"}
            assert (await runtime.inspect_turn(session, external)).state == "cancelled"
            assert not registry.list_workers()
        finally:
            await runtime.aclose()
            if relay is not None:
                await relay.aclose()
            registry.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_interrupt_failure_removes_worker_and_revokes_model(tmp_path, monkeypatch, failure):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            external = await runtime.start_turn(session, "turn", "hello")
            worker = factory.workers[-1]
            original_call = worker.rpc.call

            async def call(method, params, **kwargs):
                if method == "turn/interrupt":
                    raise failure("interrupt unavailable")
                return await original_call(method, params, **kwargs)

            monkeypatch.setattr(worker.rpc, "call", call)
            with pytest.raises(failure):
                await runtime.cancel_turn(session, external)
            assert not registry.list_workers()
            assert worker.closed
            assert worker.relay.turn is None
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_approvals_route_only_to_originating_worker_and_never_escalate(tmp_path):
    async def run():
        runtime, registry, factory, first = await setup(tmp_path)
        second = replace(first, session_id="session-2")
        observers = []
        try:
            for index, unbound in enumerate([first, second]):
                session = replace(unbound, external_thread_id=await runtime.create_thread(unbound))
                external = await runtime.start_turn(session, f"turn-{index}", "hello")
                worker = factory.workers[-1]
                for subscription in worker.rpc.subscriptions:
                    subscription.queue.put_nowait(
                        {
                            "id": 1,
                            "method": "item/commandExecution/requestApproval",
                            "params": {
                                "threadId": session.external_thread_id,
                                "turnId": external,
                                "itemId": "tool",
                                "startedAtMs": 1,
                            },
                        }
                    )
                observer = runtime.observe(session, f"turn-{index}")
                event = await anext(observer)
                observers.append((observer, worker, event["payload"]["request_id"]))
            assert observers[0][2] != observers[1][2]
            await runtime.respond_approval(observers[0][2], "decline")
            assert observers[0][1].rpc.replies == [(1, {"decision": "decline"})]
            assert observers[1][1].rpc.replies == []
            with pytest.raises(RuntimeErrorInfo, match="invalid_policy"):
                await runtime.respond_approval(observers[1][2], "accept")
            assert observers[1][1].rpc.replies == [(1, {"decision": "decline"})]
        finally:
            for observer, _, _ in observers:
                await observer.aclose()
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_failed_recovery_retains_record_and_disables_admission(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        await runtime.aclose()
        registry.create(session)
        registry.record_worker(session.session_id, "abandoned")

        async def failing_remove(name, *, confirmed):
            raise RuntimeError("Docker unavailable")

        replacement = DockerSessionRuntime(
            registry,
            image="test",
            transport=None,
            model="test-model",
            worker_factory=factory,
            worker_remover=failing_remove,
        )
        try:
            with pytest.raises(RuntimeError):
                await replacement.recover()
            assert not replacement.ready
            assert not replacement.attests_inactive(runtime.generation)
            assert registry.list_workers()[0].worker_name == "abandoned"
            with pytest.raises(RuntimeErrorInfo, match="unavailable"):
                await replacement.create_thread(session)
        finally:
            await replacement.aclose()
            registry.close()

    asyncio.run(run())


def test_terminal_inspection_keeps_items_instead_of_empty_cached_snapshot(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            external = await runtime.start_turn(session, "turn", "hello")
            worker = factory.workers[-1]
            worker.rpc.turn["items"] = [
                {"type": "agentMessage", "id": "answer", "text": "retained"}
            ]
            worker.rpc.complete()
            _ = [event async for event in runtime.observe(session, "turn")]
            observation = await runtime.inspect_turn(session, external)
            assert observation.items[0]["text"] == "retained"
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


@pytest.mark.parametrize("history_retained", [False, True])
def test_snapshot_cache_releases_old_outputs_and_restores_the_exact_evicted_turn(
    tmp_path, monkeypatch, history_retained
):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        observations = []
        turns = []
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            for index in range(40):
                external = await runtime.start_turn(session, f"local-{index}", "hello")
                worker = factory.workers[-1]
                worker.rpc.turn["items"] = [
                    {"type": "agentMessage", "id": "answer", "text": f"answer-{index}"}
                ]
                turns.append(dict(worker.rpc.turn, status="completed"))
                worker.rpc.complete()
                _ = [event async for event in runtime.observe(session, f"local-{index}")]
                observations.append(weakref.ref(await runtime.inspect_turn(session, external)))
            gc.collect()
            assert observations[0]() is None, "completed outputs must not accumulate forever"
            assert observations[-1]() is not None

            original = FakeRpc.call

            async def historical(rpc, method, params, **kwargs):
                result = await original(rpc, method, params, **kwargs)
                if method == "thread/read":
                    # A restored Codex history contains both old and recent turns.
                    result["thread"]["turns"] = turns if history_retained else turns[-1:]
                return result

            monkeypatch.setattr(FakeRpc, "call", historical)
            recovered = await runtime.inspect_turn(session, turns[0]["id"])
            if history_retained:
                assert recovered.external_turn_id == turns[0]["id"]
                assert recovered.items[0]["text"] == "answer-0"
            else:
                assert recovered.state == "unknown"
                assert recovered.external_turn_id is None
                assert recovered.items == ()
            restored = factory.workers[-1]
            assert restored.settings["home_archive"] is not None
            assert all(method != "turn/start" for method, _params in restored.rpc.calls)
            assert restored.closed
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


@pytest.mark.parametrize("close_runtime", [False, True])
def test_finished_lifecycle_releases_cached_outputs(tmp_path, close_runtime):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            external = await runtime.start_turn(session, "turn", "hello")
            factory.workers[-1].rpc.complete()
            _ = [event async for event in runtime.observe(session, "turn")]
            snapshot = weakref.ref(await runtime.inspect_turn(session, external))
            assert snapshot() is not None
            if close_runtime:
                await runtime.aclose()
            else:
                await runtime.end_thread(session)
            gc.collect()
            assert snapshot() is None
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


@pytest.mark.parametrize("reconcile", [False, True])
def test_missing_historical_snapshot_cannot_shadow_a_later_completed_turn(
    tmp_path, monkeypatch, reconcile
):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            missing = await runtime.inspect_turn(session, "absent-historical-turn")
            assert missing.state == "unknown"
            assert missing.external_turn_id is None

            external = await runtime.start_turn(session, "new-turn", "continue")
            worker = factory.workers[-1]
            worker.rpc.turn["items"] = [
                {"type": "agentMessage", "id": "answer", "text": "latest answer"}
            ]
            worker.rpc.complete()
            completed = dict(worker.rpc.turn)
            _ = [event async for event in runtime.observe(session, "new-turn")]
            original = FakeRpc.call

            async def restored_history(rpc, method, params, **kwargs):
                result = await original(rpc, method, params, **kwargs)
                if method == "thread/read":
                    result["thread"]["turns"] = [completed]
                return result

            monkeypatch.setattr(FakeRpc, "call", restored_history)
            latest = (
                await runtime.reconcile(session, None)
                if reconcile
                else await runtime.inspect_turn(session, None)
            )
            assert latest.state == "completed"
            assert latest.external_turn_id == external
            assert latest.items[0]["text"] == "latest answer"
            assert not registry.list_workers()
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_unknown_historical_snapshot_does_not_hide_later_confirmed_history(tmp_path, monkeypatch):
    async def run():
        runtime, registry, _factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            historical = {"id": "historical", "status": "inProgress", "items": []}
            original = FakeRpc.call

            async def restored_history(rpc, method, params, **kwargs):
                result = await original(rpc, method, params, **kwargs)
                if method == "thread/read":
                    result["thread"]["turns"] = [historical]
                return result

            monkeypatch.setattr(FakeRpc, "call", restored_history)
            assert (await runtime.inspect_turn(session, "historical")).state == "unknown"
            historical["status"] = "completed"
            confirmed = await runtime.inspect_turn(session, "historical")
            assert confirmed.state == "completed"
            assert confirmed.external_turn_id == "historical"
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_shutdown_cancels_waiting_admission_even_when_worker_removal_fails(tmp_path):
    async def run():
        runtime, registry, factory, first = await setup(tmp_path, max_workers=1)
        second = replace(first, session_id="session-2")
        pending = None
        try:
            first = replace(first, external_thread_id=await runtime.create_thread(first))
            second = replace(second, external_thread_id=await runtime.create_thread(second))
            await runtime.start_turn(first, "turn", "hello")
            pending = asyncio.create_task(runtime.start_turn(second, "next", "hello"))
            await asyncio.sleep(0.01)
            factory.fail_close = True
            with pytest.raises(RuntimeErrorInfo):
                await runtime.aclose()
            assert pending.done()
            assert pending.cancelled()
        finally:
            factory.fail_close = False
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_failed_registration_never_clears_an_unrelated_record(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        registry.create(session)
        registry.record_worker(session.session_id, "preserve-uncertain-worker")
        try:
            with pytest.raises(ValueError, match="removed first"):
                await runtime.create_thread(session)
            assert registry.get(session.session_id).worker_name == "preserve-uncertain-worker"
            assert not runtime.ready
            assert factory.workers[0].closed
            assert "enter" not in factory.workers[0].log
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_cancellation_during_worker_entry_removes_record_and_restores_capacity(
    tmp_path, monkeypatch
):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path, max_workers=1)
        entered = asyncio.Event()
        original = FakeWorker.__aenter__

        async def blocked_enter(worker):
            await original(worker)
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(FakeWorker, "__aenter__", blocked_enter)
        task = asyncio.create_task(runtime.create_thread(session))
        await entered.wait()
        assert registry.list_workers()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert factory.workers[0].closed
        assert not registry.list_workers()
        monkeypatch.setattr(FakeWorker, "__aenter__", original)
        try:
            assert await runtime.create_thread(session)
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_attach_existing_turn_never_dispatches_input_again(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            external = await runtime.start_turn(session, "turn", "hello")
            observation = await runtime.attach_turn(session, "turn", external)
            assert observation.state == "active"
            worker = factory.workers[-1]
            assert sum(method == "turn/start" for method, _ in worker.rpc.calls) == 1
            with pytest.raises(RuntimeErrorInfo, match="invalid_event"):
                await runtime.attach_turn(session, "forged-turn", external)
            worker.rpc.complete()
            _ = [event async for event in runtime.observe(session, "turn")]
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_abandoned_active_snapshot_is_never_cached_as_running(tmp_path, monkeypatch):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        original = FakeRpc.call
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))

            async def historical_active(rpc, method, params, **kwargs):
                if method == "thread/read":
                    rpc.turn = {"id": "abandoned", "status": "inProgress", "items": []}
                return await original(rpc, method, params, **kwargs)

            monkeypatch.setattr(FakeRpc, "call", historical_active)
            for _ in range(2):
                observation = await runtime.inspect_turn(session, "abandoned")
                assert observation.state == "unknown"
            assert all(worker.closed for worker in factory.workers)
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_terminal_snapshot_disagreement_cannot_publish_completed(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            await runtime.start_turn(session, "turn", "hello")
            worker = factory.workers[-1]
            worker.rpc.complete()
            worker.rpc.turn["status"] = "failed"
            with pytest.raises(RuntimeErrorInfo, match="invalid_event"):
                await anext(runtime.observe(session, "turn"))
            assert worker.closed
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_missing_historical_turn_does_not_return_or_close_the_running_turn(tmp_path):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            await runtime.start_turn(session, "turn", "hello")
            observation = await runtime.inspect_turn(session, "another-external-turn")
            assert observation.state == "unknown"
            assert observation.external_turn_id is None
            assert observation.items == ()
            assert not factory.workers[-1].closed
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


@pytest.mark.parametrize("history_retained", [False, True])
def test_evicted_historical_inspection_preserves_newer_active_worker(
    tmp_path, monkeypatch, history_retained
):
    async def run():
        runtime, registry, factory, session = await setup(tmp_path)
        turns = []
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            for index in range(33):
                await runtime.start_turn(session, f"old-{index}", "hello")
                worker = factory.workers[-1]
                worker.rpc.turn["items"] = [
                    {"type": "agentMessage", "id": "answer", "text": f"answer-{index}"}
                ]
                worker.rpc.complete()
                turns.append(dict(worker.rpc.turn))
                _ = [event async for event in runtime.observe(session, f"old-{index}")]
            current = await runtime.start_turn(session, "current", "continue")
            worker = factory.workers[-1]
            original_call = worker.rpc.call

            async def historical(method, params, **kwargs):
                result = await original_call(method, params, **kwargs)
                if method == "thread/read" and history_retained:
                    result["thread"]["turns"] = [*turns, worker.rpc.turn]
                return result

            monkeypatch.setattr(worker.rpc, "call", historical)
            observation = await runtime.inspect_turn(session, turns[0]["id"])
            if history_retained:
                assert observation.state == "completed"
                assert observation.external_turn_id == turns[0]["id"]
                assert observation.items[0]["text"] == "answer-0"
            else:
                assert observation.state == "unknown"
                assert observation.external_turn_id is None
                assert observation.items == ()
            assert not worker.closed
            assert worker.relay.turn == "current"
            assert "checkpoint" not in worker.log
            assert registry.list_workers()[0].worker_name == worker.name
            assert len(factory.workers) == 35  # Initial thread, 33 old turns, current turn.
            assert (await runtime.inspect_turn(session, current)).state == "active"
            worker.rpc.complete()
            events = [event async for event in runtime.observe(session, "current")]
            assert events[-1]["external_turn_id"] == current
            assert events[-1]["payload"]["state"] == "completed"
            assert not registry.list_workers()
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())


def test_thread_read_rejects_a_response_for_another_thread(tmp_path, monkeypatch):
    async def run():
        runtime, registry, _factory, session = await setup(tmp_path)
        original = FakeRpc.call
        try:
            session = replace(session, external_thread_id=await runtime.create_thread(session))
            external = await runtime.start_turn(session, "turn", "hello")

            async def wrong_thread(rpc, method, params, **kwargs):
                result = await original(rpc, method, params, **kwargs)
                if method == "thread/read":
                    result["thread"]["id"] = "forged-thread"
                return result

            monkeypatch.setattr(FakeRpc, "call", wrong_thread)
            with pytest.raises(RuntimeErrorInfo, match="invalid_event"):
                await runtime.inspect_turn(session, external)
        finally:
            await runtime.aclose()
            registry.close()

    asyncio.run(run())
