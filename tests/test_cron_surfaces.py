"""CLI and authenticated HTTP must operate the same durable schedules."""

import json
from datetime import UTC, datetime

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


def test_cli_create_pause_resume_history_remove_are_real(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Relatório",
                "--prompt",
                "Faça um resumo",
                "--every",
                "5",
                "--json",
            ]
        )
        == 0
    )
    job = json.loads(capsys.readouterr().out)
    assert JobStore(home).list()[0]["id"] == job["id"]
    assert main(["cron", "pause", job["id"], "--json"]) == 0
    capsys.readouterr()
    assert JobStore(home).list()[0]["paused"] is True
    assert main(["cron", "resume", job["id"], "--json"]) == 0
    capsys.readouterr()
    assert JobStore(home).list()[0]["paused"] is False
    assert main(["cron", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["id"] == job["id"]
    assert main(["cron", "history", job["id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"executions": []}
    assert main(["cron", "remove", job["id"], "--json"]) == 0
    capsys.readouterr()
    assert JobStore(home).list() == []


def test_api_persists_controls_and_blocks_unauthenticated_mutations(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    data = {
        "name": "Rotina",
        "prompt": "Resuma o dia",
        "schedule": {"kind": "interval", "minutes": 30},
    }
    assert TestClient(app).post("/api/cron/jobs", json=data).status_code == 401
    created = client.post("/api/cron/jobs", json=data)
    assert created.status_code == 201
    job = created.json()
    assert JobStore(home).list()[0]["id"] == job["id"]
    url = "/api/cron/jobs/" + job["id"]
    assert client.patch(url, json={"paused": True}).json()["paused"] is True
    assert client.patch(url, json={"paused": False}).json()["enabled"] is True
    assert client.get(url + "/history").json() == {"executions": []}
    assert client.delete(url).status_code == 200
    assert client.get("/api/cron/jobs").json() == {"jobs": []}
    assert client.patch(url, json={"paused": True}).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "prompt": "x", "schedule": {"kind": "interval", "minutes": True}},
        {"name": "x", "prompt": "private-input", "schedule": {"kind": "unknown"}},
        {
            "name": "x",
            "prompt": "x",
            "schedule": {"kind": "interval", "minutes": 1},
            "command": "unsupported",
        },
    ],
)
def test_invalid_api_request_never_creates_a_job_or_echoes_private_input(home, payload):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post("/api/cron/jobs", json=payload)
    assert result.status_code == 422
    assert "private-input" not in result.text
    assert JobStore(home).list() == []


def test_cron_expression_schedules_actual_next_occurrence(home):
    job = JobStore(home).create(
        name="Diário",
        prompt="Resumo",
        schedule={"kind": "cron", "expr": "0 9 * * *"},
        now=datetime(2026, 9, 12, 10, tzinfo=UTC),
    )
    assert job["next_run_at"] == "2026-09-13T09:00:00+00:00"


def test_cli_monitor_set_show_clear_and_monitoring_surface(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Vigiado",
                "--prompt",
                "Aja a partir da fonte",
                "--every",
                "5",
                "--monitor",
                "echo ok",
                "--json",
            ]
        )
        == 0
    )
    job = json.loads(capsys.readouterr().out)
    assert job["monitor"] == {"type": "script", "script": "echo ok"}
    assert main(["cron", "monitor-show", job["id"], "--json"]) == 0
    mostra = json.loads(capsys.readouterr().out)
    assert mostra["script"] == "echo ok"
    assert main(["monitoring", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["monitored"][0]["id"] == job["id"]
    assert main(["monitoring", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["monitored"] == 1
    assert main(["monitoring", "test", job["id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decisao"] == "first_run"
    assert main(["cron", "monitor-clear", job["id"], "--json"]) == 0
    capsys.readouterr()
    assert "monitor" not in JobStore(home).get(job["id"])
    assert main(["cron", "monitor-show", job["id"], "--json"]) != 0


def test_cli_rejects_monitor_on_once_schedule(home):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Único",
                "--prompt",
                "x",
                "--at",
                "2030-01-01T00:00:00Z",
                "--monitor",
                "echo x",
            ]
        )
        != 0
    )
    assert JobStore(home).list() == []


def test_api_monitor_flow_requires_auth_and_is_real(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    data = {
        "name": "Vigiado",
        "prompt": "Aja a partir da fonte",
        "schedule": {"kind": "interval", "minutes": 1},
    }
    created = client.post("/api/cron/jobs", json=data)
    assert created.status_code == 201
    url = "/api/cron/jobs/" + created.json()["id"] + "/monitor"
    assert TestClient(app).put(url, json={"script": "echo x"}).status_code == 401
    assert client.put(url, json={"script": "echo ok"}).json()["monitor"] == {
        "type": "script",
        "script": "echo ok",
    }
    assert client.get(url).json()["monitor"]["script"] == "echo ok"
    run = client.post(url + "/run")
    assert run.status_code == 200
    assert run.json()["ok"] is True
    assert run.json()["decision"] == "first_run"
    assert client.delete(url).status_code == 200
    assert client.get(url).status_code == 404
    assert client.put(url, json={"script": "echo x"}).status_code == 200
    assert client.delete(url).status_code == 200


def test_api_rejects_monitor_on_once_schedule(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post(
        "/api/cron/jobs",
        json={
            "name": "Único",
            "prompt": "x",
            "schedule": {"kind": "once", "run_at": "2030-01-01T00:00:00Z"},
            "monitor": {"type": "script", "script": "echo x"},
        },
    )
    assert result.status_code == 422
    assert JobStore(home).list() == []


def test_web_lifespan_stops_ticker_and_closes_service_even_if_journal_write_fails(
    home, monkeypatch
):
    import asyncio

    from fastapi import FastAPI

    from kairos_web import server

    started = None
    closed = []

    class Service:
        async def stream(self, envelope):
            started.set()
            await asyncio.Event().wait()
            yield

        async def aclose(self):
            closed.append(True)

    def build(_home):
        return Service()

    monkeypatch.setattr(server, "build_interaction_service", build)
    job = JobStore(home).create(
        name="x", prompt="x", schedule={"kind": "once", "run_at": "2026-01-01T00:00:00Z"}
    )

    async def run():
        nonlocal started
        started = asyncio.Event()
        application = FastAPI()
        application.state.kairos_home = home
        async with server._lifespan(application):
            await asyncio.wait_for(started.wait(), 5)

            def fail(*args):
                raise OSError("private-storage-diagnostic")

            monkeypatch.setattr(application.state.cron_scheduler.store, "finish", fail)
        assert not hasattr(application.state, "cron_task")

    asyncio.run(run())
    assert closed == [True]
    # The next owner can recover the nonterminal attempt after storage returns.
    assert JobStore(home).history(job["id"])[0]["status"] == "running"
