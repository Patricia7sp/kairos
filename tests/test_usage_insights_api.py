"""The CLI report and authenticated API share actual persisted turn accounting."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from test_chat_search_loop import RoundGateway, answer_round, collect, make_service

from kairos_cli.main import main
from kairos_integration import InteractionEnvelope
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository
from kairos_state.repositories.usage import BillingRoute, TokenDelta, UsageRepository
from kairos_web import server


def test_real_turn_accounting_is_identical_after_reopen_via_api_and_cli(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(server.app.state, "kairos_home", tmp_path, raising=False)

    async def turn():
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        try:
            service = make_service(db, RoundGateway([answer_round()]))
            events = await collect(
                service,
                InteractionEnvelope(
                    conversation_id="private-session", source="web", content="private-prompt"
                ),
            )
            assert events[-1].kind == "turn_end"
        finally:
            db.close()

    asyncio.run(turn())
    snapshot = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    client = TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN})
    result = client.get("/api/analytics/usage")
    assert result.status_code == 200
    assert main(["insights", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == result.json()
    assert report["totals"]["requests"] == 1
    assert report["totals"]["tokens"] == 11
    assert report["totals"]["estimated_cost_usd"] > 0
    assert "private" not in json.dumps(report)
    for name, data in snapshot.items():
        assert (tmp_path / name).read_bytes() == data
    assert TestClient(server.app).get("/api/analytics/usage").status_code == 401


def test_requested_days_does_not_claim_a_window_and_missing_source_is_read_only(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(server.app.state, "kairos_home", tmp_path, raising=False)
    client = TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN})
    result = client.get("/api/analytics/usage?days=7").json()
    assert result["availability"] == "unavailable"
    assert result["requested_days"] == 7
    assert result["period"] == "all_time" and result["window_supported"] is False
    assert result["daily"] == [] and result["daily_status"] == "unavailable"
    assert main(["insights", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report == {**result, "requested_days": None}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("invalid", [float("inf"), "private-invalid-cost"])
def test_invalid_cost_is_unavailable_with_valid_json_across_both_surfaces(
    tmp_path, monkeypatch, capsys, invalid
):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(server.app.state, "kairos_home", tmp_path, raising=False)
    db = connect(tmp_path / "state.db")
    try:
        initialize_schema(db)
        SessionRepository(db).create("private", "web")
        usage = UsageRepository(db)
        usage.queue(
            BillingRoute("private", "private", "private", "https://private", "api"),
            TokenDelta(api_call_count=1, actual_cost_usd=0.2, cost_status="actual"),
        )
        usage.flush(now=1)
        with db:
            db.execute("UPDATE session_model_usage SET actual_cost_usd=?", (invalid,))
    finally:
        db.close()
    client = TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN})
    result = client.get("/api/analytics/usage")
    assert result.status_code == 200
    assert result.json()["availability"] == "unavailable"
    assert result.json()["totals"]["cost_totals_complete"] is False
    assert main(["insights", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report == result.json()
    assert "private" not in json.dumps(report, allow_nan=False)
