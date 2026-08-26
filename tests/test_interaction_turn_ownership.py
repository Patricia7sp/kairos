from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

import pytest

from kairos_integration import InteractionEnvelope, InteractionEvent
from kairos_integration.interaction_service import InteractionService
from kairos_integration.turn_ownership import (
    LeaseAcquireOutcome,
    LeaseAcquireResult,
    LeaseRefreshOutcome,
    LeaseRefreshResult,
    LeaseReleaseResult,
    SQLiteAsyncTurnLeaseBackend,
    TurnLeaseLostError,
)
from kairos_providers import (
    ModelSelectionContext,
    ProviderEvent,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
)
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository


class Resolver:
    def resolve(self, _context: ModelSelectionContext) -> ResolvedModelSelection:
        return ResolvedModelSelection(
            ref=ProviderModelRef("fake", "chat-1"),
            reason=SelectionReason.GLOBAL_DEFAULT,
        )


class ContextLoader:
    def load(self, _envelope: InteractionEnvelope) -> ModelSelectionContext:
        return ModelSelectionContext(global_default=ProviderModelRef("fake", "chat-1"))


class Gateway:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def create_adapter(self, _ref):
        return self.adapter


class ImmediateAdapter:
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.requests = []

    async def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield ProviderEvent(kind="text_delta", text=self.text)
        yield ProviderEvent(kind="finish", finish_reason="stop")


class BlockingAdapter(ImmediateAdapter):
    def __init__(self, text: str = "ok") -> None:
        super().__init__(text)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        yield ProviderEvent(kind="text_delta", text=self.text)
        yield ProviderEvent(kind="finish", finish_reason="stop")


class CancellationTrackingAdapter:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.requests = []
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.stopped_at = None

    async def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped_at = self.clock()
            self.stopped.set()
        yield  # pragma: no cover - mantém a assinatura de async generator


class OverlapCheckingAdapter(ImmediateAdapter):
    def __init__(self, previous: CancellationTrackingAdapter) -> None:
        super().__init__("sucessora")
        self.previous = previous
        self.overlapped = None

    async def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.overlapped = not self.previous.stopped.is_set()
        async for event in super().stream(request):
            yield event


class FastStreamingAdapter(ImmediateAdapter):
    def __init__(self, event_count: int) -> None:
        super().__init__("")
        self.event_count = event_count
        self.emitted = 0

    async def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        for index in range(self.event_count):
            self.emitted += 1
            yield ProviderEvent(kind="text_delta", text=str(index))
        await asyncio.Event().wait()


class ProducerAbort(BaseException):
    pass


class BaseExceptionAdapter(ImmediateAdapter):
    def __init__(self) -> None:
        super().__init__("")
        self.raised = asyncio.Event()

    async def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        self.raised.set()
        raise ProducerAbort("producer-abort")
        yield  # pragma: no cover - mantém a assinatura de async generator


class FirstStreamBlocksAdapter(ImmediateAdapter):
    def __init__(self) -> None:
        super().__init__("")
        self.calls = 0
        self.first_started = asyncio.Event()

    async def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await asyncio.Event().wait()
        yield ProviderEvent(kind="finish", finish_reason="stop")


class LeaseAssertingSessions(SessionRepository):
    """The durable row must exist before normal session ensure executes."""

    def ensure(self, session_id: str, source: str = "web", **kwargs) -> str:
        row = self._conn.execute(
            "SELECT holder FROM session_turn_leases WHERE conversation_id = ?",
            (session_id,),
        ).fetchone()
        assert row is not None and row["holder"]
        return super().ensure(session_id, source=source, **kwargs)


class ManualTime:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        self.value += delay
        await asyncio.sleep(0)


