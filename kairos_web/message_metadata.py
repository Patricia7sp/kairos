"""Allowlisted per-turn accounting from durable assistant display metadata."""

from __future__ import annotations

import json
import math
from typing import Any


def public_message_accounting(raw: str | None, *, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {prefix + "cost": None, prefix + "usage": None}
    try:
        metadata = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return result
    if not isinstance(metadata, dict):
        return result
    if prefix == "" and metadata.get("error_kind") == "cancelled":
        result["is_interrupted"] = True
    cost = metadata.get(prefix + "cost")
    if isinstance(cost, dict):
        status = cost.get("status")
        source = cost.get("source")
        result[prefix + "cost"] = {
            "estimated_usd": _amount(cost.get("estimated_usd")),
            "actual_usd": _amount(cost.get("actual_usd")),
            "status": status
            if isinstance(status, str)
            and status in {"estimated", "actual", "known", "unknown", "partial"}
            else "unknown",
            "source": source
            if isinstance(source, str) and source in {"catalog", "provider", "mixed"}
            else None,
        }
    usage = metadata.get(prefix + "usage")
    if isinstance(usage, dict):
        result[prefix + "usage"] = {
            key: value
            for key, value in usage.items()
            if key
            in {
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
                "total_tokens",
            }
            and type(value) is int
            and value >= 0
        }
    return result


def _amount(value: object) -> float | int | None:
    if type(value) in {int, float} and 0 <= value <= 1e100 and math.isfinite(value):
        return value
    return None
