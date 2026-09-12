"""Read-only accounting shared by the CLI and operational API."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from kairos_state.connection import read_connection

__all__ = ["read_usage_summary"]


def _usage_totals(rows: list[sqlite3.Row]) -> dict[str, Any]:
    counters = {
        key: sum(row[key] or 0 for row in rows)
        for key in (
            "api_call_count",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
    }
    costs = {}
    for key in ("actual_cost_usd", "estimated_cost_usd"):
        known = [row[key] for row in rows if row[key] is not None]
        costs[key] = sum(known) if known else None
    statuses = {row["cost_status"] or "unknown" for row in rows}
    unknown = sum(
        row["cost_status"] not in {"actual", "estimated"}
        or (row["actual_cost_usd"] is None and row["estimated_cost_usd"] is None)
        for row in rows
    )
    status = (
        "unknown"
        if unknown or not rows
        else (next(iter(statuses)) if len(statuses) == 1 else "mixed")
    )
    return {
        **counters,
        "requests": counters["api_call_count"],
        "tokens": counters["input_tokens"] + counters["output_tokens"],
        **costs,
        "cost_status": status,
        "unknown_cost_routes": unknown,
        "cost_totals_complete": bool(rows) and not unknown,
    }


def read_usage_summary(home: Path, *, requested_days: int | None = None) -> dict[str, Any]:
    """Read persisted all-time usage without initializing or migrating the database."""
    rows = []
    availability = "available"
    try:
        with read_connection(home / "state.db") as connection:
            rows = connection.execute(
                "SELECT api_call_count, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, reasoning_tokens, actual_cost_usd, estimated_cost_usd, "
                "cost_status FROM session_model_usage"
            ).fetchall()
    except sqlite3.Error:
        availability = "unavailable"
    return {
        "availability": availability,
        "period": "all_time",
        "requested_days": requested_days,
        "window_supported": False,
        # Route counters span multiple dates. last_seen is not a usage-event timestamp.
        "daily": [],
        "daily_status": "unavailable",
        "daily_unavailable_reason": "usage_is_cumulative_per_billing_route",
        "cost_totals_basis": "known_amounts_only",
        "totals": _usage_totals(rows),
    }
