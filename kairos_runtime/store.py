"""Async facade for runtime persistence with thread-local SQLite connections."""

from __future__ import annotations

import asyncio
import contextvars
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from kairos_runtime.contracts import RuntimeCapabilities, RuntimeEvent, RuntimeSession

__all__ = ["RuntimeStore"]

_T = TypeVar("_T")


class RuntimeStore:
    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self._db_path = Path(db_path)

    async def create_session(
        self,
        session: RuntimeSession,
        source: str,
        parent_session_id: str | None = None,
        *,
        allowed_directories: tuple[str, ...],
        broad_enabled: bool = False,
        consent: bool = False,
    ) -> str:
        return await self._call(
            lambda repo: repo.create_session(
                session,
                source,
                parent_session_id,
                allowed_directories=allowed_directories,
                broad_enabled=broad_enabled,
                consent=consent,
            )
        )

    async def bind_thread(
        self, session_id: str, thread_id: str, capabilities: RuntimeCapabilities
    ) -> None:
        await self._call(lambda repo: repo.bind_thread(session_id, thread_id, capabilities))

    async def get_session(self, session_id: str) -> RuntimeSession:
        return await self._call(lambda repo: repo.get_session(session_id))

    async def admit(self, session_id: str, key: str, content: str) -> str:
        return await self._call(lambda repo: repo.admit(session_id, key, content))

    async def append(
        self, turn_id: str, event_id: str, kind: str, payload: Mapping[str, Any]
    ) -> RuntimeEvent:
        return await self._call(lambda repo: repo.append(turn_id, event_id, kind, payload))

    async def events_after(self, session_id: str, cursor: str | None) -> tuple[RuntimeEvent, ...]:
        return await self._call(lambda repo: repo.events_after(session_id, cursor))

    async def enqueue_runtime_turn(self, turn_id: str, clock: Callable[[], float]) -> None:
        await self._call(lambda repo: repo.enqueue_runtime_turn(turn_id, clock))

    async def claim_runtime_turn(
        self, turn_id: str, holder: str, ttl: float, clock: Callable[[], float]
    ) -> int | None:
        return await self._call(lambda repo: repo.claim_runtime_turn(turn_id, holder, ttl, clock))

    async def renew_runtime_turn(
        self,
        turn_id: str,
        holder: str,
        generation: int,
        ttl: float,
        clock: Callable[[], float],
    ) -> bool:
        return await self._call(
            lambda repo: repo.renew_runtime_turn(turn_id, holder, generation, ttl, clock)
        )

    async def release_runtime_turn(
        self, turn_id: str, holder: str, generation: int, clock: Callable[[], float]
    ) -> bool:
        return await self._call(
            lambda repo: repo.release_runtime_turn(turn_id, holder, generation, clock)
        )

    async def quarantine_runtime_turn(self, turn_id: str, clock: Callable[[], float]) -> None:
        await self._call(lambda repo: repo.quarantine_runtime_turn(turn_id, clock))

    async def cancel_queued_runtime_turn(self, turn_id: str, clock: Callable[[], float]) -> bool:
        return await self._call(lambda repo: repo.cancel_queued_runtime_turn(turn_id, clock))

    async def _call(self, operation: Callable[[Any], _T]) -> _T:
        def run() -> _T:
            from kairos_state import (
                SCHEMA_VERSION,
                connect,
                initialize_schema,
                read_schema_version,
            )
            from kairos_state.repositories.runtime import RuntimeRepository

            connection = connect(self._db_path)
            try:
                if read_schema_version(connection) != SCHEMA_VERSION:
                    initialize_schema(connection)
                return operation(RuntimeRepository(connection))
            finally:
                connection.close()

        context = contextvars.copy_context()
        worker = asyncio.get_running_loop().run_in_executor(None, context.run, run)
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError as exc:
                if worker.cancelled():
                    raise
                cancellation = exc
                continue
            except BaseException:
                if cancellation is not None:
                    raise cancellation from None
                raise
            if cancellation is not None:
                raise cancellation
            return result
