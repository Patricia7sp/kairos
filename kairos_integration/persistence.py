"""Async boundary for interaction transcript and usage persistence."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from kairos_providers._async_cleanup import (
    AsyncCleanupCoordinator,
    run_persistent_cleanup,
)
from kairos_state import connect
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository

__all__ = ["SQLiteAsyncInteractionPersistence"]

T = TypeVar("T")


class SQLiteAsyncInteractionPersistence:
    """Single ordered worker whose SQLite connection is created in that worker."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="kairos-interaction-persistence",
        )
        self._connection = None
        self._sessions = None
        self._messages = None
        self._usage = None
        self._executor.submit(self._initialize).result()
        self._close = AsyncCleanupCoordinator(task_name="kairos-interaction-persistence-close")

    def _initialize(self) -> None:
        connection = connect(self.db_path)
        self._connection = connection
        self._sessions = SessionRepository(connection)
        self._messages = MessageRepository(connection)
        self._usage = UsageRepository(connection)

    @property
    def usage(self) -> UsageRepository:
        assert self._usage is not None
        return self._usage

    async def ensure(self, session_id: str, *, source: str) -> str:
        assert self._sessions is not None
        return await self._call(self._sessions.ensure, session_id, source=source)

    async def append_turn_message(self, *args: Any, **kwargs: Any) -> int:
        assert self._messages is not None
        return await self._call(self._messages.append_turn_message, *args, **kwargs)

    async def append_message(self, *args: Any, **kwargs: Any) -> int:
        assert self._messages is not None
        return await self._call(self._messages.append, *args, **kwargs)

    async def flush_usage(self) -> int:
        return await self._call(self.usage.flush)

    async def aclose(self) -> None:
        await self._close.run(self._close_attempt)

    async def _close_attempt(self) -> None:
        await self.flush_usage()
        connection = self._connection
        if connection is not None:
            await self._call(connection.close)
            self._connection = None
        self._executor.shutdown(wait=True)

    async def _call(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if self._connection is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        concurrent = self._executor.submit(partial(function, *args, **kwargs))
        wrapped = asyncio.wrap_future(concurrent)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError as cancellation:

            async def wait_for_worker() -> None:
                await asyncio.shield(wrapped)

            outcome = await run_persistent_cleanup(
                wait_for_worker,
                task_name="kairos-interaction-persistence-operation",
            )
            if outcome.error is not None:
                raise outcome.error from cancellation
            raise cancellation
