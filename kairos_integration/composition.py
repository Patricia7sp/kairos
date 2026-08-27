"""Composição do serviço de interação a partir de um único ``KAIROS_HOME``."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from kairos_integration.interaction_service import InteractionService
from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_integration.selection_context import SelectionContextLoader
from kairos_integration.turn_ownership import SQLiteAsyncTurnLeaseBackend
from kairos_providers import ModelSelectionResolver
from kairos_providers._async_cleanup import AsyncCleanupCoordinator
from kairos_providers.composition import build_provider_gateway
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories import MessageRepository, SessionRepository

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
        self._close = AsyncCleanupCoordinator(task_name="kairos-interaction-service-close")

    async def aclose(self) -> None:
        """Fecha, uma única vez, somente os recursos criados por esta composição."""
        await self._close.run(self._close_attempt)

    async def _close_attempt(self) -> None:
        errors: list[BaseException] = []
        usage_flushed = False
        try:
            if self._persistence is not None:
                await self._persistence.aclose()
            else:
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

    async def __aenter__(self) -> ComposedInteractionService:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()


def build_interaction_service(home: Path) -> ComposedInteractionService:
    """Monta o grafo compartilhado de interação sem consultar estado global."""
    connection = connect(home / "state.db")
    gateway = None
    persistence = None
    try:
        migrate(connection)
        gateway = build_provider_gateway(home)
        persistence = SQLiteAsyncInteractionPersistence(home / "state.db")
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
            usage=persistence.usage,
            persistence=persistence,
            turn_leases=SQLiteAsyncTurnLeaseBackend(home / "state.db"),
        )
    except BaseException as build_error:
        cleanup_errors: list[BaseException] = []
        if gateway is not None:
            try:
                _run_async_cleanup(gateway.aclose)
            except BaseException as exc:  # noqa: BLE001 - agrega cancelamento do cleanup async
                cleanup_errors.append(exc)
        if persistence is not None:
            try:
                _run_async_cleanup(persistence.aclose)
            except BaseException as exc:  # noqa: BLE001 - agrega cleanup do worker SQLite
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
