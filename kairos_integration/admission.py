"""Admission and drain coordination for public interaction streams."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

from kairos_integration.interaction_contract import InteractionServiceUnavailableError

__all__ = ["AdmissionState", "InteractionAdmissionGate"]


class AdmissionState(StrEnum):
    OPEN = "open"
    DRAINING = "draining"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class _DrainWaiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[None]


def _complete_waiter(waiter: _DrainWaiter) -> None:
    """Complete one drain future from its owning event-loop thread."""
    if not waiter.future.done():
        waiter.future.set_result(None)


class InteractionAdmissionGate:
    """Reject new turns once drain starts and wait for admitted turns to leave."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = AdmissionState.OPEN
        self._active = 0
        self._waiters: list[_DrainWaiter] = []

    def _start_draining(self) -> None:
        """Close admission at cleanup reservation, before its async task starts."""
        with self._lock:
            self._start_draining_locked()

    def _start_draining_locked(self) -> None:
        if self._state is AdmissionState.OPEN:
            self._state = AdmissionState.DRAINING

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        with self._lock:
            if self._state is not AdmissionState.OPEN:
                raise InteractionServiceUnavailableError()
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
                waiters = self._take_waiters_locked() if self._active == 0 else ()
            self._wake(waiters)

    async def drain(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._start_draining_locked()
            if self._active == 0:
                return
            waiter = _DrainWaiter(loop, loop.create_future())
            self._waiters.append(waiter)
        try:
            await waiter.future
        finally:
            with self._lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)

    async def mark_closed(self) -> None:
        with self._lock:
            self._state = AdmissionState.CLOSED
            waiters = self._take_waiters_locked() if self._active == 0 else ()
        self._wake(waiters)

    def _take_waiters_locked(self) -> tuple[_DrainWaiter, ...]:
        waiters = tuple(self._waiters)
        self._waiters.clear()
        return waiters

    @staticmethod
    def _wake(waiters: tuple[_DrainWaiter, ...]) -> None:
        for waiter in waiters:
            try:
                waiter.loop.call_soon_threadsafe(_complete_waiter, waiter)
            except RuntimeError:
                # A canceled drain may close its loop while an active turn exits.
                if not waiter.loop.is_closed():
                    raise