class ControlledTime:
    def __init__(self, value: float = 0) -> None:
        self.value = value
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def now(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        deadline = self.value + delay
        future = asyncio.get_running_loop().create_future()
        waiter = (deadline, future)
        self._waiters.append(waiter)
        try:
            await future
        finally:
            self._waiters.remove(waiter)

    def advance_to(self, value: float) -> None:
        assert value >= self.value
        self.value = value
        for deadline, future in tuple(self._waiters):
            if deadline <= value and not future.done():
                future.set_result(None)


class RecordingBackend:
    def __init__(self, clock, refresh_result: bool = True) -> None:
        self.clock = clock
        self.refresh_result = refresh_result
        self.acquisitions = []
        self.refreshes = []
        self.releases = []
        self.release_called = asyncio.Event()
        self.refresh_called = asyncio.Event()
        self.hold_refresh = asyncio.Event()

    async def try_acquire(self, conversation_id, source, holder, *, ttl_seconds):
        now = self.clock()
        self.acquisitions.append((conversation_id, source, holder, ttl_seconds, now))
        return LeaseAcquireOutcome(LeaseAcquireResult.ACQUIRED, now + ttl_seconds)

    async def refresh(self, conversation_id, holder, *, ttl_seconds):
        now = self.clock()
        self.refreshes.append((conversation_id, holder, ttl_seconds, now))
        self.refresh_called.set()
        if self.refresh_result:
            await self.hold_refresh.wait()
        if self.refresh_result:
            return LeaseRefreshOutcome(LeaseRefreshResult.REFRESHED, now + ttl_seconds)
        return LeaseRefreshOutcome(LeaseRefreshResult.LOST)

    async def release(self, conversation_id, holder):
        self.releases.append((conversation_id, holder))
        self.release_called.set()
        return LeaseReleaseResult.RELEASED


class PermanentReleaseContentionBackend(RecordingBackend):
    async def release(self, conversation_id, holder):
        self.releases.append((conversation_id, holder))
        self.release_called.set()
        return LeaseReleaseResult.CONTENDED


class GatedLossBackend(RecordingBackend):
    def __init__(self, clock) -> None:
        super().__init__(clock)
        self.lose = asyncio.Event()

    async def refresh(self, conversation_id, holder, *, ttl_seconds):
        now = self.clock()
        self.refreshes.append((conversation_id, holder, ttl_seconds, now))
        self.refresh_called.set()
        await self.lose.wait()
        return LeaseRefreshOutcome(LeaseRefreshResult.LOST)


def envelope(conversation_id: str = "s1", content: str = "oi") -> InteractionEnvelope:
    return InteractionEnvelope(conversation_id=conversation_id, source="web", content=content)


def service(
    connection: sqlite3.Connection,
    adapter,
    *,
    backend=None,
    sessions=None,
    usage=None,
    clock=None,
    sleep=None,
    ttl: float = 300,
    refresh_interval: float | None = None,
    release_max_attempts: int = 5,
) -> InteractionService:
    return InteractionService(
        gateway=Gateway(adapter),
        resolver=Resolver(),
        context_loader=ContextLoader(),
        sessions=sessions or SessionRepository(connection),
        messages=MessageRepository(connection),
        usage=usage or UsageRepository(connection),
        turn_leases=backend,
        turn_lease_poll_interval=0,
        turn_lease_ttl_seconds=ttl,
        turn_lease_refresh_interval=refresh_interval,
        turn_lease_clock=clock,
        turn_lease_sleep=sleep,
        turn_lease_release_max_attempts=release_max_attempts,
    )


async def collect(stream: AsyncIterator[InteractionEvent]) -> list[InteractionEvent]:
    return [event async for event in stream]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_two_service_graphs_share_first_session_creation_and_fifo_history(tmp_path: Path):
    db_path = tmp_path / "state.db"
    first_connection = connect(db_path)
    initialize_schema(first_connection)
    second_connection = connect(db_path)
    first_adapter = BlockingAdapter("primeira resposta")
    second_adapter = ImmediateAdapter("segunda resposta")
    first = service(
        first_connection,
        first_adapter,
        backend=SQLiteAsyncTurnLeaseBackend(db_path),
        sessions=LeaseAssertingSessions(first_connection),
    )
    second = service(
        second_connection,
        second_adapter,
        backend=SQLiteAsyncTurnLeaseBackend(db_path),
        sessions=LeaseAssertingSessions(second_connection),
    )
    try:
        first_task = asyncio.create_task(collect(first.stream(envelope(content="primeira"))))
        await first_adapter.started.wait()
        second_task = asyncio.create_task(collect(second.stream(envelope(content="segunda"))))
        await asyncio.sleep(0.02)

        assert not second_task.done()
        first_adapter.release.set()
        await asyncio.gather(first_task, second_task)

        assert [
            (row["role"], row["payload"])
            for row in MessageRepository(second_connection).for_api("s1")
        ] == [
            ("user", "primeira"),
            ("assistant", "primeira resposta"),
            ("user", "segunda"),
            ("assistant", "segunda resposta"),
        ]
        assert [
            part.value
            for message in second_adapter.requests[0].messages
            for part in message.content
        ] == [
            "primeira",
            "primeira resposta",
            "segunda",
        ]
    finally:
        first_connection.close()
        second_connection.close()


@pytest.mark.anyio
async def test_two_service_graphs_race_new_session_inside_durable_ownership(tmp_path: Path):
    db_path = tmp_path / "state.db"
    first_connection = connect(db_path)
    initialize_schema(first_connection)
    second_connection = connect(db_path)
    first = service(
        first_connection,
        ImmediateAdapter("resposta-a"),
        backend=SQLiteAsyncTurnLeaseBackend(db_path),
        sessions=LeaseAssertingSessions(first_connection),
    )
    second = service(
        second_connection,
        ImmediateAdapter("resposta-b"),
        backend=SQLiteAsyncTurnLeaseBackend(db_path),
        sessions=LeaseAssertingSessions(second_connection),
    )
    try:
        await asyncio.gather(
            collect(first.stream(envelope(content="pergunta-a"))),
            collect(second.stream(envelope(content="pergunta-b"))),
        )

        assert (
            first_connection.execute("SELECT COUNT(*) FROM sessions WHERE id = 's1'").fetchone()[0]
            == 1
        )
        transcript = [
            (row["role"], row["payload"])
            for row in MessageRepository(first_connection).for_api("s1")
        ]
        assert transcript in (
            [
                ("user", "pergunta-a"),
                ("assistant", "resposta-a"),
                ("user", "pergunta-b"),
                ("assistant", "resposta-b"),
            ],
            [
                ("user", "pergunta-b"),
                ("assistant", "resposta-b"),
                ("user", "pergunta-a"),
                ("assistant", "resposta-a"),
            ],
        )
    finally:
        first_connection.close()
        second_connection.close()


@pytest.mark.anyio
async def test_heartbeat_refreshes_lease_during_long_turn_without_real_wait(tmp_path: Path):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    timer = ControlledTime()
    backend = RecordingBackend(timer.now, refresh_result=True)
    adapter = BlockingAdapter()
    interaction = service(
        connection,
        adapter,
        backend=backend,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    try:
        task = asyncio.create_task(collect(interaction.stream(envelope())))
        await adapter.started.wait()
        while not timer._waiters:
            await asyncio.sleep(0)
        timer.advance_to(2)
        await backend.refresh_called.wait()

        assert backend.refreshes[0][-1] == 2
        backend.hold_refresh.set()
        adapter.release.set()
        assert (await task)[-1].kind == "turn_end"
        assert len(backend.releases) == 1
    finally:
        connection.close()


@pytest.mark.anyio
async def test_lost_heartbeat_aborts_turn_before_assistant_persistence(tmp_path: Path):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    timer = ManualTime()
    backend = RecordingBackend(timer.now, refresh_result=False)
    adapter = BlockingAdapter()
    interaction = service(
        connection,
        adapter,
        backend=backend,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    try:
        with pytest.raises(TurnLeaseLostError, match="s1"):
            await collect(interaction.stream(envelope()))

        rows = MessageRepository(connection).for_api("s1")
        assert [(row["role"], row["payload"]) for row in rows] == [("user", "oi")]
        assert len(backend.releases) == 1
    finally:
        connection.close()


@pytest.mark.anyio
async def test_sqlite_refresh_extends_expiry_without_wall_clock_wait(tmp_path: Path):
    db_path = tmp_path / "state.db"
    connection = connect(db_path)
    initialize_schema(connection)
    timer = ManualTime()
    timer.value = 100
    backend = SQLiteAsyncTurnLeaseBackend(db_path, clock=timer.now)
    try:
        acquired = await backend.try_acquire("s1", "web", "first", ttl_seconds=6)
        assert acquired.result is LeaseAcquireResult.ACQUIRED
        assert acquired.expires_at == 106
        timer.value = 104
        refreshed = await backend.refresh("s1", "first", ttl_seconds=6)
        assert refreshed.result is LeaseRefreshResult.REFRESHED
        assert refreshed.expires_at == 110
        timer.value = 107
        contended = await backend.try_acquire("s1", "web", "second", ttl_seconds=6)
        assert contended.result is LeaseAcquireResult.CONTENDED
        timer.value = 111
        reacquired = await backend.try_acquire("s1", "web", "second", ttl_seconds=6)
        assert reacquired.result is LeaseAcquireResult.ACQUIRED
        assert reacquired.expires_at == 117
    finally:
        connection.close()


@pytest.mark.anyio
async def test_sqlite_acquire_samples_deadline_inside_delayed_worker(tmp_path: Path):
    db_path = tmp_path / "state.db"
    connection = connect(db_path)
    initialize_schema(connection)
    timer = ManualTime()
    backend = SQLiteAsyncTurnLeaseBackend(db_path, clock=timer.now)
    real_attempt = backend._try_acquire_sync
    entered = threading.Event()
    release = threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        release.wait(timeout=1)
        return real_attempt(*args, **kwargs)

    backend._try_acquire_sync = delayed
    try:
        task = asyncio.create_task(backend.try_acquire("s1", "web", "holder", ttl_seconds=6))
        while not entered.is_set():
            await asyncio.sleep(0)
        timer.value = 100
        release.set()
        outcome = await task
        assert outcome.expires_at == 106

        expires_at = connection.execute(
            "SELECT expires_at FROM session_turn_leases WHERE conversation_id = 's1'"
        ).fetchone()[0]
        assert expires_at == 106
    finally:
        release.set()
        connection.close()


@pytest.mark.anyio
async def test_sqlite_refresh_samples_deadline_inside_delayed_worker(tmp_path: Path):
    db_path = tmp_path / "state.db"
    connection = connect(db_path)
    initialize_schema(connection)
    timer = ManualTime()
    timer.value = 100
    backend = SQLiteAsyncTurnLeaseBackend(db_path, clock=timer.now)
    assert (
        await backend.try_acquire("s1", "web", "holder", ttl_seconds=20)
    ).result is LeaseAcquireResult.ACQUIRED
    real_refresh = backend._refresh_sync
    entered = threading.Event()
    release = threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        release.wait(timeout=1)
        return real_refresh(*args, **kwargs)

    backend._refresh_sync = delayed
    try:
        task = asyncio.create_task(backend.refresh("s1", "holder", ttl_seconds=20))
        while not entered.is_set():
            await asyncio.sleep(0)
        timer.value = 110
        release.set()
        outcome = await task
        assert outcome.result is LeaseRefreshResult.REFRESHED
        assert outcome.expires_at == 130

        expires_at = connection.execute(
            "SELECT expires_at FROM session_turn_leases WHERE conversation_id = 's1'"
        ).fetchone()[0]
        assert expires_at == 130
    finally:
        release.set()
        connection.close()


@pytest.mark.anyio
async def test_acquire_rejects_deadline_expired_while_worker_result_is_delayed(tmp_path: Path):
    db_path = tmp_path / "state.db"
    connection = connect(db_path)
    initialize_schema(connection)
    timer = ControlledTime()
    backend = SQLiteAsyncTurnLeaseBackend(db_path, clock=timer.now)
    real_attempt = backend._try_acquire_sync
    committed = threading.Event()
    return_result = threading.Event()
    calls = 0

    def delayed_after_commit(*args, **kwargs):
        nonlocal calls
        calls += 1
        outcome = real_attempt(*args, **kwargs)
        if calls == 1:
            committed.set()
            return_result.wait(timeout=1)
        return outcome

    backend._try_acquire_sync = delayed_after_commit
    interaction = service(
        connection,
        ImmediateAdapter(),
        backend=backend,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    task = asyncio.create_task(collect(interaction.stream(envelope())))
    try:
        while not committed.is_set():
            await asyncio.sleep(0)
        timer.advance_to(7)
        return_result.set()

        assert (await task)[-1].kind == "turn_end"
        assert calls == 2
    finally:
        return_result.set()
        task.cancel()
        connection.close()


@pytest.mark.anyio
async def test_refresh_rejects_deadline_expired_while_worker_result_is_delayed(tmp_path: Path):
    db_path = tmp_path / "state.db"
    connection = connect(db_path)
    initialize_schema(connection)
    timer = ManualTime()
    backend = SQLiteAsyncTurnLeaseBackend(db_path, clock=timer.now)
    real_refresh = backend._refresh_sync
    committed = threading.Event()
    return_result = threading.Event()

    def delayed_after_commit(*args, **kwargs):
        outcome = real_refresh(*args, **kwargs)
        committed.set()
        return_result.wait(timeout=1)
        return outcome

    backend._refresh_sync = delayed_after_commit
    interaction = service(
        connection,
        BlockingAdapter(),
        backend=backend,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    task = asyncio.create_task(collect(interaction.stream(envelope())))
    try:
        while not committed.is_set():
            await asyncio.sleep(0)
        timer.value = 9
        return_result.set()

        with pytest.raises(TurnLeaseLostError):
            await task
    finally:
        return_result.set()
        task.cancel()
        connection.close()


@pytest.mark.anyio
async def test_refresh_blocked_past_deadline_stops_owner_before_successor_provider(  # noqa: PLR0915
    tmp_path: Path,
):
    db_path = tmp_path / "state.db"
    first_connection = connect(db_path)
    initialize_schema(first_connection)
    second_connection = connect(db_path)
    timer = ControlledTime(100)
    first_backend = SQLiteAsyncTurnLeaseBackend(db_path, clock=timer.now)
    second_backend = SQLiteAsyncTurnLeaseBackend(db_path, clock=timer.now)
    real_refresh = first_backend._refresh_sync
    refresh_entered = threading.Event()
    allow_refresh = threading.Event()
    refresh_finished = threading.Event()

    def blocked_refresh(*args, **kwargs):
        refresh_entered.set()
        allow_refresh.wait()
        try:
            return real_refresh(*args, **kwargs)
        finally:
            refresh_finished.set()

    first_backend._refresh_sync = blocked_refresh
    first_adapter = CancellationTrackingAdapter(timer.now)
    second_adapter = OverlapCheckingAdapter(first_adapter)
    first_usage = UsageRepository(first_connection)
    second_usage = UsageRepository(second_connection)
    first = service(
        first_connection,
        first_adapter,
        backend=first_backend,
        usage=first_usage,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    second = service(
        second_connection,
        second_adapter,
        backend=second_backend,
        usage=second_usage,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    first_task = asyncio.create_task(collect(first.stream(envelope(content="primeiro"))))
    try:
        await first_adapter.started.wait()
        while not timer._waiters:
            await asyncio.sleep(0)
        timer.advance_to(102)
        while not refresh_entered.is_set():
            await asyncio.sleep(0)

        timer.advance_to(107)
        for _ in range(100):
            if first_adapter.stopped.is_set():
                break
            await asyncio.sleep(0)
        stopped_before_refresh_return = first_adapter.stopped.is_set()

        successor_events = await asyncio.wait_for(
            collect(second.stream(envelope(content="segundo"))), timeout=0.5
        )
        rows_before_old_refresh_returns = [
            (row["role"], row["payload"])
            for row in MessageRepository(second_connection).for_api("s1")
        ]

        allow_refresh.set()
        with pytest.raises(TurnLeaseLostError):
            await asyncio.wait_for(first_task, timeout=0.5)

        assert stopped_before_refresh_return
        assert first_adapter.stopped_at == 107
        assert second_adapter.overlapped is False
        assert successor_events[-1].kind == "turn_end"
        assert rows_before_old_refresh_returns == [
            ("user", "primeiro"),
            ("user", "segundo"),
            ("assistant", "sucessora"),
        ]
        assert first_usage.pending_count() == 0
        assert second_usage.pending_count() == 1
        assert refresh_finished.is_set()
        assert (
            second_connection.execute(
                "SELECT holder FROM session_turn_leases WHERE conversation_id = 's1'"
            ).fetchone()
            is None
        )
    finally:
        allow_refresh.set()
        first_task.cancel()
        with suppress(asyncio.CancelledError, TurnLeaseLostError):
            await first_task
        first_connection.close()
        second_connection.close()


@pytest.mark.anyio
async def test_lease_loss_waits_at_iterator_boundary_without_cancelling_consumer(tmp_path: Path):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    timer = ManualTime()
    backend = RecordingBackend(timer.now, refresh_result=False)
    adapter = BlockingAdapter()
    interaction = service(
        connection,
        adapter,
        backend=backend,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    stream = interaction.stream(envelope())
    consumer_paused = asyncio.Event()
    resume_consumer = asyncio.Event()

    async def consume() -> None:
        assert (await anext(stream)).kind == "turn_start"
        consumer_paused.set()
        await resume_consumer.wait()
        with pytest.raises(TurnLeaseLostError):
            await anext(stream)

    task = asyncio.create_task(consume())
    try:
        await consumer_paused.wait()
        await backend.release_called.wait()

        assert not task.done()
        resume_consumer.set()
        await task
        assert len(backend.releases) == 1
    finally:
        resume_consumer.set()
        await stream.aclose()
        connection.close()


@pytest.mark.anyio
async def test_fast_provider_is_demand_paced_and_loss_precedes_unrequested_deltas(
    tmp_path: Path,
):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    timer = ControlledTime(100)
    backend = GatedLossBackend(timer.now)
    adapter = FastStreamingAdapter(event_count=1_000)
    interaction = service(
        connection,
        adapter,
        backend=backend,
        clock=timer.now,
        sleep=timer.sleep,
        ttl=6,
        refresh_interval=2,
    )
    stream = interaction.stream(envelope())
    try:
        assert (await anext(stream)).kind == "turn_start"
        assert (await anext(stream)).kind == "delta"

        assert adapter.emitted == 1
        while not timer._waiters:
            await asyncio.sleep(0)
        timer.advance_to(102)
        await backend.refresh_called.wait()
        backend.lose.set()
        await backend.release_called.wait()

        with pytest.raises(TurnLeaseLostError):
            await anext(stream)
        assert adapter.emitted == 1
        assert [
            (row["role"], row["payload"]) for row in MessageRepository(connection).for_api("s1")
        ] == [("user", "oi")]
    finally:
        backend.lose.set()
        await stream.aclose()
        connection.close()


@pytest.mark.anyio
async def test_non_exception_base_exception_is_observable_without_waiting_for_queue(
    tmp_path: Path,
):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    adapter = BaseExceptionAdapter()
    interaction = service(connection, adapter)
    task = asyncio.create_task(collect(interaction.stream(envelope())))
    try:
        await adapter.raised.wait()
        with pytest.raises(ProducerAbort, match="producer-abort"):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, ProducerAbort):
            await task
        connection.close()


@pytest.mark.anyio
async def test_permanent_release_contention_does_not_hang_cancel_or_local_fifo(
    tmp_path: Path,
):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    timer = ManualTime()
    backend = PermanentReleaseContentionBackend(timer.now)
    adapter = FirstStreamBlocksAdapter()
    interaction = service(
        connection,
        adapter,
        backend=backend,
        clock=timer.now,
        sleep=timer.sleep,
        release_max_attempts=2,
    )
    first = asyncio.create_task(collect(interaction.stream(envelope(content="primeiro"))))
    await adapter.first_started.wait()
    second = asyncio.create_task(collect(interaction.stream(envelope(content="segundo"))))
    first.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await asyncio.wait_for(second, timeout=0.5))[-1].kind == "turn_end"
        assert len(backend.releases) == 4
    finally:
        first.cancel()
        second.cancel()
        connection.close()


@pytest.mark.anyio
async def test_slow_contended_acquire_does_not_block_unrelated_coroutine(tmp_path: Path):
    db_path = tmp_path / "state.db"
    connection = connect(db_path)
    initialize_schema(connection)
    backend = SQLiteAsyncTurnLeaseBackend(db_path)
    real_attempt = backend._try_acquire_sync
    worker_entered = threading.Event()
    worker_release = threading.Event()
    different_session_finished = threading.Event()
    fallback_used = threading.Event()
    attempts = 0

    def contended_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            worker_entered.set()
            worker_release.wait(timeout=1)
            return LeaseAcquireOutcome(LeaseAcquireResult.CONTENDED)
        return real_attempt(*args, **kwargs)

    backend._try_acquire_sync = contended_once

    def release_worker() -> None:
        if not different_session_finished.wait(timeout=0.2):
            fallback_used.set()
        worker_release.set()

    releaser = threading.Thread(target=release_worker, daemon=True)
    releaser.start()
    interaction = service(connection, ImmediateAdapter(), backend=backend)
    try:
        task = asyncio.create_task(collect(interaction.stream(envelope("s1"))))
        while not worker_entered.is_set():
            await asyncio.sleep(0)
        other_events = await asyncio.wait_for(
            collect(interaction.stream(envelope("s2"))), timeout=1
        )
        different_session_finished.set()

        assert (await asyncio.wait_for(task, timeout=1))[-1].kind == "turn_end"
        assert other_events[-1].kind == "turn_end"
        assert attempts >= 2
        assert not fallback_used.is_set()
    finally:
        worker_release.set()
        releaser.join(timeout=1)
        connection.close()
