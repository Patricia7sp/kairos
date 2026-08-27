from decimal import Decimal

from kairos_integration.cost_accounting import estimate_interaction_cost
from kairos_providers import ModelPrice, TokenUsage


def test_detail_tokens_are_not_added_to_billable_totals():
    result = estimate_interaction_cost(
        ModelPrice(
            prompt=Decimal("0.001"),
            completion=Decimal("0.002"),
            request=Decimal("0"),
        ),
        TokenUsage(
            input_tokens=100,
            output_tokens=40,
            cache_read_tokens=60,
            reasoning_tokens=10,
        ),
        attempts=1,
        source="catalog:test",
    )

    assert result.estimated_usd == 0.18
    assert result.status == "estimated"
    assert TokenUsage(input_tokens=100, output_tokens=40, reasoning_tokens=10).total == 140


def test_request_price_is_charged_without_usage():
    result = estimate_interaction_cost(
        ModelPrice(request=Decimal("0.03")),
        usage=None,
        attempts=2,
        source="catalog:test",
    )

    assert result.estimated_usd == 0.06
    assert result.status == "estimated"


def test_missing_price_for_observed_component_does_not_publish_partial_estimate():
    result = estimate_interaction_cost(
        ModelPrice(prompt=Decimal("0.001"), request=Decimal("0.03")),
        TokenUsage(input_tokens=100, output_tokens=40),
        attempts=2,
        source="catalog:test",
    )

    assert result.estimated_usd is None
    assert result.status == "unknown"
