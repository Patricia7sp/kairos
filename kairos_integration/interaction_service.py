"""Orquestra um turno canônico sem conhecer seu transporte de entrada ou saída."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import asdict, dataclass, field

from kairos_integration.interaction_contract import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionSelectionSnapshot,
)
from kairos_integration.retry import RetryPolicy
from kairos_integration.selection_context import SelectionContextLoader
from kairos_integration.turn_ownership import AsyncTurnLeaseBackend, SessionTurnOwnership
from kairos_providers import (
    AdapterRequest,
    CanonicalMessage,
    CanonicalToolCall,
    ContentPart,
    ModelSelectionResolver,
    ProviderError,
    ProviderEvent,
    ResolvedModelSelection,
    TokenUsage,
)
from kairos_providers.gateway import ProviderGateway
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository

__all__ = ["InteractionService"]

logger = logging.getLogger(__name__)


async def _invoke_provider_close(close: Callable[[], Awaitable[object]]) -> BaseException | None:
    """Run optional iterator cleanup without leaking a detached task exception."""
    try:
        await close()
    except BaseException as exc:  # noqa: BLE001 - owner interprets cleanup outcome
        return exc
    return None


async def _finish_provider_stream(
    provider_stream: AsyncIterator[ProviderEvent],
    primary: BaseException | None,
) -> None:
    """Finish one provider iterator before restoring its primary unwind signal."""
    close = getattr(provider_stream, "aclose", None)
    cleanup_error: BaseException | None = None
    cleanup_cancel: asyncio.CancelledError | None = None

    if callable(close):
        close_task = asyncio.create_task(
            _invoke_provider_close(close),
            name="kairos-provider-stream-close",
        )
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError as exc:
                cleanup_cancel = cleanup_cancel or exc
        cleanup_error = close_task.result()

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
    ) -> None:
        self._gateway = gateway
        self._resolver = resolver
        self._context_loader = context_loader
        self._sessions = sessions
        self._messages = messages
        self._usage = usage
        self._billing_base_url = billing_base_url
        self._billing_mode = billing_mode
        self._retry_policy = retry_policy or RetryPolicy()
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
                self._sessions.ensure(envelope.conversation_id, source=envelope.source)
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
        """Executa um turno depois de adquirir ownership exclusivo da conversa."""
        snapshot = self._resolve_snapshot(envelope)
        selection = ResolvedModelSelection(ref=snapshot.ref, reason=snapshot.reason)
        self._persist_user(envelope, selection)
        request = AdapterRequest(
            model=snapshot.ref,
            messages=self._history(envelope.conversation_id),
            parameters=snapshot.parameters,
        )
        yield InteractionEvent.turn_start(snapshot, envelope.conversation_id)

        accumulator = TurnAccumulator()
        attempts = 0
        while True:
            attempts += 1
            adapter = self._gateway.create_adapter(snapshot.ref)
            attempt_usage: TokenUsage | None = None
            try:
                provider_stream = adapter.stream(request)
                primary: BaseException | None = None
                try:
                    async for provider_event in provider_stream:
                        if provider_event.kind == "usage":
                            attempt_usage = provider_event.usage
                            continue
                        accumulator.accept(provider_event)
                        event = InteractionEvent.from_provider(provider_event)
                        if event is not None:
                            yield event
                except BaseException as exc:  # noqa: BLE001 - captures the primary unwind
                    primary = exc
                finally:
                    await _finish_provider_stream(provider_stream, primary)
            except ProviderError as exc:
                accumulator.add_attempt_usage(attempt_usage)
                if self._retry_policy.can_retry(
                    exc, accumulator
                ) and self._retry_policy.has_attempts_remaining(attempts):
                    await self._retry_policy.backoff(attempts)
                    continue
                if attempt_usage is not None:
                    yield InteractionEvent(kind="usage", usage=attempt_usage)
                self._persist_error(envelope.conversation_id, accumulator, selection, exc)
                self._record_usage(envelope.conversation_id, selection, accumulator.usage, attempts)
                yield InteractionEvent.turn_error(exc)
                return

            accumulator.add_attempt_usage(attempt_usage)
            if attempt_usage is not None:
                yield InteractionEvent(kind="usage", usage=attempt_usage)
            self._persist_assistant(envelope.conversation_id, accumulator, selection)
            self._record_usage(envelope.conversation_id, selection, accumulator.usage, attempts)
            yield InteractionEvent.turn_end(accumulator.finish_reason)
            return

    def _resolve_snapshot(self, envelope: InteractionEnvelope) -> InteractionSelectionSnapshot:
        resolved = self._resolver.resolve(self._context_loader.load(envelope))
        return InteractionSelectionSnapshot(
            ref=resolved.ref,
            reason=resolved.reason,
            parameters=envelope.parameters,
        )

    def _persist_user(
        self, envelope: InteractionEnvelope, selection: ResolvedModelSelection
    ) -> None:
        self._sessions.set_selection(
            envelope.conversation_id,
            selection.ref,
            dict(envelope.parameters),
            reason=selection.reason,
        )
        self._messages.append_turn_message(
            envelope.conversation_id,
            "user",
            envelope.content,
            selection,
            api_content=envelope.content,
        )

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

    def _persist_assistant(
        self,
        conversation_id: str,
        accumulator: TurnAccumulator,
        selection: ResolvedModelSelection,
    ) -> None:
        self._messages.append_turn_message(
            conversation_id,
            "assistant",
            accumulator.text,
            selection,
            api_content=accumulator.text,
            finish_reason=accumulator.finish_reason,
            reasoning=accumulator.reasoning or None,
            tool_calls=self._tool_calls(accumulator.tool_calls),
        )

    def _persist_error(
        self,
        conversation_id: str,
        accumulator: TurnAccumulator,
        selection: ResolvedModelSelection,
        error: ProviderError,
    ) -> None:
        metadata = {
            "error": error.message,
            "error_kind": error.kind.value,
            "model": selection.ref.model,
            "provider": selection.ref.provider,
            "reason": selection.reason.value,
            "retryable": error.retryable,
        }
        self._messages.append(
            conversation_id,
            "assistant",
            content=accumulator.text,
            api_content=accumulator.text,
            finish_reason="error",
            reasoning=accumulator.reasoning or None,
            tool_calls=self._tool_calls(accumulator.tool_calls),
            display_kind="error",
            display_metadata=json.dumps(metadata, sort_keys=True),
        )

    def _record_usage(
        self,
        conversation_id: str,
        selection: ResolvedModelSelection,
        usage: TokenUsage | None,
        attempts: int,
    ) -> None:
        self._usage.record_event(
            conversation_id,
            selection,
            billing_provider=selection.ref.provider,
            billing_base_url=self._billing_base_url,
            billing_mode=self._billing_mode,
            usage=usage,
            api_call_count=attempts,
        )

    @staticmethod
    def _tool_calls(tool_calls: list[CanonicalToolCall]) -> str | None:
        if not tool_calls:
            return None
        return json.dumps([asdict(tool_call) for tool_call in tool_calls], sort_keys=True)

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
            calls.append(CanonicalToolCall(id=call_id, name=name, arguments=arguments))
        return tuple(calls)
