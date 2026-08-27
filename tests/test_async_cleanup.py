from __future__ import annotations

import asyncio
import gc
import warnings

import pytest

from kairos_providers._async_cleanup import (
    AsyncCleanupCoordinator,
    run_persistent_cleanup,
)


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


def test_cleanup_persistente_drena_no_shutdown_antes_do_primeiro_passo():
    """O Runner não pode cancelar um cleanup one-shot ainda não iniciado."""
    events: list[str] = []

    async def cleanup() -> None:
        events.append("started")
        await asyncio.sleep(0)
        events.append("completed")

    async def launch_and_return() -> None:
        waiter = asyncio.create_task(
            run_persistent_cleanup(cleanup, task_name="test-one-shot-shutdown")
        )
        await asyncio.sleep(0)
        cleanup_task = next(
            task for task in asyncio.all_tasks() if task.get_name() == "test-one-shot-shutdown"
        )
        assert events == []
        assert not cleanup_task.done()
        assert not waiter.done()

    asyncio.run(launch_and_return())

    assert events == ["started", "completed"]


def test_cleanup_persistente_recusa_cancelamento_externo_antes_do_primeiro_passo():
    """Cancelamento pré-start não pode impedir o cleanup já reservado."""
    events: list[str] = []

    async def cleanup() -> None:
        events.append("started")
        await asyncio.sleep(0)
        events.append("completed")

    async def launch_cancel_and_wait():
        waiter = asyncio.create_task(
            run_persistent_cleanup(cleanup, task_name="test-one-shot-pre-start")
        )
        await asyncio.sleep(0)
        cleanup_task = next(
            task for task in asyncio.all_tasks() if task.get_name() == "test-one-shot-pre-start"
        )
        assert events == []

        assert not cleanup_task.cancel()
        outcome = await waiter

        assert outcome.error is None
        assert outcome.cancellation is None

    asyncio.run(launch_cancel_and_wait())

    assert events == ["started", "completed"]


def test_falha_do_launcher_e_outcome_sem_coroutine_orfa(monkeypatch):
    """Falha síncrona ao lançar deve fechar a coroutine e publicar o erro."""
    from kairos_providers import _async_cleanup

    calls = 0

    async def cleanup() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(
        _async_cleanup,
        "_PersistentCleanupTask",
        lambda _coroutine, *, loop, name, execution: (_ for _ in ()).throw(RuntimeError(name)),
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        outcome = asyncio.run(run_persistent_cleanup(cleanup, task_name="test-launch-failure"))
        gc.collect()

    assert isinstance(outcome.error, RuntimeError)
    assert str(outcome.error) == "test-launch-failure"
    assert outcome.cancellation is None
    assert calls == 0
    assert not [warning for warning in caught if "was never awaited" in str(warning.message)]
