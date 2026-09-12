"""Persisted jobs must cause one observable model turn, with truthful outcomes."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def once(store, **kwargs):
    return store.create(
        name="Relatório",
        prompt="Escreva o relatório",
        schedule={"kind": "once", "run_at": NOW.isoformat()},
        **kwargs,
    )


class Service:
    def __init__(self, kind="turn_end"):
        self.kind = kind
        self.envelopes = []

    async def stream(self, envelope):
        self.envelopes.append(envelope)
        yield SimpleNamespace(kind=self.kind)


def test_job_reopens_pause_resume_and_remove_preserves_history(tmp_path):
    store = JobStore(tmp_path)
    job = once(store)
    assert json.loads((tmp_path / "cron/jobs.json").read_text())["jobs"][0]["id"] == job["id"]
    reopened = JobStore(tmp_path)
    assert reopened.list()[0]["prompt"] == "Escreva o relatório"
    reopened.set_paused(job["id"], True)
    service = Service()
    assert asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))["executed"] == 0
    reopened.set_paused(job["id"], False)
    assert asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))["executed"] == 1
    history = reopened.history(job["id"])
    assert history[0]["status"] == "completed"
    assert history[0]["conversation_id"] == service.envelopes[0].conversation_id
    reopened.remove(job["id"])
    assert reopened.list() == []
    assert reopened.history(job["id"]) == history


def test_once_claim_is_durable_before_effect_and_not_repeated(tmp_path):
    store = JobStore(tmp_path)
    job = once(store)

    class Observed(Service):
        async def stream(self, envelope):
            current = JobStore(tmp_path).list()[0]
            assert current["repeat"]["completed"] == 1
            assert current["next_run_at"] is None
            assert store.history(job["id"])[0]["status"] == "running"
            async for event in super().stream(envelope):
                yield event

    service = Observed()
    assert asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))["executed"] == 1
    assert (
        asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(days=1)))["executed"] == 0
    )
    assert len(service.envelopes) == 1
    assert service.envelopes[0].source == "cron"


@pytest.mark.parametrize("kind", ["turn_error", "delta"])
def test_error_or_missing_terminal_never_reports_success(tmp_path, kind):
    job = once(JobStore(tmp_path))
    report = asyncio.run(Scheduler(tmp_path, Service(kind)).tick(now=NOW))
    assert report["failed"] == 1
    assert JobStore(tmp_path).history(job["id"])[0]["status"] == "failed"


def test_concurrent_ticks_do_not_duplicate_and_pause_is_allowed_while_running(tmp_path):
    store = JobStore(tmp_path)
    job = once(store)

    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        class Slow(Service):
            async def stream(self, envelope):
                self.envelopes.append(envelope)
                started.set()
                await finish.wait()
                yield SimpleNamespace(kind="turn_end")

        service = Slow()
        task = asyncio.create_task(Scheduler(tmp_path, service).tick(now=NOW))
        await started.wait()
        second = await Scheduler(tmp_path, Service()).tick(now=NOW)
        assert second["busy"] is True
        store.set_paused(job["id"], True)
        finish.set()
        await task
        assert len(service.envelopes) == 1

    asyncio.run(run())


def test_backlog_collapses_to_one_and_future_time_uses_current_tick(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(
        name="Periódico",
        prompt="Faça o relatório",
        schedule={"kind": "interval", "minutes": 5},
        now=NOW - timedelta(days=2),
    )
    service = Service()
    asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert len(service.envelopes) == 1
    assert store.list()[0]["next_run_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert store.history(job["id"])[0]["status"] == "completed"


def test_bad_json_and_invalid_job_never_replace_existing_file(tmp_path):
    store = JobStore(tmp_path)
    once(store)
    path = tmp_path / "cron/jobs.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.create(
            name="Inválido",
            prompt="kairos gateway restart",
            schedule={"kind": "once", "run_at": "bad"},
        )
    assert path.read_bytes() == before
    path.write_text("{broken")
    with pytest.raises(ValueError):
        once(store)
    assert path.read_text() == "{broken"


@pytest.mark.parametrize(
    "schedule",
    [
        {"kind": "interval", "minutes": True},
        {"kind": "interval", "minutes": 0},
        {"kind": "once", "run_at": "2026-09-12T12:00:00"},
        {"kind": "imaginary"},
        {"kind": "cron", "expr": "not cron"},
    ],
)
def test_invalid_schedule_rejected_before_persistence(tmp_path, schedule):
    with pytest.raises(ValueError):
        JobStore(tmp_path).create(name="x", prompt="x", schedule=schedule)
    assert not (tmp_path / "cron/jobs.json").exists()


def test_cancelled_execution_is_unknown_and_once_does_not_retry(tmp_path):
    store = JobStore(tmp_path)
    job = once(store)

    async def run():
        started = asyncio.Event()

        class Slow:
            async def stream(self, envelope):
                started.set()
                await asyncio.Event().wait()
                yield

        task = asyncio.create_task(Scheduler(tmp_path, Slow()).tick(now=NOW))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert store.history(job["id"])[0]["status"] == "unknown"
    assert asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))["executed"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"schedule": {"kind": "invalid"}},
        {"prompt": ""},
        {"repeat": {"times": 1, "completed": -1}},
    ],
)
def test_invalid_persisted_record_never_executes_or_rewrites(tmp_path, change):
    store = JobStore(tmp_path)
    once(store)
    path = tmp_path / "cron/jobs.json"
    document = json.loads(path.read_text())
    document["jobs"][0].update(change)
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    service = Service()
    with pytest.raises(ValueError):
        asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert service.envelopes == []
    assert path.read_bytes() == before


def test_exhausted_once_budget_is_respected_even_without_occurrence_ledger(tmp_path):
    store = JobStore(tmp_path)
    once(store)
    path = tmp_path / "cron/jobs.json"
    document = json.loads(path.read_text())
    document["jobs"][0]["repeat"]["completed"] = 1
    path.write_text(json.dumps(document))
    service = Service()
    assert asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))["executed"] == 0
    assert service.envelopes == []


def test_cancel_survives_failure_to_persist_unknown(tmp_path, monkeypatch):
    once(JobStore(tmp_path))

    async def run():
        started = asyncio.Event()

        class Slow:
            async def stream(self, envelope):
                started.set()
                await asyncio.Event().wait()
                yield

        scheduler = Scheduler(tmp_path, Slow())

        def broken(*args):
            raise OSError("private-storage-diagnostic")

        monkeypatch.setattr(scheduler.store, "finish", broken)
        task = asyncio.create_task(scheduler.run(interval=0.01))
        await started.wait()
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        if not done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            pytest.fail("scheduler swallowed cancellation and continued executing")
        assert task.cancelled()

    asyncio.run(run())


def test_ledger_fences_crash_between_claim_and_json_write(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    job = once(store)
    original = JobStore._write

    def fail(self, document):
        raise OSError("simulated process death before JSON replace")

    monkeypatch.setattr(JobStore, "_write", fail)
    service = Service()
    with pytest.raises(OSError):
        asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert service.envelopes == []
    assert store.history(job["id"])[0]["status"] == "claimed"
    monkeypatch.setattr(JobStore, "_write", original)
    result = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert result["recovered"] == 1
    assert result["executed"] == 0
    assert store.history(job["id"])[0]["status"] == "unknown"
    assert store.list()[0]["next_run_at"] is None


def test_tick_lock_and_recovery_survive_real_process_death(tmp_path):
    import subprocess
    import sys

    code = """import asyncio,sys
