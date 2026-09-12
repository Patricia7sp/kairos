"""Operational events shared by real CLI/API readers and lifecycle producers."""

import json

import pytest
from fastapi.testclient import TestClient

from kairos_cli.main import main
from kairos_observability.service_events import read_service_events, record_service_event
from kairos_web import server


@pytest.fixture
def logs_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(server.app.state, "kairos_home", tmp_path, raising=False)
    return TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}), tmp_path


def test_api_and_cli_read_same_filtered_durable_events(logs_client, capsys):
    client, home = logs_client
    assert record_service_event(home, "chat.failed", api_calls=2)
    assert record_service_event(home, "web.started")
    response = client.get("/api/logs", params={"service": "chat", "level": "error", "limit": 1})
    assert response.status_code == 200
    assert main(["logs", "--service", "chat", "--level", "error", "--limit", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == response.json()
    assert [e["code"] for e in response.json()["events"]] == ["chat.failed"]
    assert response.json()["events"][0]["counters"] == {"api_calls": 2}


def test_missing_log_source_is_explicit_and_readers_do_not_initialize_state(logs_client, capsys):
    client, home = logs_client
    assert client.get("/api/logs").json() == {
        "state": "unavailable",
        "source": "service-events",
        "events": [],
    }
    assert main(["logs", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "unavailable"
    assert list(home.iterdir()) == []


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"level": "secret"}, {"service": "../auth.json"}]
)
def test_log_filters_reject_invalid_requests(logs_client, params):
    client, _ = logs_client
    assert client.get("/api/logs", params=params).status_code == 422


def test_corrupt_journal_is_reported_without_exposing_raw_bytes(logs_client, capsys):
    client, home = logs_client
    record_service_event(home, "web.started")
    (home / "logs" / "service-events.jsonl").write_text("private-token-secret\n")
    response = client.get("/api/logs")
    assert response.json()["state"] == "error"
    assert "private-token-secret" not in response.text
    assert main(["logs"]) == 1
    captured = capsys.readouterr()
    assert "private-token-secret" not in captured.out + captured.err


def test_log_and_retired_environment_routes_require_auth(logs_client):
    _, home = logs_client
    with TestClient(server.app) as client:
        assert client.get("/api/logs").status_code == 401
        assert client.get("/api/env").status_code == 401
    assert not (home / "credentials.vault").exists()


def test_retired_environment_contract_points_to_existing_provider_management(logs_client):
    client, home = logs_client
    response = client.get("/api/env")
    assert response.status_code == 410
    assert response.json() == {"error": "retired", "replacement": "/api/providers"}
    assert list(home.iterdir()) == []


def test_web_lifecycle_records_start_and_stop(logs_client):
    client, home = logs_client
    with client:
        events = read_service_events(home)["events"]
        assert events[0]["code"] == "web.started"
    assert read_service_events(home)["events"][0]["code"] == "web.stopped"
