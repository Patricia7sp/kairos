from __future__ import annotations

import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_service import InteractionService, TurnAccumulator
from kairos_integration.retry import RetryPolicy
from kairos_providers import (
    CanonicalToolCall,
    ModelSelectionContext,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
    TokenUsage,
)
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository


def envelope() -> InteractionEnvelope:
    return InteractionEnvelope(
        conversation_id="s1",
        source="web",
        content="oi",
        parameters={"temperature": 0.2},
    )


class CountingResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, _context: ModelSelectionContext) -> ResolvedModelSelection:
        self.calls += 1
        return ResolvedModelSelection(
            ref=ProviderModelRef("fake", "chat-1"),
            reason=SelectionReason.GLOBAL_DEFAULT,
        )


class FakeContextLoader:
    def load(self, _envelope: InteractionEnvelope) -> ModelSelectionContext:
        return ModelSelectionContext(global_default=ProviderModelRef("fake", "chat-1"))


class FakeAdapter:
    def __init__(self, events: list[ProviderEvent | ProviderError]) -> None:
        self._events = events
        self.requests = []
        self.iterator_closes = 0
        self.adapter_closes = 0

    def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)

        async def events() -> AsyncIterator[ProviderEvent]:
            try:
                for event in self._events:
                    if isinstance(event, ProviderError):
                        raise event
                    yield event
            finally:
                self.iterator_closes += 1

        return events()

    async def aclose(self) -> None:
        self.adapter_closes += 1


class NonClosableIterator:
    """A conforming AsyncIterator deliberately without an optional aclose()."""

    def __init__(self, events: list[ProviderEvent | ProviderError]) -> None:
        self._events = iter(events)

    def __aiter__(self) -> NonClosableIterator:
        return self

    async def __anext__(self) -> ProviderEvent:
        try:
            event = next(self._events)
        except StopIteration:
            raise StopAsyncIteration from None
        if isinstance(event, ProviderError):
            raise event
        return event


class ExplicitClosableIterator(NonClosableIterator):
    def __init__(
        self,
        events: list[ProviderEvent | ProviderError],
        *,
        close_error: BaseException | None = None,
    ) -> None:
        super().__init__(events)
        self.close_calls = 0
        self.close_error = close_error

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class IteratorAdapter:
    def __init__(self, iterator: NonClosableIterator) -> None:
        self.iterator = iterator
        self.requests = []
        self.adapter_closes = 0

    def stream(self, request) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        return self.iterator

    async def aclose(self) -> None:
        self.adapter_closes += 1


class AttemptGateway:
    def __init__(self, adapters: list[FakeAdapter]) -> None:
        self._adapters = adapters
        self.refs: list[ProviderModelRef] = []

    def create_adapter(self, ref: ProviderModelRef) -> FakeAdapter:
        self.refs.append(ref)
        return self._adapters[len(self.refs) - 1]


class InteractionRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self._tmp.name) / "state.db")
        initialize_schema(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def service(
        self, adapters: list[FakeAdapter], *, retry_policy: RetryPolicy
    ) -> tuple[InteractionService, CountingResolver, AttemptGateway, UsageRepository]:
        resolver = CountingResolver()
        gateway = AttemptGateway(adapters)
        usage = UsageRepository(self.db)
        return (
            InteractionService(
                gateway=gateway,
                resolver=resolver,
                context_loader=FakeContextLoader(),
                sessions=SessionRepository(self.db),
                messages=MessageRepository(self.db),
                usage=usage,
                retry_policy=retry_policy,
            ),
            resolver,
            gateway,
            usage,
        )

    async def test_network_error_before_output_retries_once_with_the_frozen_selection(self) -> None:
        """Resolver again or duplicating the user turn would make retry a new turn."""
        sleeps: list[float] = []

        async def sleep(delay: float) -> None:
            sleeps.append(delay)

        first = FakeAdapter([ProviderError(ProviderErrorKind.NETWORK, retryable=True)])
        second = FakeAdapter(
            [
                ProviderEvent(kind="text_delta", text="ok"),
                ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=3, output_tokens=2)),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ]
        )
        service, resolver, gateway, usage = self.service(
            [first, second],
            retry_policy=RetryPolicy(
                sleep=sleep,
                clock=lambda: 100.0,
                initial_backoff=0.25,
                max_backoff=0.25,
            ),
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual(
            [event.kind for event in events], ["turn_start", "delta", "usage", "turn_end"]
        )
        self.assertEqual(resolver.calls, 1)
        self.assertEqual(gateway.refs, [ProviderModelRef("fake", "chat-1")] * 2)
        self.assertEqual(first.requests[0], second.requests[0])
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'", ("s1",)
            ).fetchone()[0],
            1,
        )
        usage.flush(now=1.0)
        row = self.db.execute(
            "SELECT api_call_count, input_tokens, output_tokens FROM session_model_usage WHERE session_id = ?",
            ("s1",),
        ).fetchone()
        self.assertEqual(tuple(row), (2, 3, 2))

    async def test_retry_closes_each_attempt_iterator_once_without_closing_adapters(self) -> None:
        """Closing an adapter instead of its iterator would tear down a shared resource."""

        async def sleep(_delay: float) -> None:
            return None

        first = FakeAdapter([ProviderError(ProviderErrorKind.NETWORK, retryable=True)])
        second = FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        service, _resolver, _gateway, _usage = self.service(
            [first, second], retry_policy=RetryPolicy(sleep=sleep, clock=lambda: 0.0)
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual(events[-1].kind, "turn_end")
        self.assertEqual([first.iterator_closes, second.iterator_closes], [1, 1])
        self.assertEqual([first.adapter_closes, second.adapter_closes], [0, 0])

    async def test_non_closable_async_iterator_completes_a_normal_turn(self) -> None:
        """Requiring aclose() would reject the ProviderAdapter AsyncIterator contract."""
        iterator = NonClosableIterator(
            [
                ProviderEvent(kind="text_delta", text="ok"),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ]
        )
        adapter = IteratorAdapter(iterator)
        service, _resolver, _gateway, _usage = self.service([adapter], retry_policy=RetryPolicy())

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "delta", "turn_end"])
        self.assertEqual(adapter.adapter_closes, 0)

    async def test_explicit_iterator_aclose_runs_once_on_normal_completion(self) -> None:
        """Forgetting or duplicating normal cleanup can leak or corrupt iterator resources."""
        iterator = ExplicitClosableIterator([ProviderEvent(kind="finish", finish_reason="stop")])
        service, _resolver, _gateway, _usage = self.service(
            [IteratorAdapter(iterator)], retry_policy=RetryPolicy()
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual(events[-1].kind, "turn_end")
        self.assertEqual(iterator.close_calls, 1)

    async def test_explicit_iterator_aclose_runs_once_for_each_retry_attempt(self) -> None:
        """Retry must close each attempt, without closing either iterator twice."""

        async def sleep(_delay: float) -> None:
            return None

        first = ExplicitClosableIterator([ProviderError(ProviderErrorKind.NETWORK, retryable=True)])
        second = ExplicitClosableIterator([ProviderEvent(kind="finish", finish_reason="stop")])
        service, _resolver, _gateway, _usage = self.service(
            [IteratorAdapter(first), IteratorAdapter(second)],
            retry_policy=RetryPolicy(sleep=sleep, clock=lambda: 0.0),
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual(events[-1].kind, "turn_end")
        self.assertEqual([first.close_calls, second.close_calls], [1, 1])

    async def test_normal_iterator_cleanup_failure_remains_observable(self) -> None:
        """A cleanup failure without a primary unwind must remain the terminal error."""
        iterator = ExplicitClosableIterator(
            [ProviderEvent(kind="finish", finish_reason="stop")],
            close_error=RuntimeError("cleanup-failure"),
        )
        service, _resolver, _gateway, _usage = self.service(
            [IteratorAdapter(iterator)], retry_policy=RetryPolicy()
        )

        with self.assertRaisesRegex(RuntimeError, "cleanup-failure"):
            _ = [event async for event in service.stream(envelope())]

        self.assertEqual(iterator.close_calls, 1)

    async def test_retry_reuses_request_frozen_before_backoff_mutates_session_and_history(
        self,
    ) -> None:
        """Rebuilding retry history would let a concurrent turn change this turn's prompt."""

        async def sleep(_delay: float) -> None:
            SessionRepository(self.db).set_selection(
                "s1",
                ProviderModelRef("other", "changed"),
                {"temperature": 0.9},
                reason=SelectionReason.GLOBAL_DEFAULT,
            )
            MessageRepository(self.db).append(
                "s1", "user", content="intruso", api_content="intruso"
            )

        first = FakeAdapter([ProviderError(ProviderErrorKind.NETWORK, retryable=True)])
        second = FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")])
        service, _resolver, _gateway, _usage = self.service(
            [first, second], retry_policy=RetryPolicy(sleep=sleep, clock=lambda: 0.0)
        )

        _ = [event async for event in service.stream(envelope())]

        self.assertIs(first.requests[0], second.requests[0])
        self.assertEqual(
            [message.content[0].value for message in second.requests[0].messages], ["oi"]
        )

    async def test_retry_preserves_usage_from_each_pre_output_attempt(self) -> None:
        """Replacing attempt usage would undercount a provider call that reported usage before failing."""

        async def sleep(_delay: float) -> None:
            return None

        service, _resolver, gateway, usage = self.service(
            [
                FakeAdapter(
                    [
                        ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=2)),
                        ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=4)),
                        ProviderError(ProviderErrorKind.NETWORK, retryable=True),
                    ]
                ),
                FakeAdapter(
                    [
                        ProviderEvent(
                            kind="usage", usage=TokenUsage(input_tokens=3, output_tokens=1)
                        ),
                        ProviderEvent(
                            kind="usage", usage=TokenUsage(input_tokens=6, output_tokens=1)
                        ),
                        ProviderEvent(kind="finish", finish_reason="stop"),
                    ]
                ),
            ],
            retry_policy=RetryPolicy(sleep=sleep, clock=lambda: 0.0),
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "usage", "turn_end"])
        self.assertEqual(events[1].usage, TokenUsage(input_tokens=6, output_tokens=1))
        self.assertEqual(len(gateway.refs), 2)
        usage.flush(now=1.0)
        row = self.db.execute(
            "SELECT api_call_count, input_tokens, output_tokens FROM session_model_usage WHERE session_id = ?",
            ("s1",),
        ).fetchone()
        self.assertEqual(tuple(row), (2, 10, 1))

    async def test_retryable_error_after_text_is_not_retried(self) -> None:
        """Replaying an accepted text delta duplicates user-visible assistant output."""
        await self._assert_observable_output_blocks_retry(
            ProviderEvent(kind="text_delta", text="parcial")
        )

    async def test_retryable_error_after_reasoning_is_not_retried(self) -> None:
        """Replaying accepted reasoning would expose a divergent hidden trace."""
        await self._assert_observable_output_blocks_retry(
            ProviderEvent(kind="reasoning_delta", reasoning="penso")
        )

    async def test_retryable_error_after_tool_call_is_not_retried_and_persists_partial_turn(
        self,
    ) -> None:
        """Replaying a tool call can invoke an external effect twice."""
        tool_call = CanonicalToolCall(id="call-1", name="weather", arguments='{"city":"Lisboa"}')
        await self._assert_observable_output_blocks_retry(
            ProviderEvent(kind="tool_call", tool_call=tool_call), expected_tool_call=tool_call
        )

    async def _assert_observable_output_blocks_retry(
        self, event: ProviderEvent, *, expected_tool_call: CanonicalToolCall | None = None
    ) -> None:
        async def sleep(_delay: float) -> None:
            raise AssertionError("retry must not sleep after observable output")

        service, _resolver, gateway, _usage = self.service(
            [
                FakeAdapter([event, ProviderError(ProviderErrorKind.NETWORK, retryable=True)]),
                FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")]),
            ],
            retry_policy=RetryPolicy(sleep=sleep, clock=lambda: 0.0),
        )

        events = [item async for item in service.stream(envelope())]

        self.assertEqual(len(gateway.refs), 1)
        self.assertEqual(events[-1].kind, "turn_error")
        row = self.db.execute(
            "SELECT content, reasoning, tool_calls, finish_reason FROM messages "
            "WHERE session_id = ? AND role = 'assistant'",
            ("s1",),
        ).fetchone()
        self.assertEqual(row["finish_reason"], "error")
        self.assertEqual(row["content"], event.text)
        self.assertEqual(row["reasoning"], event.reasoning or None)
        if expected_tool_call is not None:
            self.assertIn(expected_tool_call.id, row["tool_calls"])

    async def test_non_retryable_error_before_output_is_terminal(self) -> None:
        """Ignoring the provider retryability flag retries errors that cannot recover."""

        async def sleep(_delay: float) -> None:
            raise AssertionError("non-retryable error must not sleep")

        service, _resolver, gateway, _usage = self.service(
            [
                FakeAdapter([ProviderError(ProviderErrorKind.AUTH, retryable=False)]),
                FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")]),
            ],
            retry_policy=RetryPolicy(sleep=sleep, clock=lambda: 0.0),
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "turn_error"])
        self.assertEqual(len(gateway.refs), 1)

    async def test_second_retryable_error_before_output_is_terminal(self) -> None:
        """Allowing a third adapter attempt turns a bounded retry into an unbounded loop."""

        async def sleep(_delay: float) -> None:
            return None

        service, _resolver, gateway, usage = self.service(
            [
                FakeAdapter([ProviderError(ProviderErrorKind.NETWORK, retryable=True)]),
                FakeAdapter([ProviderError(ProviderErrorKind.NETWORK, retryable=True)]),
                FakeAdapter([ProviderEvent(kind="finish", finish_reason="stop")]),
            ],
            retry_policy=RetryPolicy(sleep=sleep, clock=lambda: 0.0),
        )

        events = [event async for event in service.stream(envelope())]

        self.assertEqual([event.kind for event in events], ["turn_start", "turn_error"])
        self.assertEqual(len(gateway.refs), 2)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'", ("s1",)
            ).fetchone()[0],
            1,
        )
        usage.flush(now=1.0)
        row = self.db.execute(
            "SELECT api_call_count FROM session_model_usage WHERE session_id = ?", ("s1",)
        ).fetchone()
        self.assertEqual(row[0], 2)

    def test_retry_policy_only_allows_retryable_errors_without_observable_output(self) -> None:
        """Removing any observable-output guard would permit duplicate text, reasoning, or tools."""
        policy = RetryPolicy()
        error = ProviderError(ProviderErrorKind.NETWORK, retryable=True)

        self.assertTrue(policy.can_retry(error, TurnAccumulator()))
        self.assertFalse(policy.can_retry(error, TurnAccumulator(text="x")))
        self.assertFalse(policy.can_retry(error, TurnAccumulator(reasoning="x")))
        self.assertFalse(
            policy.can_retry(
                error,
                TurnAccumulator(tool_calls=[CanonicalToolCall(id="call-1", name="weather")]),
            )
        )
        self.assertFalse(
            policy.can_retry(
                ProviderError(ProviderErrorKind.AUTH, retryable=False), TurnAccumulator()
            )
        )

    def test_retry_policy_rejects_more_than_two_total_attempts(self) -> None:
        """A configurable third attempt would violate the turn's hard retry limit."""
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=3)
