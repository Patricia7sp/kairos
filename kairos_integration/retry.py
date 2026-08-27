"""Retry policy for provider attempts that have not exposed assistant output."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from kairos_providers import ProviderError

__all__ = ["RetryPolicy"]


class _Accumulator(Protocol):
    text: str
    reasoning: str
    tool_calls: list[object]


async def _no_sleep(_delay: float) -> None:
    """Keep retries immediate unless a composition root opts into a delay."""


class RetryPolicy:
    """Allows one safe retry and injects time dependencies for deterministic use."""

    def __init__(
        self,
        *,
        max_attempts: int = 2,
        initial_backoff: float = 0.0,
        max_backoff: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = _no_sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_attempts <= 2:
            raise ValueError("max_attempts deve estar entre 1 e 2")
        if initial_backoff < 0 or max_backoff < 0:
            raise ValueError("backoff não pode ser negativo")
        self._max_attempts = max_attempts
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._sleep = sleep
        self._clock = clock

    def can_retry(self, error: ProviderError, accumulator: _Accumulator) -> bool:
        """Never replay text, reasoning, or tool work already accepted by a client."""
        return error.retryable and not (
            accumulator.text or accumulator.reasoning or accumulator.tool_calls
        )

    def has_attempts_remaining(self, attempts: int) -> bool:
        return attempts < self._max_attempts

    async def backoff(self, retry_number: int) -> None:
        """Wait a bounded delay, using injected time so tests never sleep for real."""
        delay = min(self._initial_backoff * (2 ** (retry_number - 1)), self._max_backoff)
        deadline = self._clock() + delay
        await self._sleep(max(0.0, deadline - self._clock()))
