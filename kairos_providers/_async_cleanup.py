"""Coordenação de cleanup async compartilhada entre event loops.

O cleanup continua no loop que iniciou a tentativa. Isso preserva afinidade de
recursos async (por exemplo, transports HTTP) sem tornar os waiters dependentes
desse loop: eles observam apenas um ``concurrent.futures.Future`` thread-safe.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass
from typing import Any

__all__ = ["AsyncCleanupCoordinator"]


class _PersistentCleanupTask(asyncio.Task[BaseException | None]):
    """Task que mantém o loop afim vivo até o cleanup terminar.

    ``asyncio.run`` cancela todas as tasks pendentes antes de fechar o loop.
    Essa fase acontece com o loop parado; recusar somente esse cancelamento
    permite que o Runner volte a dirigir o loop e drene o cleanup. Durante a
    execução normal do loop, timeout, TaskGroup e cancellation scopes mantêm a
    semântica nativa de ``asyncio.Task.cancel``.
    """

    def cancel(self, msg: Any | None = None) -> bool:
        if not self.get_loop().is_running():
            return False
        return super().cancel(msg)


def _launch_cleanup_task(
    coroutine: Coroutine[Any, Any, BaseException | None], *, name: str
) -> asyncio.Task[BaseException | None]:
    """Agenda sem consultar a task factory potencialmente eager do caller."""
    return _PersistentCleanupTask(
        coroutine,
        loop=asyncio.get_running_loop(),
        name=name,
    )


@dataclass(slots=True)
class _CleanupAttempt:
    outcome: ConcurrentFuture[None]
    task: asyncio.Task[BaseException | None] | None = None


class AsyncCleanupCoordinator:
    """Serializa tentativas globais e oferece outcomes neutros de loop."""

    def __init__(self, *, task_name: str) -> None:
        self._task_name = task_name
        self._lock = threading.Lock()
        self._attempt: _CleanupAttempt | None = None
        self._succeeded = False

    @property
    def lock(self) -> threading.Lock:
        """Lock de estado para rejeições atômicas ligadas ao início do close."""
        return self._lock

    @property
    def succeeded(self) -> bool:
        """Deve ser consultado enquanto ``lock`` está adquirido."""
        return self._succeeded

    async def run(
        self,
        cleanup: Callable[[], Awaitable[None]],
        *,
        on_reserve: Callable[[], None] | None = None,
    ) -> None:
        """Compartilha uma tentativa; falha concluída torna a próxima retry."""
        with self._lock:
            if self._succeeded:
                return
            attempt = self._attempt
            leader = attempt is None or attempt.outcome.done()
            if leader:
                attempt = _CleanupAttempt(ConcurrentFuture())
                if on_reserve is not None:
                    on_reserve()
                self._attempt = attempt

        if leader:
            self._launch(attempt, cleanup)
        await self._wait(attempt.outcome)

    def _launch(
        self,
        attempt: _CleanupAttempt,
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        coroutine = self._execute(cleanup)
        try:
            task = _launch_cleanup_task(coroutine, name=self._task_name)
        except BaseException as exc:  # noqa: BLE001 - launch failure é outcome da tentativa
            coroutine.close()
            self._publish(attempt, exc)
            return
        attempt.task = task
        task.add_done_callback(lambda completed: self._task_done(attempt, completed))

    @staticmethod
    async def _execute(cleanup: Callable[[], Awaitable[None]]) -> BaseException | None:
        try:
            await cleanup()
        except BaseException as exc:  # noqa: BLE001 - callback publica sem exception órfã
            return exc
        return None

    def _task_done(
        self,
        attempt: _CleanupAttempt,
        task: asyncio.Task[BaseException | None],
    ) -> None:
        try:
            error = task.result()
        except BaseException as exc:  # noqa: BLE001 - todo término precisa publicar outcome
            self._publish(attempt, exc)
        else:
            self._publish(attempt, error)

    def _publish(self, attempt: _CleanupAttempt, error: BaseException | None) -> None:
        with self._lock:
            if attempt.outcome.done():
                return
            if error is None:
                self._succeeded = True
        if error is None:
            attempt.outcome.set_result(None)
        else:
            attempt.outcome.set_exception(error)

    @staticmethod
    async def _wait(outcome: ConcurrentFuture[None]) -> None:
        while not outcome.done():
            await asyncio.sleep(0.001)
        outcome.result()
