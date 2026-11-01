"""Regressões e contratos de cancelamento que acompanham o PR S7497."""

import asyncio
import threading

import pytest

from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_integration.skill_catalog_turn import _local_io
from kairos_integration.turn_ownership import (
    LeaseAcquireResult,
    LeaseRefreshOutcome,
    LeaseRefreshResult,
    LeaseReleaseResult,
    SessionTurnOwnership,
    SQLiteAsyncTurnLeaseBackend,
)
from kairos_providers._async_cleanup import complete_cleanup, run_persistent_cleanup
from kairos_runtime.supervisor import CodexSupervisor
from kairos_state import connect, initialize_schema


def initialized_database(path):
    connection = connect(path)
    initialize_schema(connection)
    return connection


async def cancel_during_thread_work(invoker, effect):
    started, release, finished = (threading.Event() for _ in range(3))

    def operation():
        started.set()
        if not release.wait(5):
            raise TimeoutError("test worker was not released")
        effect()
        finished.set()

    task = asyncio.create_task(invoker(operation))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel("first")
        await asyncio.sleep(0)
        task.cancel("second")
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_catalog_local_io_waits_for_real_write_before_cancellation(tmp_path):
    target = tmp_path / "asset.txt"
    asyncio.run(cancel_during_thread_work(_local_io, lambda: target.write_text("completed")))
    assert target.read_text() == "completed"


def test_persistence_call_finishes_sqlite_write_before_cancellation(tmp_path):
    path = tmp_path / "state.db"
    connection = initialized_database(path)

    async def run():
        persistence = SQLiteAsyncInteractionPersistence(path)

        def write():
            persistence._connection.execute(
                "INSERT INTO sessions(id, source, started_at) VALUES ('cancel-test', 'test', 'now')"
            )
            persistence._connection.commit()

        try:
            await cancel_during_thread_work(persistence._call, write)
        finally:
            await persistence.aclose()

    try:
        asyncio.run(run())
        assert connection.execute("SELECT id FROM sessions WHERE id='cancel-test'").fetchone()
    finally:
        connection.close()


def test_persistent_cleanup_returns_external_cancellation_after_drain():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def cleanup():
            started.set()
            await release.wait()

        waiter = asyncio.create_task(run_persistent_cleanup(cleanup, task_name="contract-test"))
        try:
            await started.wait()
            waiter.cancel("caller")
            await asyncio.sleep(0)
            assert not waiter.done()
            release.set()
            outcome = await waiter
            assert outcome.error is None
            assert isinstance(outcome.cancellation, asyncio.CancelledError)
            assert str(outcome.cancellation) == "caller"
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(run())


def test_supervisor_consumes_only_the_expected_stderr_child_cancel():
    async def run():
        child = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        assert await CodexSupervisor._finish_stderr(child) is None
        assert child.cancelled()
        assert not asyncio.current_task().cancelling()

    asyncio.run(run())


def test_acquire_propagates_body_cancellation():
    async def run():
        ownership = SessionTurnOwnership(None)

        async def owner():
            async with ownership.acquire("body", "test"):
                asyncio.current_task().cancel()
                await asyncio.sleep(0)

        task = asyncio.create_task(owner())
        with pytest.raises(asyncio.CancelledError):
            await task
        async with ownership.acquire("body", "test"):
            pass

    asyncio.run(run())


def test_refresh_reraises_original_cancellation_after_stopping_refresh():
    async def run():
        entered, stopped = asyncio.Event(), asyncio.Event()

        class Backend:
            async def refresh(self, *args, **kwargs):
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        ownership = SessionTurnOwnership(Backend(), clock=lambda: 0)
        task = asyncio.create_task(
            ownership._refresh_before_deadline("refresh", "holder", None, asyncio.Event(), 50)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()

    asyncio.run(run())


def test_acquire_preserves_cancel_after_heartbeat_completion(tmp_path):
    path = tmp_path / "state.db"
    connection = initialized_database(path)

    async def run():
        ownership = SessionTurnOwnership(SQLiteAsyncTurnLeaseBackend(path), clock=lambda: 0)

        async def owner():
            async with ownership.acquire("heartbeat", "test"):
                current = asyncio.current_task()
                heartbeat = next(
                    task
                    for task in asyncio.all_tasks()
                    if task.get_name() == "kairos-turn-lease-heartbeat:heartbeat"
                )
                heartbeat.add_done_callback(lambda completed: current.cancel())

        task = asyncio.create_task(owner())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (
            connection.execute(
                "SELECT holder FROM session_turn_leases WHERE conversation_id='heartbeat'"
            ).fetchone()
            is None
        )

    try:
        asyncio.run(run())
    finally:
        connection.close()


def test_refresh_preserves_cancel_when_deadline_finishes():
    async def run():
        entered = asyncio.Event()
        task = None

        async def sleep(delay):
            asyncio.current_task().add_done_callback(lambda completed: task.cancel())
            entered.set()
            await asyncio.Event().wait()

        class Backend:
            async def refresh(self, *args, **kwargs):
                await entered.wait()
                return LeaseRefreshOutcome(LeaseRefreshResult.REFRESHED, 100)

        ownership = SessionTurnOwnership(Backend(), clock=lambda: 0, sleep=sleep)
        task = asyncio.create_task(
            ownership._refresh_before_deadline("deadline", "holder", None, asyncio.Event(), 50)
        )
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_repeated_cancel_cannot_release_successor_before_durable_drain(tmp_path):
    path = tmp_path / "state.db"
    connection = initialized_database(path)

    async def run():
        draining, release, released, successor_entered = (asyncio.Event() for _ in range(4))

        class Backend(SQLiteAsyncTurnLeaseBackend):
            async def release(self, *args, **kwargs):
                draining.set()
                await release.wait()
                result = await super().release(*args, **kwargs)
                released.set()
                return result

        ownership = SessionTurnOwnership(Backend(path))

        async def first_owner():
            async with ownership.acquire("drain", "test"):
                pass

        async def next_owner():
            async with ownership.acquire("drain", "test"):
                assert released.is_set()
                successor_entered.set()

        first = asyncio.create_task(first_owner())
        second = None
        try:
            await draining.wait()
            second = asyncio.create_task(next_owner())
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done()
            assert not successor_entered.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            await asyncio.wait_for(second, 2)
            assert successor_entered.is_set()
        finally:
            release.set()
            for task in [first, second]:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "terminal",
    [
        RuntimeError("not terminal"),
        GeneratorExit(),
    ],
)
def test_cleanup_failure_keeps_already_delivered_cancellation_as_cause(terminal):
    async def run():
        async def fail():
            raise terminal

        async def owner():
            try:
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            finally:
                await complete_cleanup(fail, task_name="inherited-cancel-test")

        task = asyncio.create_task(owner())
        with pytest.raises(type(terminal)) as caught:
            await task
        assert isinstance(caught.value.__cause__, asyncio.CancelledError)

    asyncio.run(run())


