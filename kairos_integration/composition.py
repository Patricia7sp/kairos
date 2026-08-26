"""Composição do serviço de interação a partir de um único ``KAIROS_HOME``."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
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

__all__ = ["build_interaction_service"]


def _load_config(home: Path) -> Mapping[str, Any]:
    path = home / "config.yaml"
    if not path.exists():
        return {}
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return config if isinstance(config, Mapping) else {}


class _ComposedInteractionService(InteractionService):
    """Serviço cujos recursos foram criados pelo composition root."""

    def __init__(self, *, home: Path, connection: sqlite3.Connection, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.home = home
        self.gateway = self._gateway
        self._connection = connection
        self._closed = False

    async def aclose(self) -> None:
        """Fecha, uma única vez, somente os recursos criados por esta composição."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.gateway.aclose()
        finally:
            self._connection.close()

    async def __aenter__(self) -> _ComposedInteractionService:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()


def build_interaction_service(home: Path) -> InteractionService:
    """Monta o grafo compartilhado de interação sem consultar estado global."""
    connection = connect(home / "state.db")
    try:
        migrate(connection)
        gateway = build_provider_gateway(home)
        sessions = SessionRepository(connection)
        config = _load_config(home)
        profile_configs = config.get("profiles", {})
        if not isinstance(profile_configs, Mapping):
            profile_configs = {}
        return _ComposedInteractionService(
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
    except BaseException:
        connection.close()
        raise
