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


def test_lifecycle_command_is_rejected_on_both_surfaces(home, capsys):
    """TT-07: agendar o foot-gun canônico do ciclo de vida é rejeitado na CLI
    E na API, na criação — nunca na execução."""
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Footgun",
                "--prompt",
                "hermes gateway restart",
                "--every",
                "5",
                "--json",
            ]
        )
        != 0
    )
    capsys.readouterr()
    assert JobStore(home).list() == []

    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    api = client.post(
        "/api/cron/jobs",
        json={
            "name": "Footgun",
            "prompt": "hermes gateway restart",
            "schedule": {"kind": "interval", "minutes": 5},
        },
    )
    assert api.status_code == 422
    assert "laço de reinício" in api.json()["detail"]
    assert JobStore(home).list() == []


def test_lifecycle_prose_passes_and_monitor_script_is_scanned(home):
    """Prossa citando gateway/restart é aceita; o monitor é executado no host,
    então a forma de comando nele é bloqueada."""
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    created = client.post(
        "/api/cron/jobs",
        json={
            "name": "Estudo",
            "prompt": "Kong API gateway double-click and restart behavior in production",
            "schedule": {"kind": "interval", "minutes": 5},
        },
    )
    assert created.status_code == 201

    base = "/api/cron/jobs/" + created.json()["id"]
    blocked = client.put(base + "/monitor", json={"script": "pkill -f kairos"})
    assert blocked.status_code == 422
    assert "laço de reinício" in blocked.json()["detail"]
    assert JobStore(home).get(created.json()["id"]) is not None


def test_cli_and_api_create_with_delivery_persist_the_target(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Entregue",
                "--prompt",
                "Escreva o relatório",
                "--every",
                "5",
                "--deliver",
                "wpp:alice",
                "--json",
            ]
        )
        == 0
    )
    job = json.loads(capsys.readouterr().out)
    assert job["delivery"] == {"target": "wpp:alice"}
    assert JobStore(home).list()[0]["delivery"] == {"target": "wpp:alice"}

    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    delivery = {"delivery": {"target": "telegram:eu"}}
    api = client.post(
        "/api/cron/jobs",
        json={
            "name": "Entregue API",
            "prompt": "Resumo",
            "schedule": {"kind": "interval", "minutes": 5},
            **delivery,
        },
    )
    assert api.status_code == 201
    assert api.json()["delivery"] == {"target": "telegram:eu"}


def test_cli_rejects_malformed_delivery_target(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "X",
                "--prompt",
                "x",
                "--every",
                "5",
                "--deliver",
                "semseparador",
            ]
        )
        != 0
    )
    capsys.readouterr()
    assert JobStore(home).list() == []


def test_api_rejects_delivery_platform_not_among_registered_adapters(home, monkeypatch):
    monkeypatch.setattr(app.state, "delivery_adapters", {"wpp", "telegram"}, raising=False)
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post(
        "/api/cron/jobs",
        json={
            "name": "X",
            "prompt": "x",
            "schedule": {"kind": "interval", "minutes": 5},
            "delivery": {"target": "orb:chat"},
        },
    )
    assert result.status_code == 422
    assert "não está entre os adapters registrados" in result.json()["detail"]
    assert JobStore(home).list() == []


