from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from kairos_integration import InteractionEnvelope, InteractionEvent
from kairos_integration.interaction_service import InteractionService
from kairos_integration.turn_ownership import (
    LeaseAcquireResult,
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


class RecordingBackend:
    def __init__(self, refresh_result: bool = True) -> None:
        self.refresh_result = refresh_result
        self.acquisitions = []
        self.refreshes = []
        self.releases = []
        self.refresh_called = asyncio.Event()
        self.hold_refresh = asyncio.Event()

    async def try_acquire(self, conversation_id, source, holder, *, ttl_seconds, now):
        self.acquisitions.append((conversation_id, source, holder, ttl_seconds, now))
        return LeaseAcquireResult.ACQUIRED

    async def refresh(self, conversation_id, holder, *, ttl_seconds, now):
        self.refreshes.append((conversation_id, holder, ttl_seconds, now))
        self.refresh_called.set()
        if self.refresh_result:
            await self.hold_refresh.wait()
        return LeaseRefreshResult.REFRESHED if self.refresh_result else LeaseRefreshResult.LOST

    async def release(self, conversation_id, holder):
        self.releases.append((conversation_id, holder))
        return LeaseReleaseResult.RELEASED


def envelope(conversation_id: str = "s1", content: str = "oi") -> InteractionEnvelope:
    return InteractionEnvelope(conversation_id=conversation_id, source="web", content=content)


def service(
    connection: sqlite3.Connection,
    adapter,
    *,
    backend=None,
    sessions=None,
    clock=None,
    sleep=None,
    ttl: float = 300,
    refresh_interval: float | None = None,
) -> InteractionService:
    return InteractionService(
        gateway=Gateway(adapter),
        resolver=Resolver(),
        context_loader=ContextLoader(),
        sessions=sessions or SessionRepository(connection),
        messages=MessageRepository(connection),
        usage=UsageRepository(connection),
        turn_leases=backend,
        turn_lease_poll_interval=0,
        turn_lease_ttl_seconds=ttl,
        turn_lease_refresh_interval=refresh_interval,
        turn_lease_clock=clock,
        turn_lease_sleep=sleep,
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
    timer = ManualTime()
    backend = RecordingBackend(refresh_result=True)
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
        await backend.refresh_called.wait()

        assert backend.refreshes[0][-1] == 2
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
    backend = RecordingBackend(refresh_result=False)
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
    backend = SQLiteAsyncTurnLeaseBackend(db_path)
    try:
        assert (
            await backend.try_acquire("s1", "web", "first", ttl_seconds=6, now=100)
            is LeaseAcquireResult.ACQUIRED
        )
        assert (
            await backend.refresh("s1", "first", ttl_seconds=6, now=104)
            is LeaseRefreshResult.REFRESHED
        )
        assert (
            await backend.try_acquire("s1", "web", "second", ttl_seconds=6, now=107)
            is LeaseAcquireResult.CONTENDED
        )
        assert (
            await backend.try_acquire("s1", "web", "second", ttl_seconds=6, now=111)
            is LeaseAcquireResult.ACQUIRED
        )
    finally:
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
            return LeaseAcquireResult.CONTENDED
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
