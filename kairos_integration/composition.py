"""Composição do serviço de interação a partir de um único ``KAIROS_HOME``."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future as ConcurrentFuture
from pathlib import Path
from typing import Any

import yaml

from kairos_integration.interaction_service import InteractionService
from kairos_integration.selection_context import SelectionContextLoader
from kairos_providers import ModelSelectionResolver
from kairos_providers.composition import build_provider_gateway
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository

__all__ = ["ComposedInteractionService", "build_interaction_service"]


def _load_config(home: Path) -> Mapping[str, Any]:
    path = home / "config.yaml"
    if not path.exists():
        return {}
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return config if isinstance(config, Mapping) else {}


def _run_async_cleanup(cleanup: Callable[[], Awaitable[None]]) -> None:
    """Executa cleanup async até o fim a partir de uma factory síncrona.

    O thread dedicado também funciona quando a factory é chamada dentro de um
    event loop já ativo; não agenda trabalho órfão nem bloqueia esse mesmo loop
    esperando uma coroutine que ele próprio teria de executar.
    """
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            asyncio.run(cleanup())
        except BaseException as exc:  # noqa: BLE001 - preserva cancelamento do cleanup async
            errors.append(exc)

    thread = threading.Thread(target=runner, name="kairos-interaction-build-cleanup")
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


class ComposedInteractionService(InteractionService):
    """Serviço cujos recursos foram criados pelo composition root."""

    def __init__(self, *, home: Path, connection: sqlite3.Connection, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.home = home
        self.gateway = self._gateway
        self._connection = connection
        self._closed = False
        self._close_lock = threading.Lock()
        self._close_result: ConcurrentFuture[None] | None = None
        self._close_runner: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        """Fecha, uma única vez, somente os recursos criados por esta composição."""
        with self._close_lock:
            if self._closed:
                return
            result = self._close_result
            if result is None or result.done():
                result = ConcurrentFuture()
                self._close_result = result
                self._close_runner = asyncio.create_task(
                    self._publish_close_result(result),
                    name="kairos-interaction-service-close",
                )
        await self._await_close_result(result)

    @staticmethod
    async def _await_close_result(result: ConcurrentFuture[None]) -> None:
        while not result.done():
            await asyncio.sleep(0.001)
        result.result()

    async def _publish_close_result(self, result: ConcurrentFuture[None]) -> None:
        try:
            await self._close_attempt()
        except BaseException as exc:  # noqa: BLE001 - publica falha/cancelamento aos waiters
            result.set_exception(exc)
        else:
            result.set_result(None)

    async def _close_attempt(self) -> None:
        errors: list[BaseException] = []
        usage_flushed = False
        try:
            self._usage.flush()
            usage_flushed = True
        except Exception as exc:  # noqa: BLE001 - agrega falha de persistência no shutdown
            errors.append(exc)
        try:
            await self.gateway.aclose()
        except BaseException as exc:  # noqa: BLE001 - tenta o banco mesmo sob cancelamento
            errors.append(exc)
        if usage_flushed:
            try:
                self._connection.close()
            except Exception as exc:  # noqa: BLE001 - mantém as demais falhas de cleanup
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup("falha ao fechar InteractionService", errors)
        with self._close_lock:
            self._closed = True

    async def __aenter__(self) -> ComposedInteractionService:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()


def build_interaction_service(home: Path) -> ComposedInteractionService:
    """Monta o grafo compartilhado de interação sem consultar estado global."""
    connection = connect(home / "state.db")
    gateway = None
    try:
        migrate(connection)
        gateway = build_provider_gateway(home)
        sessions = SessionRepository(connection)
        config = _load_config(home)
        profile_configs = config.get("profiles", {})
        if not isinstance(profile_configs, Mapping):
            profile_configs = {}
        return ComposedInteractionService(
            home=home,
            connection=connection,
            gateway=gateway,
            resolver=ModelSelectionResolver(gateway.catalog),
            context_loader=SelectionContextLoader(
                sessions,
                profile_configs=profile_configs,
                global_config=config,
            ),
            sessions=sessions,
            messages=MessageRepository(connection),
            usage=UsageRepository(connection),
        )
    except BaseException as build_error:
        cleanup_errors: list[BaseException] = []
        if gateway is not None:
            try:
                _run_async_cleanup(gateway.aclose)
            except BaseException as exc:  # noqa: BLE001 - agrega cancelamento do cleanup async
                cleanup_errors.append(exc)
        try:
            connection.close()
        except Exception as exc:  # noqa: BLE001 - agrega falha ao fechar SQLite
            cleanup_errors.append(exc)
        if cleanup_errors:
            raise BaseExceptionGroup(
                "falha ao compor e liberar InteractionService",
                [build_error, *cleanup_errors],
            ) from None
        raise