def test_delivery_targets_derive_from_registered_adapters(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    assert TestClient(app).get("/api/cron/delivery-targets").status_code == 401

    sem_adapter = client.get("/api/cron/delivery-targets")
    assert sem_adapter.status_code == 200
    assert sem_adapter.json() == {"targets": [{"id": "local", "name": "Local (só grava)"}]}

    app.state.delivery_adapters = {"wpp", "telegram", "slack_pub"}
    try:
        with_adapter = client.get("/api/cron/delivery-targets")
    finally:
        del app.state.delivery_adapters
    assert with_adapter.status_code == 200
    ids = [t["id"] for t in with_adapter.json()["targets"]]
    assert ids == ["local", "slack_pub", "telegram", "wpp"]


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


def test_cli_blueprint_list_show_and_create_are_real(home, capsys):
    assert main(["cron", "blueprint", "list", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert len(catalog["blueprints"]) == 13
    keys = {b["key"] for b in catalog["blueprints"]}
    assert "morning-brief" in keys and "important-mail" in keys

    assert main(["cron", "blueprint", "list", "--category", "email", "--json"]) == 0
    only_email = json.loads(capsys.readouterr().out)["blueprints"]
    assert {b["key"] for b in only_email} == {"important-mail"}

    assert main(["cron", "blueprint", "show", "weekly-review", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["key"] == "weekly-review"
    assert entry["command"].startswith("/blueprint weekly-review")
    assert entry["appUrl"].startswith("hermes://blueprint/weekly-review")
    assert len(entry["fields"]) > 0

    assert main(["cron", "blueprint", "show", "nao-existe", "--json"]) != 0


def test_cli_blueprint_create_uses_defaults_and_overrides(home, capsys):
    assert (
        main(
            [
                "cron",
                "blueprint",
                "create",
                "custom-reminder",
                "time=09:00",
                "what=testar o deploy",
                "--json",
            ]
        )
        == 0
    )
    job = json.loads(capsys.readouterr().out)
    assert job["name"] == "Lembrete personalizado"
    assert "testar o deploy" in job["prompt"]
    assert job["schedule"]["kind"] == "cron"
    assert job["schedule"]["expr"] == "0 9 * * *"
    assert "delivery" not in job or job["delivery"] is None
    assert JobStore(home).list()[0]["id"] == job["id"]


def test_cli_blueprint_create_via_slash_roundtrip(home, capsys):
    assert (
        main(
            [
                "cron",
                "blueprint",
                "create",
                "/blueprint important-mail interval_min=60",
                "--json",
            ]
        )
        == 0
    )
    job = json.loads(capsys.readouterr().out)
    assert job["schedule"]["expr"].startswith("*/60")


def test_cli_blueprint_create_rejects_unknown_slot(home, capsys):
    assert main(["cron", "blueprint", "create", "morning-brief", "bogus=x"]) != 0
    capsys.readouterr()
    assert JobStore(home).list() == []


def test_cli_blueprint_create_rejects_missing_required(home, capsys):
    assert main(["cron", "blueprint", "create", "morning-brief", "time="]) != 0
    capsys.readouterr()
    assert JobStore(home).list() == []


def test_api_blueprint_list_show_require_auth_and_are_real(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    assert TestClient(app).get("/api/cron/blueprints").status_code == 401
    catalog = client.get("/api/cron/blueprints")
    assert catalog.status_code == 200
    assert len(catalog.json()["blueprints"]) == 13

    assert TestClient(app).get("/api/cron/blueprints/morning-brief").status_code == 401
    entry = client.get("/api/cron/blueprints/morning-brief")
    assert entry.status_code == 200
    assert entry.json()["blueprint"]["title"] == "Resumo da manhã"
    assert client.get("/api/cron/blueprints/nao-existe").status_code == 404


def test_api_blueprint_create_job_is_real_and_guard_is_shared(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post(
        "/api/cron/blueprints/custom-reminder/jobs",
        json={"values": {"time": "09:00", "what": "testar api"}},
    )
    assert result.status_code == 201
    job = result.json()
    assert "testar api" in job["prompt"]
    assert JobStore(home).list()[0]["id"] == job["id"]

    blocked = client.post(
        "/api/cron/blueprints/custom-reminder/jobs",
        json={"values": {"colp": "x"}},
    )
    assert blocked.status_code == 422
    assert "colp" in blocked.json()["detail"]
    assert JobStore(home).list() == [job]


def test_api_blueprint_job_enforces_lifecycle_guard(home):
    """O prompt de um blueprint também passa pelo guard de ciclo de vida —
    sem segundo motor de jobs."""
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    values = {"what": "hermes gateway restart", "time": "09:00"}
    result = client.post("/api/cron/blueprints/custom-reminder/jobs", json={"values": values})
    assert result.status_code == 422
    assert "laço de reinício" in result.json()["detail"]
    assert JobStore(home).list() == []


def test_api_blueprint_delivery_still_validated(home, monkeypatch):
    monkeypatch.setattr(app.state, "delivery_adapters", {"wpp"}, raising=False)
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    result = client.post(
        "/api/cron/blueprints/custom-reminder/jobs",
        json={"values": {"time": "09:00", "deliver": "orb:chat"}},
    )
    assert result.status_code == 422
    assert "não está entre os adapters registrados" in result.json()["detail"]
    assert JobStore(home).list() == []
