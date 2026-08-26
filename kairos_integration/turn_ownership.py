"""Ownership assíncrono e renovável de turnos por conversa."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeVar
from uuid import uuid4

from kairos_state.writes import is_busy_error

logger = logging.getLogger(__name__)

__all__ = [
    "AsyncTurnLeaseBackend",
    "LeaseAcquireResult",
    "LeaseRefreshResult",
    "LeaseReleaseResult",
    "SQLiteAsyncTurnLeaseBackend",
    "SessionTurnOwnership",
    "TurnLeaseLostError",
]


class LeaseAcquireResult(StrEnum):
    ACQUIRED = "acquired"
    CONTENDED = "contended"


class LeaseRefreshResult(StrEnum):
    REFRESHED = "refreshed"
    CONTENDED = "contended"
    LOST = "lost"


class LeaseReleaseResult(StrEnum):
    RELEASED = "released"
    CONTENDED = "contended"


class TurnLeaseLostError(RuntimeError):
    """O turno perdeu ownership durável e não pode continuar persistindo."""


class AsyncTurnLeaseBackend(Protocol):
    async def try_acquire(
        self,
        conversation_id: str,
        source: str,
        holder: str,
        *,
        ttl_seconds: float,
        now: float,
    ) -> LeaseAcquireResult: ...

    async def refresh(
        self,
        conversation_id: str,
        holder: str,
        *,
        ttl_seconds: float,
        now: float,
    ) -> LeaseRefreshResult: ...

    async def release(self, conversation_id: str, holder: str) -> LeaseReleaseResult: ...


class SQLiteAsyncTurnLeaseBackend:
    """Lease SQLite via conexões curtas criadas somente dentro do worker."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    async def try_acquire(
        self,
        conversation_id: str,
        source: str,
        holder: str,
        *,
        ttl_seconds: float,
        now: float,
    ) -> LeaseAcquireResult:
        return await self._offload(
            self._try_acquire_sync,
            conversation_id,
            source,
            holder,
            ttl_seconds,
            now,
        )

    async def refresh(
        self,
        conversation_id: str,
        holder: str,
        *,
        ttl_seconds: float,
        now: float,
    ) -> LeaseRefreshResult:
        return await self._offload(
            self._refresh_sync,
            conversation_id,
            holder,
            ttl_seconds,
            now,
        )

    async def release(self, conversation_id: str, holder: str) -> LeaseReleaseResult:
        return await self._offload(self._release_sync, conversation_id, holder)

    async def _offload(self, function: Callable[..., T], *args: object) -> T:
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=0")
        return connection

    def _try_acquire_sync(
        self,
        conversation_id: str,
        source: str,
        holder: str,
        ttl_seconds: float,
        now: float,
    ) -> LeaseAcquireResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO sessions(id, source, started_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (conversation_id, source, now),
            )
            cursor = connection.execute(
                "INSERT INTO session_turn_leases"
                "(conversation_id, holder, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "holder = excluded.holder, acquired_at = excluded.acquired_at, "
                "expires_at = excluded.expires_at "
                "WHERE session_turn_leases.expires_at <= ? "
                "OR session_turn_leases.holder = excluded.holder",
                (conversation_id, holder, now, now + ttl_seconds, now),
            )
            connection.commit()
            return (
                LeaseAcquireResult.ACQUIRED if cursor.rowcount > 0 else LeaseAcquireResult.CONTENDED
            )
        except sqlite3.OperationalError as exc:
            connection.rollback()
            if is_busy_error(exc):
                return LeaseAcquireResult.CONTENDED
            raise
        finally:
            connection.close()

    def _refresh_sync(
        self,
        conversation_id: str,
        holder: str,
        ttl_seconds: float,
        now: float,
    ) -> LeaseRefreshResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE session_turn_leases SET expires_at = ? "
                "WHERE conversation_id = ? AND holder = ? AND expires_at > ?",
                (now + ttl_seconds, conversation_id, holder, now),
            )
            connection.commit()
            return LeaseRefreshResult.REFRESHED if cursor.rowcount > 0 else LeaseRefreshResult.LOST
        except sqlite3.OperationalError as exc:
            connection.rollback()
            if is_busy_error(exc):
                return LeaseRefreshResult.CONTENDED
            raise
        finally:
            connection.close()

    def _release_sync(self, conversation_id: str, holder: str) -> LeaseReleaseResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM session_turn_leases WHERE conversation_id = ? AND holder = ?",
                (conversation_id, holder),
            )
            connection.commit()
            return LeaseReleaseResult.RELEASED
        except sqlite3.OperationalError as exc:
            connection.rollback()
            if is_busy_error(exc):
                return LeaseReleaseResult.CONTENDED
            raise
        finally:
            connection.close()