@pytest.mark.parametrize("terminal_kind", ["lost", "generator"])
def test_cleanup_cancel_preserves_existing_terminal_error(tmp_path, terminal_kind):
    from kairos_integration.turn_ownership import TurnLeaseLostError

    path = tmp_path / "state.db"
    connection = initialized_database(path)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        terminal = (
            TurnLeaseLostError("lost test lease") if terminal_kind == "lost" else GeneratorExit()
        )

        class Backend(SQLiteAsyncTurnLeaseBackend):
            async def release(self, *args, **kwargs):
                entered.set()
                await release.wait()
                return await super().release(*args, **kwargs)

        ownership = SessionTurnOwnership(Backend(path))

        async def owner():
            async with ownership.acquire("terminal", "test"):
                raise terminal

        task = asyncio.create_task(owner())
        try:
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(type(terminal)) as caught:
                await task
            assert caught.value is terminal
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        connection.close()


def test_exhausted_release_keeps_durable_gate_until_expiry(tmp_path):
    path = tmp_path / "state.db"
    connection = initialized_database(path)

    async def run():
        now = [100]

        class ContendedRelease(SQLiteAsyncTurnLeaseBackend):
            async def release(self, *args, **kwargs):
                return LeaseReleaseResult.CONTENDED

        backend = ContendedRelease(path, clock=lambda: now[0])
        ownership = SessionTurnOwnership(
            backend, clock=lambda: now[0], ttl_seconds=6, release_max_attempts=2, poll_interval=0
        )
        async with ownership.acquire("expiry", "test"):
            pass
        assert (
            await backend.try_acquire("expiry", "test", "next", ttl_seconds=6)
        ).result is LeaseAcquireResult.CONTENDED
        now[0] = 107
        assert (
            await backend.try_acquire("expiry", "test", "next", ttl_seconds=6)
        ).result is LeaseAcquireResult.ACQUIRED
        await SQLiteAsyncTurnLeaseBackend.release(backend, "expiry", "next")

    try:
        asyncio.run(run())
    finally:
        connection.close()


@pytest.mark.parametrize("terminal_kind", ["lost", "generator"])
def test_cleanup_failure_preserves_terminal_and_logs_only_error_type(
    tmp_path, caplog, terminal_kind
):
    from kairos_integration.turn_ownership import TurnLeaseLostError

    path = tmp_path / "state.db"
    connection = initialized_database(path)

    async def run():
        started = asyncio.Event()
        terminal = TurnLeaseLostError("lease lost") if terminal_kind == "lost" else GeneratorExit()

        class Ownership(SessionTurnOwnership):
            async def _heartbeat(self, *args):
                try:
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    raise RuntimeError("sensitive test sentinel")

        ownership = Ownership(SQLiteAsyncTurnLeaseBackend(path))

        async def owner():
            async with ownership.acquire("failure", "test"):
                await started.wait()
                raise terminal

        with pytest.raises(type(terminal)) as caught:
            await asyncio.create_task(owner())
        assert caught.value is terminal
        assert (
            connection.execute(
                "SELECT holder FROM session_turn_leases WHERE conversation_id='failure'"
            ).fetchone()
            is None
        )

    try:
        asyncio.run(run())
        record = next(record for record in caplog.records if record.event == "turn.cleanup_failed")
        assert record.error_type == "RuntimeError"
        assert "sensitive test sentinel" not in caplog.text
        assert record.exc_info is None
    finally:
        connection.close()
