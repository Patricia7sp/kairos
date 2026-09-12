"""Finite recurrence consumes durable reservations, including failed/unknown work."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
INTERVAL = {"kind": "interval", "minutes": 1}
CRON = {"kind": "cron", "expr": "* * * * *"}
ONCE = {"kind": "once", "run_at": NOW.isoformat()}


def create(store, *, times=None, schedule=None):
    return store.create(
        name="Bounded", prompt="Content", schedule=schedule or INTERVAL, times=times, now=NOW
    )


def change_job(store, **fields):
    document = json.loads(store.path.read_text())
    document["jobs"][0].update(fields)
    store.path.write_text(json.dumps(document))


class Service:
    def __init__(self, kind="turn_end"):
        self.kind = kind
        self.calls = []

    async def stream(self, envelope):
        self.calls.append(envelope)
        yield SimpleNamespace(kind=self.kind)


@pytest.mark.parametrize("schedule", [INTERVAL, CRON])
@pytest.mark.parametrize("times", [1, 2, 1_000_000, None])
def test_recurring_limit_is_persisted_and_reopened(tmp_path, schedule, times):
    store = JobStore(tmp_path)
    job = create(store, schedule=schedule, times=times)
    assert job["repeat"] == {"times": times, "completed": 0}
    assert JobStore(tmp_path).list()[0]["repeat"] == job["repeat"]


@pytest.mark.parametrize("schedule", [INTERVAL, CRON, ONCE])
@pytest.mark.parametrize("times", [0, -1, True, 1.0, "2", [], 1_000_001])
def test_invalid_limits_never_create_job_files(tmp_path, schedule, times):
    with pytest.raises(ValueError):
        create(JobStore(tmp_path), schedule=schedule, times=times)
    assert not (tmp_path / "cron/jobs.json").exists()
    assert not (tmp_path / "state.db").exists()


@pytest.mark.parametrize("times", [None, 1])
def test_once_keeps_exactly_one_reservation(tmp_path, times):
    store = JobStore(tmp_path)
    job = create(store, schedule=ONCE, times=times)
    assert job["repeat"]["times"] == 1
    assert store.claim(job["id"], NOW) is not None
    assert store.claim(job["id"], NOW + timedelta(days=1)) is None
    assert store.list()[0]["repeat"]["completed"] == 1


def test_once_rejects_multiple_occurrences(tmp_path):
    with pytest.raises(ValueError):
        create(JobStore(tmp_path), schedule=ONCE, times=2)


@pytest.mark.parametrize("schedule", [INTERVAL, CRON])
@pytest.mark.parametrize("outcome", ["turn_end", "turn_error", "delta"])
def test_two_reservations_stop_scheduler_even_when_turns_fail(tmp_path, schedule, outcome):
    store = JobStore(tmp_path)
    job = create(store, schedule=schedule, times=2)
    service = Service(outcome)
    for minute in (1, 2, 3):
        report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=minute)))
        assert report["executed"] == (1 if minute < 3 else 0)
    assert len(service.calls) == 2
    saved = JobStore(tmp_path).list()[0]
    assert saved["repeat"] == {"times": 2, "completed": 2}
    assert saved["next_run_at"] is None
    assert len(store.history(job["id"])) == 2
    expected = "completed" if outcome == "turn_end" else "failed"
    assert {row["status"] for row in store.history(job["id"])} == {expected}


def test_unknown_execution_consumes_limit_and_is_not_replayed(tmp_path):
    store = JobStore(tmp_path)
    job = create(store, times=2)
    assert store.claim(job["id"], NOW + timedelta(minutes=1)) is not None
    service = Service()
    scheduler = Scheduler(tmp_path, service)
    report = asyncio.run(scheduler.tick(now=NOW + timedelta(minutes=2)))
    assert report["recovered"] == 1
    assert report["executed"] == 1
    assert asyncio.run(scheduler.tick(now=NOW + timedelta(minutes=3)))["executed"] == 0
    assert len(service.calls) == 1
    assert {row["status"] for row in store.history(job["id"])} == {"unknown", "completed"}


def test_backlog_collapses_without_spending_missed_occurrences(tmp_path):
    store = JobStore(tmp_path)
    job = create(store, times=2)
    late = NOW + timedelta(days=5)
    assert store.claim(job["id"], late) is not None
    saved = store.list()[0]
    assert saved["repeat"]["completed"] == 1
    assert saved["next_run_at"] == (late + timedelta(minutes=1)).isoformat()
    assert store.claim(job["id"], late) is None
    assert len(store.history(job["id"])) == 1


def test_crash_after_ledger_insert_reconciles_duplicate_without_double_consumption(
    tmp_path, monkeypatch
):
    store = JobStore(tmp_path)
    job = create(store, times=2)
    initial_due = NOW + timedelta(minutes=1)

    def fail_write(document):
        raise OSError("simulated crash after ledger commit")

    monkeypatch.setattr(store, "_write", fail_write)
    with pytest.raises(OSError):
        store.claim(job["id"], initial_due)
    assert len(store.history(job["id"])) == 1
    assert json.loads(store.path.read_text())["jobs"][0]["repeat"]["completed"] == 0
    reopened = JobStore(tmp_path)
    assert reopened.claim(job["id"], initial_due) is None
    assert reopened.list()[0]["repeat"]["completed"] == 1
    service = Service()
    assert (
        asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=2)))["executed"]
        == 1
    )
    assert (
        asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=3)))["executed"]
        == 0
    )
    assert len(service.calls) == 1
    assert len(reopened.history(job["id"])) == 2


def test_crash_on_final_reservation_exhausts_from_ledger_before_next_insert(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    job = create(store, times=2)
    first = store.claim(job["id"], NOW + timedelta(minutes=1))
    store.finish(first[1], "completed")
    monkeypatch.setattr(store, "_write", lambda _: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        store.claim(job["id"], NOW + timedelta(minutes=2))
    reopened = JobStore(tmp_path)
    assert reopened.claim(job["id"], NOW + timedelta(days=1)) is None
    assert len(reopened.history(job["id"])) == 2
    assert reopened.list()[0]["repeat"] == {"times": 2, "completed": 2}
    assert reopened.list()[0]["next_run_at"] is None


def test_json_counter_ahead_of_ledger_is_not_decreased_or_double_counted(tmp_path):
    store = JobStore(tmp_path)
    job = create(store, times=3)
    first_due = NOW + timedelta(minutes=1)
    assert store.claim(job["id"], first_due) is not None
    change_job(store, repeat={"times": 3, "completed": 2}, next_run_at=first_due.isoformat())
    assert store.claim(job["id"], first_due) is None
    assert store.list()[0]["repeat"]["completed"] == 2
    assert len(store.history(job["id"])) == 1
    assert store.claim(job["id"], NOW + timedelta(minutes=2)) is not None
    assert store.list()[0]["repeat"]["completed"] == 3
    assert store.list()[0]["next_run_at"] is None
    assert store.claim(job["id"], NOW + timedelta(minutes=3)) is None


def test_pause_resume_preserves_remaining_budget_and_due_date(tmp_path):
    store = JobStore(tmp_path)
    job = create(store, times=2)
    store.claim(job["id"], NOW + timedelta(minutes=1))
    before = store.list()[0]
    store.set_paused(job["id"], True)
    assert store.claim(job["id"], NOW + timedelta(days=1)) is None
    resumed = JobStore(tmp_path).set_paused(job["id"], False)
    assert resumed["repeat"] == before["repeat"]
    assert resumed["next_run_at"] == before["next_run_at"]
    store.claim(job["id"], NOW + timedelta(days=2))
    store.set_paused(job["id"], True)
    resumed = store.set_paused(job["id"], False)
    assert resumed["next_run_at"] is None
    assert resumed["repeat"]["completed"] == 2


def test_resume_after_crash_cannot_revive_exhausted_budget(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    job = create(store, times=1)
    monkeypatch.setattr(store, "_write", lambda _: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        store.claim(job["id"], NOW + timedelta(minutes=1))
    reopened = JobStore(tmp_path)
    reopened.set_paused(job["id"], True)
    resumed = reopened.set_paused(job["id"], False)
    assert resumed["repeat"] == {"times": 1, "completed": 1}
    assert resumed["next_run_at"] is None
    assert reopened.claim(job["id"], NOW + timedelta(days=1)) is None


def test_existing_unlimited_jobs_continue_and_reconcile_legacy_zero_counter(tmp_path):
    store = JobStore(tmp_path)
    job = create(store)
    store.claim(job["id"], NOW + timedelta(minutes=1))
    change_job(store, repeat={"times": None, "completed": 0})
    for minute in (2, 3, 4):
        assert JobStore(tmp_path).claim(job["id"], NOW + timedelta(minutes=minute)) is not None
    saved = JobStore(tmp_path).list()[0]
    assert saved["repeat"] == {"times": None, "completed": 4}
    assert saved["next_run_at"] is not None


@pytest.mark.parametrize("times", [True, 0, -1, "2", 1.5, 1_000_001])
def test_invalid_persisted_budget_fails_without_rewriting(tmp_path, times):
    store = JobStore(tmp_path)
    job = create(store, times=2)
    change_job(store, repeat={"times": times, "completed": 0})
    original = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.claim(job["id"], NOW + timedelta(minutes=1))
    assert store.path.read_bytes() == original
    assert not (tmp_path / "state.db").exists()


def reserve_in_process(home, job_id):
    store = JobStore(home)
    for minute in range(1, 6):
        store.claim(job_id, NOW + timedelta(minutes=minute))


def test_two_processes_cannot_reserve_beyond_shared_limit(tmp_path):
    import multiprocessing

    store = JobStore(tmp_path)
    job = create(store, times=2)
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=reserve_in_process, args=(tmp_path, job["id"])) for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
    assert len(store.history(job["id"])) == 2
    assert store.list()[0]["repeat"] == {"times": 2, "completed": 2}
    assert store.list()[0]["next_run_at"] is None
