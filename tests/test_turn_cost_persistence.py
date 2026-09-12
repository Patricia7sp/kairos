from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_service import InteractionService
from kairos_providers import (
    ModelPrice,
    ModelSelectionContext,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
    TokenUsage,
)
from kairos_providers.gateway import ProviderBillingMetadata
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository


class _ContextLoader:
    def load(self, _envelope: InteractionEnvelope) -> ModelSelectionContext:
        return ModelSelectionContext(global_default=ProviderModelRef("fake", "priced-model"))


class _Resolver:
    def resolve(self, context: ModelSelectionContext) -> ResolvedModelSelection:
        assert context.global_default is not None
        return ResolvedModelSelection(
            ref=context.global_default,
            reason=SelectionReason.GLOBAL_DEFAULT,
        )


class _Adapter:
    def __init__(self, events: list[ProviderEvent | ProviderError]) -> None:
        self._events = events

    def stream(self, _request: object) -> AsyncIterator[ProviderEvent]:
        async def events() -> AsyncIterator[ProviderEvent]:
            for event in self._events:
                if isinstance(event, ProviderError):
                    raise event
                yield event

        return events()


class _PreparedGateway:
    def __init__(
        self,
        events: list[ProviderEvent | ProviderError],
        price: ModelPrice,
        *,
        cost_source: str | None = "catalog",
    ) -> None:
        self._adapter = _Adapter(events)
        self._price = price
        self._cost_source = cost_source

    def prepare(self, _ref: ProviderModelRef):
        adapter = self._adapter
        price = self._price
        cost_source = self._cost_source

        class Prepared:
            credential_id = None
            billing = ProviderBillingMetadata("fake", "https://fake.invalid/v1", "api_key")
            pricing_version = "test-pricing"

            def create_adapter(self):
                return adapter

        Prepared.price = price
        Prepared.cost_source = cost_source
        return Prepared()


def _envelope() -> InteractionEnvelope:
    return InteractionEnvelope(conversation_id="s1", source="web", content="hello")


class TurnCostPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self._tmp.name) / "state.db")
        initialize_schema(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def service(
        self,
        events: list[ProviderEvent | ProviderError],
        price: ModelPrice,
        *,
        cost_source: str | None = "catalog",
    ) -> InteractionService:
        return InteractionService(
            gateway=_PreparedGateway(events, price, cost_source=cost_source),
            resolver=_Resolver(),
            context_loader=_ContextLoader(),
            sessions=SessionRepository(self.db),
            messages=MessageRepository(self.db),
            usage=UsageRepository(self.db),
        )

    def assistant_metadata(self) -> dict[str, object]:
        row = self.db.execute(
            "SELECT display_metadata FROM messages WHERE session_id = 's1' AND role = 'assistant'"
        ).fetchone()
        assert row is not None
        return json.loads(row["display_metadata"])

    async def test_success_persists_the_cost_and_usage_emitted_for_the_turn(self) -> None:
        usage = TokenUsage(
            input_tokens=3,
            output_tokens=2,
            cache_read_tokens=5,
            reasoning_tokens=7,
            cache_write_tokens=11,
        )
        service = self.service(
            [
                ProviderEvent(kind="text_delta", text="done"),
                ProviderEvent(kind="usage", usage=usage),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ],
            ModelPrice(
                prompt=Decimal("0.001"),
                completion=Decimal("0.002"),
                request=Decimal("0.01"),
            ),
        )

        events = [event async for event in service.stream(_envelope())]
        emitted = next(event for event in events if event.kind == "usage")
        metadata = self.assistant_metadata()

        expected_cost = {
            "estimated_usd": 0.017,
            "actual_usd": None,
            "status": "estimated",
            "source": "catalog",
        }
        expected_usage = {
            "input_tokens": 3,
            "output_tokens": 2,
            "cache_read_tokens": 5,
            "reasoning_tokens": 7,
            "cache_write_tokens": 11,
            "total_tokens": 5,
        }
        self.assertEqual(metadata["cost"], expected_cost)
        self.assertEqual(metadata["usage"], expected_usage)
        self.assertEqual(metadata["cost"]["estimated_usd"], emitted.cost.estimated_usd)
        self.assertEqual(metadata["usage"]["total_tokens"], emitted.usage.total)
        self.assertEqual(
            {key: metadata[key] for key in ("provider", "model", "reason")},
            {"provider": "fake", "model": "priced-model", "reason": "global_default"},
        )

    async def test_partial_error_persists_emitted_cost_usage_and_error_metadata(self) -> None:
        service = self.service(
            [
                ProviderEvent(kind="text_delta", text="partial"),
                ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=4, output_tokens=1)),
                ProviderError(ProviderErrorKind.NETWORK, retryable=False),
            ],
            ModelPrice(
                prompt=Decimal("0.001"),
                completion=Decimal("0.002"),
                request=Decimal("0.01"),
            ),
        )

        events = [event async for event in service.stream(_envelope())]
        emitted = next(event for event in events if event.kind == "usage")
        metadata = self.assistant_metadata()

        self.assertEqual(
            metadata["cost"],
            {
                "estimated_usd": 0.016,
                "actual_usd": None,
                "status": "estimated",
                "source": "catalog",
            },
        )
        self.assertEqual(metadata["cost"]["estimated_usd"], emitted.cost.estimated_usd)
        self.assertEqual(metadata["usage"]["total_tokens"], emitted.usage.total)
        self.assertEqual(metadata["usage"]["input_tokens"], 4)
        self.assertEqual(metadata["usage"]["output_tokens"], 1)
        self.assertEqual(metadata["error_kind"], "network")
        self.assertFalse(metadata["retryable"])
        self.assertEqual(metadata["provider"], "fake")
        self.assertEqual(metadata["model"], "priced-model")
        self.assertEqual(metadata["reason"], "global_default")

    async def test_unknown_cost_is_persisted_instead_of_becoming_an_old_style_record(self) -> None:
        service = self.service(
            [
                ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=2, output_tokens=1)),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ],
            ModelPrice(),
            cost_source=None,
        )

        events = [event async for event in service.stream(_envelope())]
        emitted = next(event for event in events if event.kind == "usage")
        metadata = self.assistant_metadata()

        self.assertEqual(
            metadata["cost"],
            {
                "estimated_usd": None,
                "actual_usd": None,
                "status": "unknown",
                "source": None,
            },
        )
        self.assertEqual(metadata["cost"]["status"], emitted.cost.status)
        self.assertEqual(metadata["usage"]["total_tokens"], 3)

    async def test_exact_zero_cost_is_persisted_without_being_treated_as_unknown(self) -> None:
        service = self.service(
            [
                ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=9, output_tokens=4)),
                ProviderEvent(kind="finish", finish_reason="stop"),
            ],
            ModelPrice(
                prompt=Decimal("0"),
                completion=Decimal("0"),
                request=Decimal("0"),
            ),
        )

        events = [event async for event in service.stream(_envelope())]
        emitted = next(event for event in events if event.kind == "usage")
        metadata = self.assistant_metadata()

        self.assertEqual(
            metadata["cost"],
            {
                "estimated_usd": 0.0,
                "actual_usd": None,
                "status": "estimated",
                "source": "catalog",
            },
        )
        self.assertEqual(metadata["cost"]["estimated_usd"], emitted.cost.estimated_usd)
        self.assertEqual(metadata["usage"]["total_tokens"], 13)


if __name__ == "__main__":
    unittest.main()
