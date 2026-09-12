from decimal import Decimal

from kairos_integration.cost_accounting import estimate_interaction_cost
from kairos_providers import ModelPrice


def test_paid_response_without_usage_is_unknown_not_zero():
    cost = estimate_interaction_cost(
        ModelPrice(prompt=Decimal("0.001"), completion=Decimal("0.002"), request=Decimal("0")),
        None,
        1,
        "catalog",
    )
    assert cost.status == "unknown"
    assert cost.estimated_usd is None


def test_free_catalog_can_estimate_zero_even_without_usage():
    cost = estimate_interaction_cost(
        ModelPrice(prompt=Decimal("0"), completion=Decimal("0"), request=Decimal("0")),
        None,
        1,
        "catalog",
    )
    assert cost.status == "estimated"
    assert cost.estimated_usd == 0