from pathlib import Path
from kairos_cron.scheduler import Scheduler
class Service:
 async def stream(self,envelope):
  print('EFFECT_STARTED',flush=True)
  await asyncio.Event().wait()
  yield
asyncio.run(Scheduler(Path(sys.argv[1]),Service()).tick())
"""
    store = JobStore(tmp_path)
    job = once(store)
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        import select

        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == "EFFECT_STARTED"
        assert asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))["busy"] is True
        child.kill()
        child.wait(timeout=10)
        service = Service()
        result = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
        assert result["recovered"] == 1
        assert service.envelopes == []
        assert store.history(job["id"])[0]["status"] == "unknown"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdout.close()


def test_timeout_closes_provider_stream_and_marks_failure(tmp_path):
    store = JobStore(tmp_path)
    job = once(store)
    closed = []

    class Slow:
        async def stream(self, envelope):
            try:
                await asyncio.Event().wait()
                yield
            finally:
                closed.append(True)

    report = asyncio.run(Scheduler(tmp_path, Slow(), timeout=0.01).tick(now=NOW))
    assert report["failed"] == 1
    assert closed == [True]
    assert store.history(job["id"])[0]["status"] == "failed"


def test_terminal_execution_is_immutable_in_database(tmp_path):
    import sqlite3

    job = once(JobStore(tmp_path))
    asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))
    with sqlite3.connect(tmp_path / "state.db") as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE executions SET status='running' WHERE job_id=?", (job["id"],))


def test_incomplete_repeat_record_blocks_read_write_and_dispatch(tmp_path):
    store = JobStore(tmp_path)
    store.create(
        name="x",
        prompt="x",
        schedule={"kind": "interval", "minutes": 5},
        now=NOW - timedelta(days=1),
    )
    path = tmp_path / "cron/jobs.json"
    document = json.loads(path.read_text())
    del document["jobs"][0]["repeat"]["times"]
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.list()
    with pytest.raises(ValueError):
        once(store)
    with pytest.raises(ValueError):
        asyncio.run(Scheduler(tmp_path, Service()).tick(now=NOW))
    assert path.read_bytes() == before