@dataclass
class _TurnQueueEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]
T = TypeVar("T")


class SessionTurnOwnership:
    """FIFO local, lease durável renovado e cleanup em todos os terminais."""

    def __init__(
        self,
        backend: AsyncTurnLeaseBackend | None,
        *,
        poll_interval: float = 0.01,
        ttl_seconds: float = 300,
        refresh_interval: float | None = None,
        clock: Clock = time.time,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if refresh_interval is None:
            refresh_interval = ttl_seconds / 3
        if poll_interval < 0:
            raise ValueError("turn_lease_poll_interval não pode ser negativo")
        if ttl_seconds <= 0:
            raise ValueError("turn_lease_ttl_seconds deve ser positivo")
        if refresh_interval <= 0 or refresh_interval >= ttl_seconds:
            raise ValueError("turn_lease_refresh_interval deve estar entre zero e o TTL")
        self._backend = backend
        self._poll_interval = poll_interval
        self._ttl_seconds = ttl_seconds
        self._refresh_interval = refresh_interval
        self._clock = clock
        self._sleep = sleep
        self._entries: dict[str, _TurnQueueEntry] = {}
        self._entries_guard = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, conversation_id: str, source: str) -> AsyncIterator[None]:
        async with self._entries_guard:
            entry = self._entries.setdefault(conversation_id, _TurnQueueEntry())
            entry.users += 1

        local_acquired = False
        durable_acquired = False
        heartbeat: asyncio.Task[None] | None = None
        lease_lost = asyncio.Event()
        holder = uuid4().hex
        owner = asyncio.current_task()
        try:
            await entry.lock.acquire()
            local_acquired = True
            if self._backend is not None:
                expires_at = await self._acquire_durable(conversation_id, source, holder)
                durable_acquired = True
                heartbeat = asyncio.create_task(
                    self._heartbeat(
                        conversation_id,
                        holder,
                        owner,
                        lease_lost,
                        expires_at,
                    ),
                    name=f"kairos-turn-lease-heartbeat:{conversation_id}",
                )
            try:
                yield
            except asyncio.CancelledError as exc:
                if lease_lost.is_set():
                    raise TurnLeaseLostError(
                        f"ownership do turno {conversation_id!r} foi perdido"
                    ) from exc
                raise
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
            try:
                if durable_acquired:
                    await self._release_durable(conversation_id, holder)
            finally:
                if local_acquired:
                    entry.lock.release()
                async with self._entries_guard:
                    entry.users -= 1
                    if entry.users == 0 and self._entries.get(conversation_id) is entry:
                        del self._entries[conversation_id]

    async def _acquire_durable(self, conversation_id: str, source: str, holder: str) -> float:
        while True:
            attempt_time = self._clock()
            task = asyncio.create_task(
                self._backend.try_acquire(
                    conversation_id,
                    source,
                    holder,
                    ttl_seconds=self._ttl_seconds,
                    now=attempt_time,
                )
            )
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                result = await task
                if result is LeaseAcquireResult.ACQUIRED:
                    await self._release_durable(conversation_id, holder)
                raise
            if result is LeaseAcquireResult.ACQUIRED:
                return attempt_time + self._ttl_seconds
            await self._sleep(self._poll_interval)

    async def _heartbeat(
        self,
        conversation_id: str,
        holder: str,
        owner: asyncio.Task[object] | None,
        lease_lost: asyncio.Event,
        expires_at: float,
    ) -> None:
        try:
            while True:
                await self._sleep(self._refresh_interval)
                while True:
                    refresh_time = self._clock()
                    result = await self._backend.refresh(
                        conversation_id,
                        holder,
                        ttl_seconds=self._ttl_seconds,
                        now=refresh_time,
                    )
                    if result is LeaseRefreshResult.REFRESHED:
                        expires_at = refresh_time + self._ttl_seconds
                        break
                    if result is LeaseRefreshResult.LOST or refresh_time >= expires_at:
                        lease_lost.set()
                        if owner is not None:
                            owner.cancel()
                        return
                    await self._sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("falha ao renovar lease do turno %s: %s", conversation_id, exc)
        lease_lost.set()
        if owner is not None:
            owner.cancel()

    async def _release_durable(self, conversation_id: str, holder: str) -> None:
        while True:
            result = await self._backend.release(conversation_id, holder)
            if result is LeaseReleaseResult.RELEASED:
                return
            await self._sleep(self._poll_interval)
