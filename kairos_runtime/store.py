"""Async facade for runtime persistence with thread-local SQLite connections."""

from __future__ import annotations

import asyncio
import contextvars
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from kairos_runtime.contracts import RuntimeCapabilities, RuntimeEvent, RuntimeSession
from kairos_runtime.redaction import sanitize_payload

__all__ = ["RuntimeStore"]

_T = TypeVar("_T")


class RuntimeStore:
    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self._db_path = Path(db_path)

    async def owned(self, turn_id, holder, generation, operation, *args, allow_expired=False):
        return await self._call(
            lambda repo: repo.owned(
                turn_id, holder, generation, operation, *args, allow_expired=allow_expired
            )
        )

    async def adopt_runtime_turn(
        self,
        turn_id,
        previous_holder,
        previous_generation,
        holder,
        *,
        confirmed_inactive,
        ttl,
        clock,
    ):
        return await self._call(
            lambda repo: repo.adopt_runtime_turn(
                turn_id,
                previous_holder,
                previous_generation,
                holder,
                confirmed_inactive=confirmed_inactive,
                ttl=ttl,
                clock=clock,
            )
        )

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
        safe = sanitize_payload(kind, dict(payload))
        return await self._call(lambda repo: repo.append(turn_id, event_id, kind, safe))

    async def events_after(self, session_id: str, cursor: str | None) -> tuple[RuntimeEvent, ...]:
        return await self._call(lambda repo: repo.events_after(session_id, cursor))

    async def session_details(self, session_id: str) -> dict:
        return await self._call(lambda repo: repo.session_details(session_id))

    async def session_state(self, session_id: str, state: str) -> None:
        await self._call(lambda repo: repo.session_state(session_id, state))

    async def unbound_sessions(self) -> tuple[dict, ...]:
        return await self._call(lambda repo: repo.unbound_sessions())

    async def get_turn(self, turn_id: str) -> dict:
        return await self._call(lambda repo: repo.get_turn(turn_id))

    async def nonterminal_turns(self) -> tuple[dict, ...]:
        return await self._call(lambda repo: repo.nonterminal_turns())

    async def terminal_without_event(self) -> tuple[dict, ...]:
        return await self._call(lambda repo: repo.terminal_without_event())

    async def transition(self, turn_id: str, expected: str, target: str) -> bool:
        return await self._call(lambda repo: repo.transition(turn_id, expected, target))

    async def dispatch(self, turn_id: str, generation: str) -> bool:
        return await self._call(lambda repo: repo.dispatch(turn_id, generation))

    async def confirm_dispatch(self, turn_id: str, external_turn_id: str) -> None:
        await self._call(lambda repo: repo.confirm_dispatch(turn_id, external_turn_id))

    async def uncertain(self, turn_id: str) -> None:
        await self._call(lambda repo: repo.uncertain(turn_id))

    async def lose_turn(
        self, turn_id: str, holder: str, generation: int, content: str, usage: dict | None
    ) -> bool:
        return await self._call(
            lambda repo: repo.lose_turn(turn_id, holder, generation, content, usage)
        )

    async def save_approval(
        self,
        turn_id: str,
        process_generation: str,
        external_request_id: str,
        external_item_id: str | None,
        request: dict,
    ) -> str:
        return await self._call(
            lambda repo: repo.save_approval(
                turn_id, process_generation, external_request_id, external_item_id, request
            )
        )

    async def get_approval(self, approval_id: str) -> dict:
        return await self._call(lambda repo: repo.get_approval(approval_id))

    async def decide_approval(self, approval_id: str, decision: str) -> bool:
        return await self._call(lambda repo: repo.decide_approval(approval_id, decision))

    async def acknowledge_approval(self, approval_id: str, receipt: Mapping | None) -> None:
        await self._call(lambda repo: repo.acknowledge_approval(approval_id, receipt))

    async def finish(self, turn_id: str, state: str, content: str, usage: dict | None) -> None:
        await self._call(lambda repo: repo.finish(turn_id, state, content, usage))

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
        self,
        turn_id: str,
        holder: str,
        generation: int,
        clock: Callable[[], float],
        *,
        confirmed_inactive: bool = False,
    ) -> bool:
        return await self._call(
            lambda repo: repo.release_runtime_turn(
                turn_id,
                holder,
                generation,
                clock,
                confirmed_inactive=confirmed_inactive,
            )
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
