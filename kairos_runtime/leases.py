"""Durable ownership and FIFO admission for Agent Runtime turns."""

from __future__ import annotations

import time
from collections.abc import Callable

from .store import RuntimeStore

__all__ = [
    "DEFAULT_RENEW_INTERVAL_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "RuntimeLeaseManager",
]

DEFAULT_TTL_SECONDS = 30.0
DEFAULT_RENEW_INTERVAL_SECONDS = 10.0
Clock = Callable[[], float]


class RuntimeLeaseManager:
    """Coordinate the session and canonical-directory leases as one unit."""

    def __init__(self, store: RuntimeStore, clock: Clock = time.time) -> None:
        self._store = store
        self._clock = clock

    async def enqueue(self, turn_id: str) -> None:
        await self._store.enqueue_runtime_turn(turn_id, self._clock)

    async def adopt(
        self,
        turn_id: str,
        previous_holder: str,
        previous_generation: int,
        holder: str,
        *,
        confirmed_inactive: bool = False,
    ) -> int | None:
        """Fence an existing turn's observer; caller attests predecessor inactivity."""
        self._validate_owner(holder, DEFAULT_TTL_SECONDS)
        self._validate_owner(previous_holder, DEFAULT_TTL_SECONDS)
        self._validate_generation(previous_generation)
        if type(confirmed_inactive) is not bool:
            raise ValueError("confirmed_inactive must be boolean")
        return await self._store.adopt_runtime_turn(
            turn_id,
            previous_holder,
            previous_generation,
            holder,
            confirmed_inactive=confirmed_inactive,
            ttl=DEFAULT_TTL_SECONDS,
            clock=self._clock,
        )

    async def claim(
        self, turn_id: str, holder: str, ttl: float = DEFAULT_TTL_SECONDS
    ) -> int | None:
        self._validate_owner(holder, ttl)
        return await self._store.claim_runtime_turn(turn_id, holder, ttl, self._clock)

    async def renew(
        self,
        turn_id: str,
        holder: str,
        generation: int,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> bool:
        self._validate_owner(holder, ttl)
        self._validate_generation(generation)
        return await self._store.renew_runtime_turn(turn_id, holder, generation, ttl, self._clock)

    async def release(
        self,
        turn_id: str,
        holder: str,
        generation: int,
        *,
        confirmed_inactive: bool = False,
    ) -> bool:
        self._validate_owner(holder, DEFAULT_TTL_SECONDS)
        self._validate_generation(generation)
        if type(confirmed_inactive) is not bool:
            raise ValueError("confirmed_inactive must be boolean")
        return await self._store.release_runtime_turn(
            turn_id,
            holder,
            generation,
            self._clock,
            confirmed_inactive=confirmed_inactive,
        )

    async def quarantine(self, turn_id: str) -> None:
        await self._store.quarantine_runtime_turn(turn_id, self._clock)

    async def cancel_queued(self, turn_id: str) -> bool:
        """Cancel a waiting turn without contacting the external runtime."""

        return await self._store.cancel_queued_runtime_turn(turn_id, self._clock)

    @staticmethod
    def _validate_owner(holder: str, ttl: float) -> None:
        if not isinstance(holder, str) or not holder:
            raise ValueError("runtime lease holder must be non-empty")
        if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or ttl <= 0:
            raise ValueError("runtime lease TTL must be positive")

    @staticmethod
    def _validate_generation(generation: int) -> None:
        if type(generation) is not int or generation <= 0:
            raise ValueError("runtime lease generation must be positive")
