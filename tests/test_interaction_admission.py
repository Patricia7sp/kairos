import asyncio

import pytest

from kairos_integration.admission import InteractionAdmissionGate
from kairos_integration.interaction_contract import InteractionServiceUnavailableError


@pytest.mark.anyio
async def test_drain_rejects_new_turns_and_waits_for_active_turn() -> None:
    """Removing drain state or active accounting would race resource shutdown."""
    gate = InteractionAdmissionGate()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def active_turn() -> None:
        async with gate.admit():
            entered.set()
            await release.wait()

    turn = asyncio.create_task(active_turn())
    await entered.wait()
    drain = asyncio.create_task(gate.drain())
    await asyncio.sleep(0)

    with pytest.raises(InteractionServiceUnavailableError) as caught:
        async with gate.admit():
            pass
    assert caught.value.error_kind == "unavailable"
    assert caught.value.message == "serviço de interação indisponível"
    assert caught.value.retryable is True
    assert not drain.done()

    release.set()
    await turn
    await drain


@pytest.mark.anyio
async def test_drain_never_reopens_admission_after_active_turn_finishes() -> None:
    """Resetting to OPEN after the active count reaches zero admits work during close retry."""
    gate = InteractionAdmissionGate()

    await gate.drain()

    with pytest.raises(InteractionServiceUnavailableError):
        async with gate.admit():
            pass
