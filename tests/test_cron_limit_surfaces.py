"""API and CLI must persist the same strict finite occurrence budget."""

import json

import pytest
from fastapi.testclient import TestClient

from kairos_cli.main import main
from kairos_cron.jobs import JobStore
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(app.state, "kairos_home", tmp_path, raising=False)
    return tmp_path


def test_cli_and_api_create_identical_finite_budget(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Rotina",
                "--prompt",
                "Resuma",
                "--every",
                "5",
                "--times",
                "2",
                "--json",
            ]
        )
        == 0
    )
    cli = json.loads(capsys.readouterr().out)
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post(
        "/api/cron/jobs",
        json={
            "name": "Rotina",
            "prompt": "Resuma",
            "schedule": {"kind": "interval", "minutes": 5},
            "times": 2,
        },
    )
    assert result.status_code == 201
    assert result.json()["repeat"] == cli["repeat"] == {"times": 2, "completed": 0}
    assert [job["repeat"] for job in JobStore(home).list()] == [cli["repeat"], cli["repeat"]]


@pytest.mark.parametrize("times", [True, False, "2", 2.0, 0, -1, 1000001])
def test_api_rejects_invalid_budget_without_creating_state(home, times):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post(
        "/api/cron/jobs",
        json={
            "name": "Rotina",
            "prompt": "private-prompt",
            "schedule": {"kind": "interval", "minutes": 5},
            "times": times,
        },
    )
    assert result.status_code == 422
    assert "private-prompt" not in result.text
    assert JobStore(home).list() == []


def test_api_rejects_once_budget_above_one(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post(
        "/api/cron/jobs",
        json={
            "name": "Rotina",
            "prompt": "Resuma",
            "schedule": {"kind": "once", "run_at": "2026-10-01T09:00:00Z"},
            "times": 2,
        },
    )
    assert result.status_code == 422
    assert JobStore(home).list() == []


def test_cli_rejects_zero_budget_before_persistence(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Rotina",
                "--prompt",
                "Resuma",
                "--every",
                "5",
                "--times",
                "0",
                "--json",
            ]
        )
        != 0
    )
    capsys.readouterr()
    assert JobStore(home).list() == []


def test_unlimited_default_and_explicit_null_remain_compatible(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    for extra in ({}, {"times": None}):
        result = client.post(
            "/api/cron/jobs",
            json={
                "name": "Rotina",
                "prompt": "Resuma",
                "schedule": {"kind": "interval", "minutes": 5},
                **extra,
            },
        )
        assert result.status_code == 201
        assert result.json()["repeat"] == {"times": None, "completed": 0}
