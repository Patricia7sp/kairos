"""Monitores de fonte: a fonte roda antes; o agente só roda quando ela pede.

A fonte é execução isolada com orçamento (kairos_cron.source). Os ticks que
não executam o agente (no_change / source_error) não consomem orçamento nem
criam linha no ledger; só avançam a cadência e registram um evento.
"""

import asyncio
import json
import shlex
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kairos_cron.jobs import JobStore
from kairos_cron.monitor import output_hash
from kairos_cron.scheduler import Scheduler
from kairos_observability.service_events import read_service_events

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def source(carregador: str) -> str:
    return " ".join(
        shlex.quote(arg)
        for arg in (
            sys.executable,
            "-c",
            "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(), end='')",
            carregador,
        )
    )


def monitor(store, *, tmp_path, now=NOW):
    arquivo = tmp_path / "fonte.txt"
    arquivo.write_text("A")
    job = store.create(
        name="Monitorado",
        prompt="Aja com base na fonte",
        schedule={"kind": "interval", "minutes": 5},
        now=now,
        monitor={"type": "script", "script": source(str(arquivo))},
    )
    return job, arquivo


def make_due(store, job_id, now):
    doc = json.loads(store.path.read_text())
    for job in doc["jobs"]:
        if job["id"] == job_id:
            job["next_run_at"] = now.isoformat()
    store.path.write_text(json.dumps(doc))


def set_repeat_times(tmp_path, times):
    caminho = tmp_path / "cron/jobs.json"
    doc = json.loads(caminho.read_text())
    doc["jobs"][0]["repeat"]["times"] = times
    caminho.write_text(json.dumps(doc))


class Service:
    def __init__(self):
        self.envelopes = []

    async def stream(self, envelope):
        self.envelopes.append(envelope)
        yield SimpleNamespace(kind="turn_end")


def test_first_run_runs_agent_once_and_stores_hash(tmp_path):
    store = JobStore(tmp_path)
    job, _ = monitor(store, tmp_path=tmp_path)
    make_due(store, job["id"], NOW)
    service = Service()
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert report["monitored"] == 1
    assert report["executed"] == 1
    assert len(service.envelopes) == 1
    estado = store.get(job["id"])["monitor_state"]
    assert estado["last_changed_at"] == NOW.isoformat()
    assert estado["last_output_hash"] == output_hash("A")


def test_unchanged_source_suppresses_agent_and_advances_cadence(tmp_path):
    store = JobStore(tmp_path)
    job, _ = monitor(store, tmp_path=tmp_path)
    make_due(store, job["id"], NOW)
    assert asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))["executed"] == 1

    service = Service()
    make_due(store, job["id"], NOW + timedelta(minutes=10))
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=10)))
    assert report["executed"] == 0
    assert report["suppressed"] == 1
    assert service.envelopes == []
    job_atual = store.get(job["id"])
    assert job_atual["repeat"]["completed"] == 1
    assert job_atual["next_run_at"] == (NOW + timedelta(minutes=15)).isoformat()
    assert (
        job_atual["monitor_state"]["last_checked_at"] == (NOW + timedelta(minutes=10)).isoformat()
    )
    events = read_service_events(tmp_path)["events"]
    assert events[0]["code"] == "cron.no_change"
    assert len(store.history(job["id"])) == 1


def test_changed_source_runs_agent_again_and_advances_hash(tmp_path):
    store = JobStore(tmp_path)
    job, arquivo = monitor(store, tmp_path=tmp_path)
    make_due(store, job["id"], NOW)
    asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))
    arquivo.write_text("B")
    service = Service()
    make_due(store, job["id"], NOW + timedelta(minutes=10))
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=10)))
    assert report["executed"] == 1
    assert report["monitored"] == 1
    assert len(service.envelopes) == 1
    estado = store.get(job["id"])["monitor_state"]
    assert estado["last_output_hash"] == output_hash("B")
    assert estado["last_changed_at"] == (NOW + timedelta(minutes=10)).isoformat()


def test_source_error_is_error_never_change_and_preserves_hash(tmp_path):
    store = JobStore(tmp_path)
    job, arquivo = monitor(store, tmp_path=tmp_path)
    make_due(store, job["id"], NOW)
    asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))
    arquivo.unlink()
    service = Service()
    make_due(store, job["id"], NOW + timedelta(minutes=10))
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=10)))
    assert report["source_errors"] == 1
    assert report["executed"] == 0
    assert service.envelopes == []
    estado = store.get(job["id"])["monitor_state"]
    assert estado["last_output_hash"] == output_hash("A")
    events = read_service_events(tmp_path)["events"]
    assert events[0]["code"] == "cron.monitor_error"
    assert len(store.history(job["id"])) == 1


def test_suppressed_tick_consumes_no_budget(tmp_path):
    store = JobStore(tmp_path)
    job, _ = monitor(store, tmp_path=tmp_path)
    set_repeat_times(tmp_path, 5)
    make_due(store, job["id"], NOW)
    asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))
    assert store.get(job["id"])["repeat"]["completed"] == 1

    service = Service()
    make_due(store, job["id"], NOW + timedelta(minutes=10))
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=10)))
    assert report["suppressed"] == 1
    assert report["executed"] == 0
    assert service.envelopes == []
    assert store.get(job["id"])["repeat"]["completed"] == 1


def test_paused_monitor_job_never_runs_source(tmp_path):
    store = JobStore(tmp_path)
    job, _ = monitor(store, tmp_path=tmp_path)
    store.set_paused(job["id"], True)
    make_due(store, job["id"], NOW)
    service = Service()
    report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert report["executed"] == 0
    assert report["suppressed"] == 0
    assert report["source_errors"] == 0
    assert store.get(job["id"])["monitor_state"]["last_checked_at"] is None


def test_monitor_requires_recursive_schedule_and_only_script(tmp_path):
    store = JobStore(tmp_path)
    with pytest.raises(ValueError):
        store.create(
            name="Uma",
            prompt="x",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
            monitor={"type": "script", "script": "echo x"},
        )
    assert not (tmp_path / "cron/jobs.json").exists()
    job, _ = monitor(store, tmp_path=tmp_path)
    store.set_monitor(job["id"], "echo ok")
    with pytest.raises(ValueError):
        store.set_monitor(job["id"], "'aspas soltas")
    with pytest.raises(ValueError):
        store.create(
            name="x",
            prompt="x",
            schedule={"kind": "interval", "minutes": 5},
            monitor={"type": "url", "url": "https://example.org"},
        )


def test_scheduler_persists_monitor_state_before_agent_stream(tmp_path):
    store = JobStore(tmp_path)
    job, _ = monitor(store, tmp_path=tmp_path)
    make_due(store, job["id"], NOW)
    observed = {}

    async def stream(envelope):
        observed["hash"] = JobStore(tmp_path).get(job["id"])["monitor_state"]["last_output_hash"]
        yield SimpleNamespace(kind="turn_end")

    scheduler = Scheduler(tmp_path, SimpleNamespace(stream=stream))
    asyncio.run(scheduler.tick(now=NOW))
    assert observed["hash"] == output_hash("A")


def test_clear_monitor_removes_fonte_and_state(tmp_path):
    store = JobStore(tmp_path)
    job, _ = monitor(store, tmp_path=tmp_path)
    assert "monitor" in store.get(job["id"])
    cleared = store.clear_monitor(job["id"])
    assert "monitor" not in cleared
    assert cleared["monitor_state"] is None
