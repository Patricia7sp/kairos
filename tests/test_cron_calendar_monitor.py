"""Monitor de cron do tipo `calendar` — lembrete por janela da agenda local.

A fonte é lida em processo (mesmo parser da ferramenta `calendar`). O agente só
roda quando uma ocorrência entra na janela `[agora, agora+janela]` e ainda não
foi lembrada; o que já foi lembrado fica no `monitor_state` do job. Ticks sem
novidade suprimem o agente (sem custo de modelo e sem consumir orçamento).
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from kairos_cli.main import main
from kairos_cron.blueprints import BlueprintFillError, fill_blueprint, get_blueprint
from kairos_cron.calendar_monitor import (
    MAX_WINDOW_MINUTES,
    CalendarMonitorError,
    check_calendar_monitor,
    default_calendar_state,
    render_calendar_reminder,
    validate_calendar_monitor,
)
from kairos_cron.jobs import JobStore
from kairos_cron.monitor import MonitorOutcome, monitor_type, validate_monitor
from kairos_cron.scheduler import Scheduler
from kairos_observability.service_events import read_service_events
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
UID = "evento-1"


def _vevent(uid: str, dtstart: str, summary: str, *, rrule: str | None = None) -> str:
    regra = f"\r\nRRULE:{rrule}" if rrule else ""
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{dtstart}{regra}\r\n"
        f"SUMMARY:{summary}\r\nEND:VEVENT\r\n"
    )


def _vcalendar(*vevents: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(vevents) + "END:VCALENDAR\r\n"


def _write_calendar(home, source: str) -> None:
    (home / "config.yaml").write_text(
        yaml.safe_dump({"calendar": {"source": source}}, sort_keys=False), encoding="utf-8"
    )


def _window_event(tmp_path, *, delta_min: int = 60) -> tuple:
    """Escreve uma agenda com um evento começando em `NOW+delta_min`."""
    inicio = (NOW + timedelta(minutes=delta_min)).strftime("%Y%m%dT%H%M%SZ")
    source = tmp_path / "agenda.ics"
    source.write_text(_vcalendar(_vevent(UID, inicio, "Almoço com Maria")), encoding="utf-8")
    _write_calendar(tmp_path, str(source))
    return source


def _calendar_job(store, tmp_path, *, now=NOW, janela_min: int | None = None):
    job = store.create(
        name="Agenda",
        prompt="Lembre o usuário",
        schedule={"kind": "interval", "minutes": 5},
        now=now,
        monitor={"type": "calendar", "janela_min": janela_min or None},
    )
    return job


def make_due(store, job_id, now):
    doc = json.loads(store.path.read_text())
    for job in doc["jobs"]:
        if job["id"] == job_id:
            job["next_run_at"] = now.isoformat()
    store.path.write_text(json.dumps(doc))


class Service:
    def __init__(self):
        self.envelopes = []

    async def stream(self, envelope):
        self.envelopes.append(envelope)
        yield SimpleNamespace(kind="turn_end")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(app.state, "kairos_home", tmp_path, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Forma e validação
# ---------------------------------------------------------------------------


def test_validate_monitor_calendar_normaliza_janela_default(tmp_path):
    monitor = validate_monitor({"type": "calendar"})
    assert monitor == {"type": "calendar", "janela_min": 120}


def test_validate_monitor_calendar_limites_da_janela():
    with pytest.raises(CalendarMonitorError):
        validate_calendar_monitor({"type": "calendar", "janela_min": 0})
    with pytest.raises(CalendarMonitorError):
        validate_calendar_monitor({"type": "calendar", "janela_min": MAX_WINDOW_MINUTES + 1})
    with pytest.raises(CalendarMonitorError):
        validate_calendar_monitor({"type": "calendar", "script": "echo x"})
    assert validate_calendar_monitor({"type": "calendar", "janela_min": 30})["janela_min"] == 30


def test_estado_calendar_inicial_tem_as_tres_chaves():
    assert default_calendar_state() == {
        "remindidos": [],
        "last_changed_at": None,
        "last_checked_at": None,
    }


def test_monitor_type_reflete_o_tipo_do_job(tmp_path):
    store = JobStore(tmp_path)
    job = _calendar_job(store, tmp_path)
    assert monitor_type(job) == "calendar"


def test_calendar_monitor_requer_agendamento_recorrente(tmp_path, home):
    store = JobStore(tmp_path)
    with pytest.raises(ValueError):
        store.create(
            name="Uma",
            prompt="x",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
            monitor={"type": "calendar"},
        )
    job = _calendar_job(store, tmp_path)
    store.set_calendar_monitor(job["id"], 45)
    assert store.get(job["id"])["monitor"]["janela_min"] == 45
    with pytest.raises(CalendarMonitorError):
        store.set_calendar_monitor(job["id"], 0)
    with pytest.raises(CalendarMonitorError):
        store.set_calendar_monitor(job["id"], 99999999)


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------


def test_primeiro_evento_na_janela_dispara_e_suprime_o_proximo(tmp_path):
    _window_event(tmp_path)
    store = JobStore(tmp_path)
    job = _calendar_job(store, tmp_path, janela_min=120)
    make_due(store, job["id"], NOW)
    service = Service()
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert report["executed"] == 1
    assert report["monitored"] == 1
    assert len(service.envelopes) == 1
    estado = store.get(job["id"])["monitor_state"]
    assert len(estado["remindidos"]) == 1
    assert estado["last_changed_at"] == NOW.isoformat()
    assert estado["last_checked_at"] == NOW.isoformat()

    service2 = Service()
    make_due(store, job["id"], NOW + timedelta(minutes=10))
    report2 = asyncio.run(Scheduler(tmp_path, service2).tick(now=NOW + timedelta(minutes=10)))
    assert report2["executed"] == 0
    assert report2["suppressed"] == 1
    assert service2.envelopes == []
    assert store.get(job["id"])["repeat"]["completed"] == 1
    assert store.get(job["id"])["monitor_state"]["remindidos"] == estado["remindidos"]


def test_check_calendar_monitor_expande_recorrencia_na_janela(tmp_path):
    inicio = (NOW + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")
    source = tmp_path / "agenda.ics"
    source.write_text(
        _vcalendar(_vevent(UID, inicio, "Diário", rrule="FREQ=DAILY")), encoding="utf-8"
    )
    _write_calendar(tmp_path, str(source))
    primeiro = check_calendar_monitor(tmp_path, {"type": "calendar"}, None, NOW)
    assert primeiro.outcome is MonitorOutcome.FIRST_RUN
    assert [e.start for e in primeiro.window_events] == [NOW + timedelta(minutes=30)]

    segundo_dia = check_calendar_monitor(
        tmp_path,
        {"type": "calendar"},
        primeiro.next_state,
        NOW + timedelta(days=1),
    )
    assert segundo_dia.outcome is MonitorOutcome.CHANGED
    assert [e.start for e in segundo_dia.window_events] == [NOW + timedelta(days=1, minutes=30)]
    assert segundo_dia.run_agent is True


def test_recorrencia_diaria_lembra_por_ocorrencia(tmp_path):
    inicio = (NOW + timedelta(minutes=60)).strftime("%Y%m%dT%H%M%SZ")
    source = tmp_path / "agenda.ics"
    source.write_text(
        _vcalendar(_vevent(UID, inicio, "Diário", rrule="FREQ=DAILY")), encoding="utf-8"
    )
    _write_calendar(tmp_path, str(source))
    store = JobStore(tmp_path)
    job = _calendar_job(store, tmp_path, janela_min=120)
    make_due(store, job["id"], NOW)
    service = Service()
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert report["executed"] == 1
    assert len(store.get(job["id"])["monitor_state"]["remindidos"]) == 1

    service2 = Service()
    make_due(store, job["id"], NOW + timedelta(minutes=10))
    report2 = asyncio.run(Scheduler(tmp_path, service2).tick(now=NOW + timedelta(minutes=10)))
    assert report2["executed"] == 0
    assert report2["suppressed"] == 1

    service3 = Service()
    segundo_dia = NOW + timedelta(days=1)
    make_due(store, job["id"], segundo_dia)
    report3 = asyncio.run(Scheduler(tmp_path, service3).tick(now=segundo_dia))
    assert report3["executed"] == 1
    assert report3["suppressed"] == 0
    assert len(service3.envelopes) == 1
    estado = store.get(job["id"])["monitor_state"]
    assert len(estado["remindidos"]) == 1
    assert estado["remindidos"][0].startswith("2026-09-13")


def test_evento_fora_da_janela_suprime_sem_disparar(tmp_path):
    _window_event(tmp_path, delta_min=400)
    store = JobStore(tmp_path)
    job = _calendar_job(store, tmp_path, janela_min=120)
    make_due(store, job["id"], NOW)
    service = Service()
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert report["executed"] == 0
    assert report["suppressed"] == 1
    assert service.envelopes == []
    assert store.get(job["id"])["repeat"]["completed"] == 0


def test_fonte_ausente_e_source_error_sem_tocar_state(tmp_path):
    store = JobStore(tmp_path)
    job = _calendar_job(store, tmp_path)
    make_due(store, job["id"], NOW)
    service = Service()
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert report["source_errors"] == 1
    assert report["executed"] == 0
    assert service.envelopes == []
    events = read_service_events(tmp_path)["events"]
    assert events[0]["code"] == "cron.monitor_error"
    estado = store.get(job["id"])["monitor_state"]
    assert estado["remindidos"] == []
    assert estado["last_changed_at"] is None
    assert estado["last_checked_at"] == NOW.isoformat()


def test_ics_corrompido_e_source_error_fail_closed(tmp_path):
    source = tmp_path / "agenda.ics"
    source.write_text(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260912T130000Z", encoding="utf-8"
    )
    _write_calendar(tmp_path, str(source))
    store = JobStore(tmp_path)
    job = _calendar_job(store, tmp_path)
    make_due(store, job["id"], NOW)
    service = Service()
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert report["source_errors"] == 1
    assert report["executed"] == 0
    assert service.envelopes == []


def test_check_calendar_monitor_outcome_enum_traduz_a_decisao(tmp_path):
    _window_event(tmp_path, delta_min=30)
    decision = check_calendar_monitor(tmp_path, {"type": "calendar"}, None, NOW)
    assert decision.outcome is MonitorOutcome.FIRST_RUN
    assert decision.run_agent is True
    assert len(decision.window_events) == 1


def test_render_calendar_reminder_lista_eventos_e_titulo(tmp_path):
    _window_event(tmp_path, delta_min=30)
    decision = check_calendar_monitor(tmp_path, {"type": "calendar"}, None, NOW)
    block = render_calendar_reminder(decision.window_events)
    assert "INSTRUÇÃO DE CALENDÁRIO" in block
    assert "Almoço com Maria" in block


def test_bloco_calendario_e_injetado_no_prompt_do_agente(tmp_path):
    _window_event(tmp_path)
    store = JobStore(tmp_path)
    job = _calendar_job(store, tmp_path)
    make_due(store, job["id"], NOW)
    service = Service()
    asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    content = service.envelopes[0].content
    assert "INSTRUÇÃO DE CALENDÁRIO" in content
    assert "Almoço com Maria" in content


# ---------------------------------------------------------------------------
# Superfícies
# ---------------------------------------------------------------------------


def test_cli_create_monitor_calendar_show_set_e_run(home, capsys):
    _window_event(home)
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Agenda",
                "--prompt",
                "Lembre o usuário",
                "--expr",
                "*/5 * * * *",
                "--monitor-calendar",
                "--window-minutes",
                "60",
                "--json",
            ]
        )
        == 0
    )
    job_id = json.loads(capsys.readouterr().out)["id"]
    assert main(["cron", "monitor-show", job_id, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["type"] == "calendar"
    assert shown["janela_min"] == 60

    assert main(["cron", "monitor-calendar-set", job_id, "--window-minutes", "90", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["monitor"]["janela_min"] == 90

    assert main(["cron", "monitor-run", job_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decisao"] != "source_error"

    assert main(["monitoring", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["monitored"][0]
    assert listed["tipo"] == "calendar"
    assert listed["janela_min"] == 90


def test_cli_rejects_window_minutes_sem_monitor_calendar(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Agenda",
                "--prompt",
                "x",
                "--expr",
                "*/5 * * * *",
                "--window-minutes",
                "60",
                "--json",
            ]
        )
        != 0
    )


def test_cli_rejects_invalid_window_minutes(home, capsys):
    assert (
        main(
            [
                "cron",
                "create",
                "--name",
                "Agenda",
                "--prompt",
                "x",
                "--expr",
                "*/5 * * * *",
                "--monitor-calendar",
                "--window-minutes",
                "0",
                "--json",
            ]
        )
        != 0
    )


def test_api_put_monitor_calendar_e_run(home):
    _window_event(home)
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    created = client.post(
        "/api/cron/jobs",
        json={
            "name": "Agenda",
            "prompt": "Lembre o usuário",
            "schedule": {"kind": "cron", "expr": "*/5 * * * *"},
        },
    )
    assert created.status_code == 201
    url = "/api/cron/jobs/" + created.json()["id"] + "/monitor"
    assert client.put(url, json={"type": "calendar", "janela_min": 30}).json()["monitor"] == {
        "type": "calendar",
        "janela_min": 30,
    }
    assert client.get(url).json()["monitor_state"] == default_calendar_state()
    run = client.post(url + "/run")
    assert run.status_code == 200
    assert run.json()["ok"] is True
    assert run.json()["decision"] != "source_error"


def test_estado_do_script_continua_aceito_apos_tipagem(tmp_path, home):
    store = JobStore(tmp_path)
    job = store.create(
        name="Script",
        prompt="x",
        schedule={"kind": "interval", "minutes": 5},
        now=NOW,
        monitor={"type": "script", "script": "echo ok"},
    )
    assert store.get(job["id"])["monitor"]["type"] == "script"
    assert set(store.get(job["id"])["monitor_state"]) == {
        "last_output_hash",
        "last_changed_at",
        "last_checked_at",
    }


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------


def test_blueprint_agenda_lembrete_preenche_monitor_calendar():
    bp = get_blueprint("agenda-lembrete")
    assert bp is not None
    kwargs = fill_blueprint(bp, {"janela_min": "90", "tom": "formal"})
    assert kwargs["name"] == "Lembrete da agenda"
    assert kwargs["monitor"] == {"type": "calendar", "janela_min": 90}
    assert kwargs["schedule"] == {"kind": "cron", "expr": "*/5 * * * *"}


def test_blueprint_rejeita_janela_invalida():
    bp = get_blueprint("agenda-lembrete")
    assert bp is not None
    with pytest.raises(BlueprintFillError):
        fill_blueprint(bp, {"janela_min": "1500"})


def test_cli_blueprint_create_agenda_lembrete(home, capsys):
    _window_event(home)
    assert main(["cron", "blueprint", "create", "agenda-lembrete", "janela_min=75", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["monitor"] == {"type": "calendar", "janela_min": 75}
