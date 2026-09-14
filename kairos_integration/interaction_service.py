"""Orquestra um turno canônico sem conhecer seu transporte de entrada ou saída."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import aclosing, suppress
from dataclasses import asdict, dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from kairos_integration.admission import InteractionAdmissionGate
from kairos_integration.chat_tools import (
    CHAT_TOOLS,
    TOOL_APPROVAL_TIMEOUT_SECONDS,
    WEB_SEARCH_TOOLS,
    chat_tool_definitions,
    denied_tool_result,
    execute_chat_tool,
    needs_tool_approval,
)
from kairos_integration.cost_accounting import estimate_interaction_cost
from kairos_integration.interaction_contract import (
    InteractionCost,
    InteractionEnvelope,
    InteractionEvent,
    InteractionPersistenceError,
    InteractionSelectionSnapshot,
    InteractionServiceError,
    InteractionToolResult,
)
from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_integration.retry import RetryPolicy
from kairos_integration.selection_context import SelectionContextLoader
from kairos_integration.turn_ownership import AsyncTurnLeaseBackend, SessionTurnOwnership
from kairos_observability.service_events import record_service_event_async
from kairos_providers import (
    AdapterRequest,
    CanonicalMessage,
    CanonicalToolCall,
    ContentPart,
    ModelPrice,
    ModelSelectionResolver,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    ResolvedModelSelection,
    TokenUsage,
)
from kairos_providers._async_cleanup import run_persistent_cleanup
from kairos_providers.gateway import (
    PreparedProviderAdapter,
    ProviderBillingMetadata,
    ProviderGateway,
)
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository

__all__ = ["InteractionService"]

logger = logging.getLogger(__name__)


async def _finish_provider_stream(
    provider_stream: AsyncIterator[ProviderEvent],
    primary: BaseException | None,
) -> None:
    """Finish one provider iterator before restoring its primary unwind signal."""
    close = getattr(provider_stream, "aclose", None)
    cleanup_error: BaseException | None = None
    cleanup_cancel: asyncio.CancelledError | None = None

    if callable(close):
        outcome = await run_persistent_cleanup(
            close,
            task_name="kairos-provider-stream-close",
        )
        cleanup_error = outcome.error
        cleanup_cancel = outcome.cancellation

    unwind = primary if primary is not None else cleanup_cancel
    if cleanup_error is not None:
        if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit)):
            raise cleanup_error
        if unwind is None:
            raise cleanup_error
        logger.error(
            "falha ao fechar iterador do provider; preservando desenrolamento primário",
            exc_info=(type(cleanup_error), cleanup_error, cleanup_error.__traceback__),
        )
    if unwind is not None:
        raise unwind from cleanup_error


@dataclass
class TurnAccumulator:
    """Estado mutável de um único stream, nunca exposto às superfícies."""

    text: str = ""
    reasoning: str = ""
    tool_calls: list[CanonicalToolCall] = field(default_factory=list)
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    attempts: int = 0
    error: ProviderError | None = None

    def accept(self, event: ProviderEvent) -> None:
        if event.kind in {"text_delta", "delta"}:
            self.text += event.text
        elif event.kind in {"reasoning_delta", "reasoning"}:
            self.reasoning += event.reasoning
        elif event.kind == "tool_call" and event.tool_call is not None:
            self.tool_calls.append(event.tool_call)
        elif event.kind == "finish":
            self.finish_reason = event.finish_reason

    def add_attempt_usage(self, usage: TokenUsage | None) -> None:
        """Accumulate one final provider snapshot per completed attempt."""
        if usage is None:
            return
        if self.usage is None:
            self.usage = usage
            return
        self.usage = TokenUsage(
            input_tokens=self.usage.input_tokens + usage.input_tokens,
            output_tokens=self.usage.output_tokens + usage.output_tokens,
            cache_read_tokens=self.usage.cache_read_tokens + usage.cache_read_tokens,
            reasoning_tokens=self.usage.reasoning_tokens + usage.reasoning_tokens,
            cache_write_tokens=self.usage.cache_write_tokens + usage.cache_write_tokens,
        )


@dataclass(frozen=True)
class _StreamTerminal:
    event: InteractionEvent | None = None
    error: BaseException | None = None


class InteractionService:
    """Executa, transmite e persiste um turno com uma seleção já congelada."""

    def __init__(
        self,
        *,
        gateway: ProviderGateway,
        resolver: ModelSelectionResolver,
        context_loader: SelectionContextLoader,
        sessions: SessionRepository,
        messages: MessageRepository,
        usage: UsageRepository,
        persistence: SQLiteAsyncInteractionPersistence | None = None,
        billing_base_url: str = "",
        billing_mode: str = "unknown",
        retry_policy: RetryPolicy | None = None,
        turn_leases: AsyncTurnLeaseBackend | None = None,
        turn_lease_poll_interval: float = 0.01,
        turn_lease_ttl_seconds: float = 300,
        turn_lease_refresh_interval: float | None = None,
        turn_lease_release_max_attempts: int = 5,
        turn_lease_clock: Callable[[], float] | None = None,
        turn_lease_sleep: Callable[[float], Awaitable[None]] | None = None,
        admission: InteractionAdmissionGate | None = None,
        event_home: Path | None = None,
    ) -> None:
        self._gateway = gateway
        self._resolver = resolver
        self._context_loader = context_loader
        self._sessions = sessions
        self._messages = messages
        self._usage = usage
        self._persistence = persistence
        self._billing_base_url = billing_base_url
        self._billing_mode = billing_mode
        self._retry_policy = retry_policy or RetryPolicy()
        self._admission = admission
        self._event_home = event_home
        self._tool_approvals: dict[str, tuple[str, asyncio.Future[str]]] = {}
        ownership_options = {}
        if turn_lease_clock is not None:
            ownership_options["clock"] = turn_lease_clock
        if turn_lease_sleep is not None:
            ownership_options["sleep"] = turn_lease_sleep
        self._turn_ownership = SessionTurnOwnership(
            turn_leases,
            poll_interval=turn_lease_poll_interval,
            ttl_seconds=turn_lease_ttl_seconds,
            refresh_interval=turn_lease_refresh_interval,
            release_max_attempts=turn_lease_release_max_attempts,
            **ownership_options,
        )

    async def stream(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        """Executa exatamente uma seleção e transmite seus eventos normalizados."""
        if self._admission is None:
            async with aclosing(self._stream_with_owned_producer(envelope)) as stream:
                async for event in stream:
                    yield event
            return
        async with (
            self._admission.admit(),
            aclosing(self._stream_with_owned_producer(envelope)) as stream,
        ):
            async for event in stream:
                yield event

    async def _stream_with_owned_producer(
        self, envelope: InteractionEnvelope
    ) -> AsyncIterator[InteractionEvent]:
        """Own the demand-paced producer until terminal delivery or explicit close."""
        demand: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        events: asyncio.Queue[InteractionEvent] = asyncio.Queue(maxsize=1)
        producer = asyncio.create_task(
            self._produce_owned(envelope, demand, events),
            name=f"kairos-interaction-turn:{envelope.conversation_id}",
        )
        try:
            while True:
                await demand.put(None)
                event_task = asyncio.create_task(events.get())
                try:
                    done, _ = await asyncio.wait(
                        {event_task, producer}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if event_task in done:
                        yield event_task.result()
                        continue
                    terminal = producer.result()
                finally:
                    if not event_task.done():
                        event_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await event_task
                if terminal.event is not None:
                    yield terminal.event
                if terminal.error is not None:
                    raise terminal.error
                return
        finally:
            if not producer.done():
                producer.cancel()
                with suppress(asyncio.CancelledError):
                    await producer
            elif not producer.cancelled():
                error = producer.exception()
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise error

    async def _produce_owned(
        self,
        envelope: InteractionEnvelope,
        demand: asyncio.Queue[None],
        events: asyncio.Queue[InteractionEvent],
    ) -> _StreamTerminal:
        try:
            async with self._turn_ownership.acquire(envelope.conversation_id, envelope.source):
                await self._ensure_session(envelope)
                async with aclosing(self._stream_owned(envelope)) as owned_stream:
                    while True:
                        await demand.get()
                        try:
                            event = await anext(owned_stream)
                        except StopAsyncIteration:
                            return _StreamTerminal()
                        if event.kind in {"turn_end", "turn_error"}:
                            return _StreamTerminal(event=event)
                        await events.put(event)
        except Exception as exc:  # noqa: BLE001 - transporta falha pelo limite do iterador
            return _StreamTerminal(error=exc)

    async def _stream_owned(self, envelope: InteractionEnvelope) -> AsyncIterator[InteractionEvent]:
        """Own the complete loop and close pending calls even on disconnect."""
        await self._repair_incomplete_tools(envelope.conversation_id)
        primary: BaseException | None = None
        try:
            async with aclosing(self._stream_tool_turn(envelope)) as stream:
                async for event in stream:
                    yield event
        except BaseException as exc:
            primary = exc
            raise
        finally:
            outcome = await run_persistent_cleanup(
                lambda: self._repair_incomplete_tools(envelope.conversation_id),
                task_name="kairos-chat-tool-history-close",
            )
            if outcome.error is not None:
                if primary is None:
                    raise outcome.error
                logger.error("falha ao fechar resultados pendentes de ferramentas")
            if outcome.cancellation is not None and primary is None:
                raise outcome.cancellation

    async def _stream_tool_turn(
        self, envelope: InteractionEnvelope
    ) -> AsyncIterator[InteractionEvent]:
        snapshot, prepared, selection, request = await self._prepare_turn(envelope)
        yield InteractionEvent.turn_start(snapshot, envelope.conversation_id)
        totals = TurnAccumulator()
        costs: list[InteractionCost] = []
        accumulator = TurnAccumulator()
        tool_rounds = 0
        executed_calls: list[str] = []
        enabled_names = self._chat_tool_names(envelope)
        while True:
            async with aclosing(
                self._stream_accountable_round(
                    envelope.conversation_id,
                    selection,
                    prepared,
                    request,
                    accumulator,
                    totals=totals,
                    costs=costs,
                )
            ) as stream:
                async for event in stream:
                    yield event
            error = accumulator.error
            cost = estimate_interaction_cost(
                prepared.price,
                accumulator.usage,
                accumulator.attempts,
                prepared.cost_source,
            )
            totals.add_attempt_usage(accumulator.usage)
            costs.append(cost)
            total_cost = _combined_cost(costs)
            turn_accounting = {
                f"turn_{key}": value
                for key, value in self._turn_display_metadata(totals.usage, total_cost).items()
            }
            outcome = await run_persistent_cleanup(
                partial(
                    self._commit_provider_round,
                    envelope.conversation_id,
                    selection,
                    prepared,
                    accumulator,
                    cost=cost,
                    turn_accounting=turn_accounting,
                ),
                task_name="kairos-provider-round-commit",
            )
            if outcome.cancellation is not None:
                raise outcome.cancellation from outcome.error
            if outcome.error is not None:
                logger.error("falha ao persistir resposta e contabilidade da chamada")
                yield InteractionEvent.turn_error(InteractionPersistenceError())
                return
            if totals.usage is not None or _cost_is_present(total_cost):
                yield InteractionEvent(kind="usage", usage=totals.usage, cost=total_cost)
            if error is not None:
                yield InteractionEvent.turn_error(error)
                return
            if not accumulator.tool_calls:
                yield InteractionEvent.turn_end(accumulator.finish_reason)
                return

            limit_error = None
            if not (envelope.tools or envelope.web_search):
                limit_error = InteractionServiceError(
                    "tools_disabled",
                    "as ferramentas estão desligadas para este turno",
                    retryable=False,
                )
            elif tool_rounds >= 4 or len(executed_calls) + len(accumulator.tool_calls) > 8:
                limit_error = InteractionServiceError(
                    "tool_limit",
                    "limite de ferramentas deste turno atingido",
                    retryable=False,
                )
            async with aclosing(
                self._execute_tool_calls(
                    envelope.conversation_id,
                    selection,
                    accumulator.tool_calls,
                    executed_calls,
                    limit_error,
                    enabled_names,
                )
            ) as stream:
                async for event in stream:
                    yield event
            if limit_error is not None:
                yield InteractionEvent.turn_error(limit_error)
                return
            tool_rounds += 1
            request = replace(
                request,
                messages=self._history(envelope.conversation_id),
                tools=(
                    self._chat_tools(envelope)
                    if tool_rounds < 4 and len(executed_calls) < 8
                    else ()
                ),
            )
            accumulator = TurnAccumulator()

    async def _commit_provider_round(
        self,
        conversation_id: str,
        selection: ResolvedModelSelection,
        prepared: PreparedProviderAdapter | _LegacyPreparedAdapter,
        accumulator: TurnAccumulator,
        *,
        cost: InteractionCost,
        turn_accounting: dict[str, object],
    ) -> None:
        # Transcript and accounting finish as one owned operation even if the
        # transport disappears while SQLite's ordered worker is writing.
        if not self._turn_ownership.is_lost():
            if accumulator.error is None:
                await self._persist_assistant(
                    conversation_id,
                    accumulator,
                    selection,
                    cost,
                    turn_accounting=turn_accounting,
                )
            else:
                await self._persist_error(
                    conversation_id,
                    accumulator,
                    selection,
                    cost,
                    accumulator.error,
                    turn_accounting=turn_accounting,
                )
        self._record_usage(
            conversation_id,
            selection,
            prepared=prepared,
            usage=accumulator.usage,
            attempts=accumulator.attempts,
            cost=cost,
        )
        await self._flush_usage()

        await self._observe_round(
            accumulator, "chat.failed" if accumulator.error else "chat.completed"
        )

    async def _execute_tool_calls(  # noqa: PYI028
        self,
        conversation_id: str,
        selection: ResolvedModelSelection,
        calls: list[CanonicalToolCall],
        executed_calls: list[str],
        limit_error: InteractionServiceError | None,
        enabled_names: frozenset[str],
    ) -> AsyncIterator[InteractionEvent]:
        for call in calls:
            if limit_error is not None:
                result = _tool_error(call.id, limit_error.message)
            elif call.name not in enabled_names:
                result = _tool_error(call.id, "ferramenta não habilitada para este turno")
            else:
                executed_calls.append(call.id)
                if needs_tool_approval(call.name):
                    approval_id, future = self._open_approval(conversation_id)
                    yield InteractionEvent.tool_approval_request(approval_id, call, conversation_id)
                    decision = await self._await_approval(
                        approval_id, future, call.name, conversation_id
                    )
                    if decision != "allow":
                        result = denied_tool_result(call)
                    else:
                        result = await execute_chat_tool(call)
                    await self._observe_tool(call, result)
                else:
                    result = await execute_chat_tool(call)
                    await self._observe_tool(call, result)
            await self._persist_tool_result(conversation_id, call, result, selection)
            yield InteractionEvent.from_tool_result(result, conversation_id)

    def _open_approval(self, conversation_id: str) -> tuple[str, asyncio.Future[str]]:
        approval_id = uuid.uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._tool_approvals[approval_id] = (conversation_id, future)
        return approval_id, future

    async def _await_approval(
        self,
        approval_id: str,
        future: asyncio.Future[str],
        call_name: str,
        conversation_id: str,
    ) -> str:
        """Aguarda a decisão do usuário; timeout recusa por falha segura."""
        try:
            try:
                return await asyncio.wait_for(future, timeout=TOOL_APPROVAL_TIMEOUT_SECONDS)
            except TimeoutError:
                logger.warning(
                    "aprovação da ferramenta %r na conversa %s expirou; recusada",
                    call_name,
                    conversation_id,
                )
                return "deny"
        finally:
            self._tool_approvals.pop(approval_id, None)

    async def _observe_tool(
        self,
        call: CanonicalToolCall,
        result: InteractionToolResult,
    ) -> None:
        family = "search" if call.name == "web_search" else "tool"
        await self._observe(f"{family}.failed" if result.is_error else f"{family}.completed")

    def decide_tool_approval(
        self,
        *,
        approval_id: str,
        session_id: str,
        decision: str,
    ) -> None:
        """Resolve uma aprovação pendente do Chat; nunca bloqueia."""
        if decision not in {"allow", "deny"}:
            raise InteractionServiceError(
                "approval_invalid_decision",
                "decisão de aprovação inválida",
                retryable=False,
            )
        pending = self._tool_approvals.get(approval_id)
        if pending is None:
            raise InteractionServiceError(
                "approval_not_found",
                "aprovação não encontrada ou já expirada",
                retryable=False,
            )
        expected_session, future = pending
        if expected_session != session_id:
            raise InteractionServiceError(
                "approval_session_mismatch",
                "aprovação pertence a outra conversa",
                retryable=False,
            )
        if future.done():
            raise InteractionServiceError(
                "approval_already_decided",
                "aprovação já decidida",
                retryable=False,
            )
        future.set_result(decision)

    async def _stream_accountable_round(
        self,
        conversation_id: str,
        selection: ResolvedModelSelection,
        prepared: PreparedProviderAdapter | _LegacyPreparedAdapter,
        request: AdapterRequest,
        accumulator: TurnAccumulator,
        *,
        totals: TurnAccumulator,
        costs: list[InteractionCost],
    ) -> AsyncIterator[InteractionEvent]:
        try:
            async with aclosing(
                self._stream_provider_round(prepared, request, accumulator)
            ) as stream:
                async for event in stream:
                    yield event
        except BaseException as exc:
            event_code = (
                "chat.cancelled"
                if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                else "chat.failed"
            )
            outcome = await run_persistent_cleanup(
                lambda: self._record_interrupted_round(
                    conversation_id,
                    selection,
                    prepared,
                    accumulator,
                    totals=totals,
                    costs=costs,
                    event_code=event_code,
                ),
                task_name="kairos-interrupted-round-accounting",
            )
            if outcome.error is not None:
                logger.error("falha ao persistir contabilidade da chamada interrompida")
            raise

    async def _record_interrupted_round(
        self,
        conversation_id: str,
        selection: ResolvedModelSelection,
        prepared: PreparedProviderAdapter | _LegacyPreparedAdapter,
        accumulator: TurnAccumulator,
        *,
        totals: TurnAccumulator,
        costs: list[InteractionCost],
        event_code: str,
    ) -> None:
        if not accumulator.attempts:
            return
        cost = estimate_interaction_cost(
            prepared.price,
            accumulator.usage,
            accumulator.attempts,
            prepared.cost_source,
        )
        totals.add_attempt_usage(accumulator.usage)
        costs.append(cost)
        if not self._turn_ownership.is_lost():
            await self._persist_error(
                conversation_id,
                accumulator,
                selection,
                cost,
                InteractionServiceError("cancelled", "Resposta interrompida.", retryable=False),
                turn_accounting={
                    f"turn_{key}": value
                    for key, value in self._turn_display_metadata(
                        totals.usage, _combined_cost(costs)
                    ).items()
                },
            )
        self._record_usage(
            conversation_id,
            selection,
            prepared=prepared,
            usage=accumulator.usage,
            attempts=accumulator.attempts,
            cost=cost,
        )
        await self._flush_usage()

        await self._observe_round(accumulator, event_code)

    async def _observe(self, code: str, **counters: int) -> None:
        if self._event_home is not None:
            await record_service_event_async(self._event_home, code, **counters)

    async def _observe_round(self, accumulator: TurnAccumulator, code: str) -> None:
        counters = {"api_calls": accumulator.attempts}
        if accumulator.usage is not None:
            counters.update(
                input_tokens=accumulator.usage.input_tokens,
                output_tokens=accumulator.usage.output_tokens,
            )
        await self._observe(code, **counters)

    async def _stream_provider_round(
        self,
        prepared: PreparedProviderAdapter | _LegacyPreparedAdapter,
        request: AdapterRequest,
        accumulator: TurnAccumulator,
    ) -> AsyncIterator[InteractionEvent]:
        while True:
            accumulator.attempts += 1
            attempt_usage: TokenUsage | None = None
            try:
                provider_stream = prepared.create_adapter().stream(request)
                primary: BaseException | None = None
                try:
                    async for provider_event in provider_stream:
                        if provider_event.kind == "usage":
                            attempt_usage = provider_event.usage
                            continue
                        self._validate_tool_event(provider_event, accumulator)
                        accumulator.accept(provider_event)
                        event = InteractionEvent.from_provider(provider_event)
                        if event is not None:
                            yield event
                except BaseException as exc:  # noqa: BLE001 - preserves primary unwind
                    primary = exc
                finally:
                    await _finish_provider_stream(provider_stream, primary)
            except ProviderError as exc:
                accumulator.add_attempt_usage(attempt_usage)
                if self._retry_policy.can_retry(
                    exc, accumulator
                ) and self._retry_policy.has_attempts_remaining(accumulator.attempts):
                    await self._retry_policy.backoff(accumulator.attempts)
                    continue
                accumulator.error = exc
            except BaseException:
                accumulator.add_attempt_usage(attempt_usage)
                raise
            else:
                accumulator.add_attempt_usage(attempt_usage)
            return

    @staticmethod
    def _validate_tool_event(event: ProviderEvent, accumulator: TurnAccumulator) -> None:
        if event.kind != "tool_call" or event.tool_call is None:
            return
        call = event.tool_call
        if (
            not isinstance(call.name, str)
            or not call.name.strip()
            or len(call.name) > 256
            or not isinstance(call.arguments, str)
            or not isinstance(call.id, str)
            or not call.id.strip()
            or len(call.id) > 256
            or any(previous.id == call.id for previous in accumulator.tool_calls)
            or len(accumulator.tool_calls) >= 32
        ):
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)

    async def _persist_tool_result(
        self,
        conversation_id: str,
        call: CanonicalToolCall,
        result: InteractionToolResult,
        selection: ResolvedModelSelection,
    ) -> None:
        args = (conversation_id, "tool", result.content, selection)
        kwargs = {
            "api_content": result.content,
            "tool_call_id": call.id,
            "tool_name": call.name,
            "display_metadata": {"is_error": result.is_error},
        }
        if self._persistence is not None:
            await self._persistence.append_turn_message(*args, **kwargs)
        else:
            self._messages.append_turn_message(*args, **kwargs)

    async def _repair_incomplete_tools(self, conversation_id: str) -> None:
        # Also repairs a process crash before accepting a new user message. Never
        # re-execute an unacknowledged tool: its external outcome is unknown.
        if self._turn_ownership.is_lost():
            return
        pending: dict[str, CanonicalToolCall] = {}
        for message in self._history(conversation_id):
            for call in message.tool_calls:
                pending[call.id] = call
            if message.role == "tool":
                pending.pop(message.tool_call_id, None)
        for call in pending.values():
            result = _tool_error(call.id, "ferramenta interrompida; resultado não disponível")
            args = (conversation_id, "tool")
            kwargs = {
                "content": result.content,
                "api_content": result.content,
                "tool_call_id": call.id,
                "tool_name": call.name,
                "display_metadata": json.dumps({"is_error": True}),
            }
            if self._persistence is not None:
                await self._persistence.append_message(*args, **kwargs)
            else:
                self._messages.append(*args, **kwargs)

    async def _prepare_turn(
        self, envelope: InteractionEnvelope
    ) -> tuple[
        InteractionSelectionSnapshot,
        PreparedProviderAdapter | _LegacyPreparedAdapter,
        ResolvedModelSelection,
        AdapterRequest,
    ]:
        snapshot = self._resolve_snapshot(envelope)
        prepared = self._prepare(snapshot.ref)
        snapshot = replace(snapshot, credential_id=prepared.credential_id)
        selection = ResolvedModelSelection(ref=snapshot.ref, reason=snapshot.reason)
        initial_parameters = _thaw_parameters(snapshot.parameters)
        if self._persistence is not None:
            await self._persistence.initialize_selection(
                envelope.conversation_id,
                snapshot.ref,
                initial_parameters,
                profile=envelope.profile,
            )
        else:
            self._sessions.initialize_selection(
                envelope.conversation_id,
                snapshot.ref,
                initial_parameters,
                profile=envelope.profile,
            )
        await self._persist_user(envelope, selection)
        return (
            snapshot,
            prepared,
            selection,
            AdapterRequest(
                model=snapshot.ref,
                messages=self._history(envelope.conversation_id),
                parameters=snapshot.parameters,
                tools=self._chat_tools(envelope),
            ),
        )

    @staticmethod
    def _chat_tools(envelope: InteractionEnvelope) -> tuple[dict[str, Any], ...]:
        if envelope.tools:
            return chat_tool_definitions(web_search_enabled=envelope.web_search)
        if envelope.web_search:
            return WEB_SEARCH_TOOLS
        return ()

    @staticmethod
    def _chat_tool_names(envelope: InteractionEnvelope) -> frozenset[str]:
        if envelope.tools:
            tools = set(CHAT_TOOLS)
            if not envelope.web_search:
                tools.discard("web_search")
            return frozenset(tools)
        if envelope.web_search:
            return frozenset({"web_search"})
        return frozenset()

    def _prepare(self, ref: ProviderModelRef) -> PreparedProviderAdapter | _LegacyPreparedAdapter:
        prepare = getattr(self._gateway, "prepare", None)
        if callable(prepare):
            return prepare(ref)
        return _LegacyPreparedAdapter(
            self._gateway,
            ref,
            billing_base_url=self._billing_base_url,
            billing_mode=self._billing_mode,
        )

    def _resolve_snapshot(self, envelope: InteractionEnvelope) -> InteractionSelectionSnapshot:
        context = self._context_loader.load(envelope)
        resolved = self._resolver.resolve(context)
        return InteractionSelectionSnapshot(
            ref=resolved.ref,
            reason=resolved.reason,
            parameters=_merged_parameters(context, message_parameters=envelope.parameters),
        )

    async def _ensure_session(self, envelope: InteractionEnvelope) -> None:
        if self._persistence is not None:
            await self._persistence.ensure(envelope.conversation_id, source=envelope.source)
            return
        self._sessions.ensure(envelope.conversation_id, source=envelope.source)

    async def _persist_user(
        self, envelope: InteractionEnvelope, selection: ResolvedModelSelection
    ) -> None:
        args = (envelope.conversation_id, "user", envelope.content, selection)
        kwargs = {"api_content": envelope.content}
        if self._persistence is not None:
            await self._persistence.append_turn_message(*args, **kwargs)
        else:
            self._messages.append_turn_message(*args, **kwargs)

    def _history(self, conversation_id: str) -> tuple[CanonicalMessage, ...]:
        return tuple(
            CanonicalMessage(
                role=row["role"],
                content=(ContentPart(kind="text", value=row["payload"]),),
                tool_call_id=row["tool_call_id"],
                tool_calls=self._rehydrate_tool_calls(row["tool_calls"]),
            )
            for row in self._messages.for_api(conversation_id)
        )

    async def _persist_assistant(
        self,
        conversation_id: str,
        accumulator: TurnAccumulator,
        selection: ResolvedModelSelection,
        cost: InteractionCost,
        *,
        turn_accounting: dict[str, object] | None = None,
    ) -> None:
        args = (conversation_id, "assistant", accumulator.text, selection)
        kwargs = {
            "api_content": accumulator.text,
            "finish_reason": accumulator.finish_reason,
            "reasoning": accumulator.reasoning or None,
            "tool_calls": self._tool_calls(accumulator.tool_calls),
            "display_metadata": self._turn_display_metadata(accumulator.usage, cost)
            | (turn_accounting or {}),
        }
        if self._persistence is not None:
            await self._persistence.append_turn_message(*args, **kwargs)
        else:
            self._messages.append_turn_message(*args, **kwargs)

    async def _persist_error(
        self,
        conversation_id: str,
        accumulator: TurnAccumulator,
        selection: ResolvedModelSelection,
        cost: InteractionCost,
        error: ProviderError | InteractionServiceError,
        *,
        turn_accounting: dict[str, object] | None = None,
    ) -> None:
        event = InteractionEvent.turn_error(error)
        metadata = self._turn_display_metadata(accumulator.usage, cost) | (turn_accounting or {})
        metadata.update(
            {
                "error": event.error,
                "error_kind": event.error_kind,
                "retryable": event.retryable,
            }
        )
        args = (conversation_id, "assistant", accumulator.text, selection)
        kwargs = {
            "api_content": accumulator.text,
            "finish_reason": "error",
            "reasoning": accumulator.reasoning or None,
            "tool_calls": self._tool_calls(accumulator.tool_calls),
            "display_kind": "error",
            "display_metadata": metadata,
        }
        if self._persistence is not None:
            await self._persistence.append_turn_message(*args, **kwargs)
        else:
            self._messages.append_turn_message(*args, **kwargs)

    def _record_usage(
        self,
        conversation_id: str,
        selection: ResolvedModelSelection,
        *,
        prepared: PreparedProviderAdapter | _LegacyPreparedAdapter,
        usage: TokenUsage | None,
        attempts: int,
        cost: InteractionCost,
    ) -> None:
        self._usage.record_event(
            conversation_id,
            selection,
            billing_provider=prepared.billing.provider,
            billing_base_url=prepared.billing.base_url,
            billing_mode=prepared.billing.mode,
            usage=usage,
            api_call_count=attempts,
            estimated_cost_usd=cost.estimated_usd,
            actual_cost_usd=cost.actual_usd,
            cost_status=cost.status,
            cost_source=cost.source,
        )

    async def _flush_usage(self) -> None:
        if self._persistence is not None:
            await self._persistence.flush_usage()
        else:
            self._usage.flush()

    async def _flush_terminal_usage(self) -> InteractionEvent | None:
        try:
            await self._flush_usage()
        except Exception:
            logger.exception("falha ao persistir contabilidade do turno")
            return InteractionEvent.turn_error(InteractionPersistenceError())
        return None

    @staticmethod
    def _tool_calls(tool_calls: list[CanonicalToolCall]) -> str | None:
        if not tool_calls:
            return None
        return json.dumps([asdict(tool_call) for tool_call in tool_calls], sort_keys=True)

    @staticmethod
    def _turn_display_metadata(
        usage: TokenUsage | None,
        cost: InteractionCost,
    ) -> dict[str, object]:
        return {
            "cost": {
                "estimated_usd": cost.estimated_usd,
                "actual_usd": cost.actual_usd,
                "status": cost.status,
                "source": cost.source,
            },
            "usage": (
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "reasoning_tokens": usage.reasoning_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                    "total_tokens": usage.total,
                }
                if usage is not None
                else None
            ),
        }

    @staticmethod
    def _rehydrate_tool_calls(raw: str | None) -> tuple[CanonicalToolCall, ...]:
        if not raw:
            return ()
        try:
            records = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        if not isinstance(records, list):
            return ()
        calls = []
        for record in records:
            if not isinstance(record, dict):
                continue
            call_id = record.get("id")
            name = record.get("name")
            arguments = record.get("arguments", "")
            if (
                not isinstance(call_id, str)
                or not isinstance(name, str)
                or not isinstance(arguments, str)
            ):
                continue
            signature = record.get("thought_signature")
            calls.append(
                CanonicalToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                    thought_signature=signature if isinstance(signature, str) else None,
                )
            )
        return tuple(calls)


def _tool_error(call_id: str, message: str) -> InteractionToolResult:
    return InteractionToolResult(call_id, json.dumps({"error": message}, ensure_ascii=False), True)


def _combined_cost(costs: list[InteractionCost]) -> InteractionCost:
    # Never present the known subset as the total when a round lacks pricing.
    if any(cost.estimated_usd is None for cost in costs):
        return InteractionCost(status="unknown", source=costs[-1].source)
    return InteractionCost(
        estimated_usd=sum(cost.estimated_usd for cost in costs),
        status="estimated",
        source=costs[-1].source,
    )


def _merged_parameters(
    context: object,
    *,
    message_parameters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Merge lower-precedence defaults first, preserving nested routing policy."""
    merged: dict[str, object] = {}
    for field_name in (
        "global_parameters",
        "profile_parameters",
        "activity_parameters",
        "conversation_parameters",
        "message_parameters",
    ):
        layer = getattr(context, field_name, None)
        if isinstance(layer, Mapping):
            _merge_mapping(merged, layer)
    if isinstance(message_parameters, Mapping):
        _merge_mapping(merged, message_parameters)
    return merged


def _cost_is_present(cost: InteractionCost) -> bool:
    return cost.estimated_usd is not None or cost.actual_usd is not None


def _thaw_parameters(value):
    if isinstance(value, Mapping):
        return {key: _thaw_parameters(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_parameters(item) for item in value]
    return value


def _merge_mapping(target: dict[str, object], layer: Mapping[str, object]) -> None:
    for key, value in layer.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _merge_mapping(current, value)
        elif isinstance(value, Mapping):
            nested: dict[str, object] = {}
            _merge_mapping(nested, value)
            target[key] = nested
        else:
            target[key] = value


class _LegacyPreparedAdapter:
    """Compatibility for injected gateways that predate the prepared boundary."""

    credential_id = None
    price = ModelPrice()
    cost_source = None
    pricing_version = None

    def __init__(
        self,
        gateway: object,
        ref: ProviderModelRef,
        *,
        billing_base_url: str = "",
        billing_mode: str = "unknown",
    ) -> None:
        self._gateway = gateway
        self._ref = ref
        self.billing = ProviderBillingMetadata(ref.provider, billing_base_url, billing_mode)

    def create_adapter(self) -> object:
        return self._gateway.create_adapter(self._ref)
