"""Admission and drain coordination for public interaction streams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum

from kairos_integration.interaction_contract import InteractionServiceUnavailableError

__all__ = ["AdmissionState", "InteractionAdmissionGate"]


class AdmissionState(StrEnum):
    OPEN = "open"
    DRAINING = "draining"
    CLOSED = "closed"


class InteractionAdmissionGate:
    """Reject new turns once drain starts and wait for admitted turns to leave."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._state = AdmissionState.OPEN
        self._active = 0

    def _start_draining(self) -> None:
        """Close admission at cleanup reservation, before its async task starts."""
        if self._state is AdmissionState.OPEN:
            self._state = AdmissionState.DRAINING

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._state is not AdmissionState.OPEN:
                raise InteractionServiceUnavailableError()
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                if self._active == 0:
                    self._condition.notify_all()

    async def drain(self) -> None:
        async with self._condition:
            self._start_draining()
            await self._condition.wait_for(lambda: self._active == 0)

    async def mark_closed(self) -> None:
        async with self._condition:
            self._state = AdmissionState.CLOSED
            self._condition.notify_all()
