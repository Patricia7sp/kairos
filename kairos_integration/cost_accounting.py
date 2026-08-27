"""Canonical estimated-cost calculation for completed interactions."""

from __future__ import annotations

from decimal import Decimal

from kairos_integration.interaction_contract import InteractionCost
from kairos_providers import ModelPrice, TokenUsage


def estimate_interaction_cost(
    price: ModelPrice,
    usage: TokenUsage | None,
    attempts: int,
    source: str | None,
) -> InteractionCost:
    quantities = (
        (usage.input_tokens if usage is not None else 0, price.prompt),
        (usage.output_tokens if usage is not None else 0, price.completion),
        (attempts, price.request),
    )
    if any(quantity > 0 and unit_price is None for quantity, unit_price in quantities):
        return InteractionCost(status="unknown", source=source)
    total = sum(
        Decimal(quantity) * unit_price
        for quantity, unit_price in quantities
        if quantity > 0 and unit_price is not None
    )
    if not any(quantity > 0 for quantity, _ in quantities):
        return InteractionCost(status="unknown", source=source)
    return InteractionCost(
        estimated_usd=float(total),
        status="estimated",
        source=source,
    )
