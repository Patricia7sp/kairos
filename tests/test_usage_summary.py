"""Persisted accounting remains truthful and read-only across CLI/API readers."""

import json
import sqlite3

import pytest

from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository
from kairos_state.repositories.usage import BillingRoute, TokenDelta, UsageRepository


def _persist(home, deltas):
    db = connect(home / "state.db")
    try:
        initialize_schema(db)
        SessionRepository(db).create("private-session", "web")
        usage = UsageRepository(db)
        for index, delta in enumerate(deltas):
            usage.queue(
                BillingRoute(
                    "private-session", "private-model", str(index), "https://private", "api"
                ),
                delta,
            )
        usage.flush(now=100)
    finally:
        db.close()


def test_summary_reopens_all_routes_and_preserves_partial_costs(tmp_path):
    _persist(
        tmp_path,
        [
            TokenDelta(
                api_call_count=2,
                input_tokens=100,
                output_tokens=20,
                cache_read_tokens=4,
                cache_write_tokens=3,
                reasoning_tokens=2,
                actual_cost_usd=0.3,
                cost_status="actual",
            ),
            TokenDelta(
                api_call_count=1,
                input_tokens=50,
                output_tokens=10,
                cache_read_tokens=5,
                cache_write_tokens=7,
                reasoning_tokens=8,
                estimated_cost_usd=0.2,
                cost_status="estimated",
            ),
            TokenDelta(api_call_count=1, input_tokens=5, output_tokens=1, cost_status="unknown"),
        ],
    )
    from kairos_state.usage_summary import read_usage_summary

    report = read_usage_summary(tmp_path, requested_days=1)
    assert report == {
        "availability": "available",
        "period": "all_time",
        "requested_days": 1,
        "window_supported": False,
        "daily": [],
        "daily_status": "unavailable",
        "daily_unavailable_reason": "usage_is_cumulative_per_billing_route",
        "cost_totals_basis": "known_amounts_only",
        "totals": {
            "api_call_count": 4,
            "requests": 4,
            "input_tokens": 155,
            "output_tokens": 31,
            "tokens": 186,
            "cache_read_tokens": 9,
            "cache_write_tokens": 10,
            "reasoning_tokens": 10,
            "actual_cost_usd": 0.3,
            "estimated_cost_usd": 0.2,
            "cost_status": "unknown",
            "unknown_cost_routes": 1,
            "cost_totals_complete": False,
        },
    }
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    ("deltas", "actual", "estimated", "status", "unknown", "complete"),
    [
        ([], None, None, "unknown", 0, False),
        (
            [TokenDelta(api_call_count=1, actual_cost_usd=0.0, cost_status="actual")],
            0.0,
            None,
            "actual",
            0,
            True,
        ),
        (
            [TokenDelta(api_call_count=1, estimated_cost_usd=0.0, cost_status="estimated")],
            None,
            0.0,
            "estimated",
            0,
            True,
        ),
        ([TokenDelta(api_call_count=1, cost_status="actual")], None, None, "unknown", 1, False),
        (
            [TokenDelta(api_call_count=1, cost_status="unknown", actual_cost_usd=0.5)],
            0.5,
            None,
            "unknown",
            1,
            False,
        ),
        (
            [
                TokenDelta(api_call_count=1, actual_cost_usd=0.2, cost_status="actual"),
                TokenDelta(api_call_count=1, estimated_cost_usd=0.3, cost_status="estimated"),
            ],
            0.2,
            0.3,
            "mixed",
            0,
            True,
        ),
    ],
)
def test_cost_zero_unknown_empty_and_mixed_are_distinct(
    tmp_path, *, deltas, actual, estimated, status, unknown, complete
):
    _persist(tmp_path, deltas)
    from kairos_state.usage_summary import read_usage_summary

    result = read_usage_summary(tmp_path)
    assert result["availability"] == "available"
    assert result["requested_days"] is None
    totals = result["totals"]
    assert totals["actual_cost_usd"] == actual
    assert totals["estimated_cost_usd"] == estimated
    assert totals["cost_status"] == status
    assert totals["unknown_cost_routes"] == unknown
    assert totals["cost_totals_complete"] is complete


@pytest.mark.parametrize("state", ["absent_home", "absent_db", "corrupt", "old_schema"])
def test_unavailable_inputs_return_safe_result_without_creating_or_repairing(tmp_path, state):
    home = tmp_path / "private-home" if state == "absent_home" else tmp_path
    if state == "corrupt":
        (home / "state.db").write_bytes(b"private-secret-not-a-database")
    elif state == "old_schema":
        with sqlite3.connect(home / "state.db") as db:
            db.execute("CREATE TABLE session_model_usage (api_call_count INTEGER)")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    from kairos_state.usage_summary import read_usage_summary

    result = read_usage_summary(home)
    assert result["availability"] == "unavailable"
    assert result["totals"]["actual_cost_usd"] is None
    assert result["totals"]["cost_totals_complete"] is False
    assert "private" not in json.dumps(result)
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    if state == "absent_home":
        assert not home.exists()


def test_reading_preserves_database_configuration_and_vault(tmp_path):
    _persist(tmp_path, [TokenDelta(api_call_count=1, input_tokens=7)])
    for name in ("config.yaml", "auth.json", "vault.json"):
        (tmp_path / name).write_text("private-secret")
    before = {
        name: (tmp_path / name).read_bytes()
        for name in ("state.db", "config.yaml", "auth.json", "vault.json")
    }
    from kairos_state.usage_summary import read_usage_summary

    assert read_usage_summary(tmp_path)["totals"]["tokens"] == 7
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
