from __future__ import annotations

import asyncio

import pytest

from kairos_providers._async_cleanup import AsyncCleanupCoordinator


def test_asyncio_run_shutdown_drena_cleanup_antes_do_primeiro_passo():
    """Runner deve executar o cleanup reservado mesmo antes do primeiro passo."""
    coordinator = AsyncCleanupCoordinator(task_name="test-runner-shutdown")
    events: list[str] = []

    async def cleanup() -> None:
        events.append("cleanup")

    async def launch_and_return() -> None:
        waiter = asyncio.create_task(coordinator.run(cleanup))
        await asyncio.sleep(0)
        assert events == []
        assert not waiter.done()

    asyncio.run(launch_and_return())

    assert events == ["cleanup"]


def test_timeout_dentro_do_cleanup_cancela_task_supervisora():
    """Timeout interno deve interromper o await feito pelo próprio cleanup."""
    coordinator = AsyncCleanupCoordinator(task_name="test-cleanup-timeout")
    timed_out = False

    async def cleanup() -> None:
        nonlocal timed_out
        fallback = asyncio.Event()
        fallback_handle = asyncio.get_running_loop().call_later(0.01, fallback.set)
        try:
            async with asyncio.timeout(0):
                await fallback.wait()
        except TimeoutError:
            timed_out = True
        finally:
            fallback_handle.cancel()

    asyncio.run(coordinator.run(cleanup))

    assert timed_out


def test_taskgroup_cancela_corpo_do_cleanup_quando_filho_falha():
    """TaskGroup deve conseguir cancelar o parent task durante runtime."""
    coordinator = AsyncCleanupCoordinator(task_name="test-cleanup-taskgroup")
    parent_interrupted = False

    async def fail_child() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("falha do filho")

    async def cleanup() -> None:
        nonlocal parent_interrupted
        fallback = asyncio.Event()
        fallback_handle = asyncio.get_running_loop().call_later(0.01, fallback.set)
        try:
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(fail_child())
                    try:
                        await fallback.wait()
                    except asyncio.CancelledError:
                        parent_interrupted = True
                        raise
            except* RuntimeError:
                pass
        finally:
            fallback_handle.cancel()

    asyncio.run(coordinator.run(cleanup))

    assert parent_interrupted


def test_cancelar_waiter_nao_cancela_cleanup_compartilhado():
    """Waiters observam o outcome sem possuir a task supervisora."""
    coordinator = AsyncCleanupCoordinator(task_name="test-cancelled-waiter")
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def cleanup() -> None:
        nonlocal attempts
        attempts += 1
        started.set()
        await release.wait()

    async def run_waiters() -> None:
        leader = asyncio.create_task(coordinator.run(cleanup))
        await started.wait()
        cancelled_waiter = asyncio.create_task(coordinator.run(cleanup))
        await asyncio.sleep(0)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter
        assert not leader.done()
        release.set()
        await leader

    asyncio.run(run_waiters())

    assert attempts == 1
