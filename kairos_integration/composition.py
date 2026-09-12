"""Composição do serviço de interação a partir de um único ``KAIROS_HOME``."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import aclosing
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from kairos_integration.admission import InteractionAdmissionGate
from kairos_integration.interaction_contract import InteractionEnvelope, InteractionEvent
from kairos_integration.interaction_service import InteractionService
from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_integration.router import InteractionRouter
from kairos_integration.selection_context import SelectionContextLoader
from kairos_integration.turn_ownership import SQLiteAsyncTurnLeaseBackend
from kairos_providers import ModelSelectionContext, ModelSelectionResolver, ProviderModelRef
from kairos_providers._async_cleanup import AsyncCleanupCoordinator, run_persistent_cleanup
from kairos_providers.composition import build_provider_gateway
from kairos_providers.selection import ModelSelectionUnavailableError
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories import MessageRepository, SessionRepository

__all__ = [
    "ComposedInteractionService",
    "build_interaction_router",
    "build_interaction_service",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TurnComposition:
    gateway: Any
    resolver: ModelSelectionResolver
    context_loader: SelectionContextLoader


class _TurnScopedDependency:
    """Delegate to the immutable composition of the current turn."""

    def __init__(
        self,
        fallback: Any,
        current: ContextVar[_TurnComposition | None],
        name: str,
    ) -> None:
        self._fallback = fallback
        self._current = current
        self._name = name

    def __getattr__(self, name: str) -> Any:
        composition = self._current.get()
        dependency = self._fallback if composition is None else getattr(composition, self._name)
        return getattr(dependency, name)


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
        admission = InteractionAdmissionGate()
        gateway = kwargs["gateway"]
        current: ContextVar[_TurnComposition | None] = ContextVar(
            f"kairos-interaction-turn-composition-{id(self)}", default=None
        )
        kwargs["gateway"] = _TurnScopedDependency(gateway, current, "gateway")
        kwargs["resolver"] = _TurnScopedDependency(kwargs["resolver"], current, "resolver")
        kwargs["context_loader"] = _TurnScopedDependency(
            kwargs["context_loader"], current, "context_loader"
        )
        super().__init__(admission=admission, **kwargs)
        self.home = home
        self.gateway = gateway
        self._turn_composition = current
        self._connection = connection
        self._close = AsyncCleanupCoordinator(task_name="kairos-interaction-service-close")

    async def _stream_owned(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        """Freeze fresh local configuration for one already-owned turn."""
        gateway = build_provider_gateway(self.home)
        primary: BaseException | None = None
        try:
            config = _load_config(self.home)
            profile_configs = config.get("profiles", {})
            if not isinstance(profile_configs, Mapping):
                profile_configs = {}
            composition = _TurnComposition(
                gateway=gateway,
                resolver=ModelSelectionResolver(gateway.catalog),
                context_loader=SelectionContextLoader(
                    self._sessions,
                    profile_configs=profile_configs,
                    global_config=config,
                ),
            )
            await self._refresh_missing_selection(composition, envelope)
            token = self._turn_composition.set(composition)
            try:
                async with aclosing(super()._stream_owned(envelope)) as stream:
                    async for event in stream:
                        yield event
            finally:
                self._turn_composition.reset(token)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if gateway is not self.gateway:
                outcome = await run_persistent_cleanup(
                    gateway.aclose,
                    task_name=f"kairos-interaction-turn-gateway-close:{envelope.conversation_id}",
                )
                if outcome.error is not None:
                    if isinstance(outcome.error, (KeyboardInterrupt, SystemExit)):
                        raise outcome.error
                    if primary is None:
                        raise outcome.error
                    logger.error(
                        "falha ao fechar gateway do turno; preservando desenrolamento primário",
                        exc_info=(
                            type(outcome.error),
                            outcome.error,
                            outcome.error.__traceback__,
                        ),
                    )
                if primary is None and outcome.cancellation is not None:
                    raise outcome.cancellation

    @staticmethod
    async def _refresh_missing_selection(
        composition: _TurnComposition,
        envelope: InteractionEnvelope,
    ) -> None:
        context = composition.context_loader.load(envelope)
        try:
            composition.resolver.resolve(context)
        except ModelSelectionUnavailableError:
            candidate = _first_selection_candidate(context)
            if candidate is None:
                raise
            await composition.gateway.refresh(candidate.provider)
            resolved = composition.resolver.resolve(context)
            if resolved.ref != candidate:
                raise ModelSelectionUnavailableError(
                    f"seleção indisponível: {candidate.provider}/{candidate.model}"
                ) from None

    async def aclose(self) -> None:
        """Fecha, uma única vez, somente os recursos criados por esta composição."""
        assert self._admission is not None
        await self._close.run(
            self._close_attempt,
            on_reserve=self._admission._start_draining,
        )

    async def _close_attempt(self) -> None:
        assert self._admission is not None
        await self._admission.drain()
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
        await self._admission.mark_closed()

    async def __aenter__(self) -> ComposedInteractionService:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()


def _first_selection_candidate(context: ModelSelectionContext) -> ProviderModelRef | None:
    for field in ("message", "conversation", "activity", "profile", "global_default"):
        candidate = getattr(context, field)
        if candidate is not None:
            return candidate
    return None


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


def build_interaction_router(home: Path) -> InteractionRouter:
    """Compose model execution with an explicit client for the shared runtime host."""
    from kairos_runtime import RuntimeClient

    canonical_home = Path(home).expanduser().resolve()
    model_service = build_interaction_service(canonical_home)
    return InteractionRouter(
        canonical_home,
        model_service,
        RuntimeClient(canonical_home / "run" / "runtime.sock"),
    )
