"""Offloaded journaling must preserve terminal results and lifecycle cleanup."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler
from kairos_observability import service_events
from kairos_web import server


def pause_first_write(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    write = service_events._write_all

    def paused(descriptor, line):
        if not entered.is_set():
            entered.set()
            release.wait(timeout=2)
        write(descriptor, line)

    monkeypatch.setattr(service_events, "_write_all", paused)
    return entered, release


@pytest.mark.parametrize("success", [True, False])
def test_cancellation_during_terminal_cron_journal_does_not_record_unknown(
    tmp_path, monkeypatch, success
):
    entered, release = pause_first_write(monkeypatch)
    store = JobStore(tmp_path)
    job = store.create(
        name="Scheduled",
        prompt="Content",
        schedule={"kind": "once", "run_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
    )

    class Service:
        async def stream(self, envelope):
            yield SimpleNamespace(kind="turn_end" if success else "turn_error")

    async def scenario():
        task = asyncio.create_task(Scheduler(tmp_path, Service()).tick())
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            assert not task.done(), "journal I/O must not block the event loop"
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            status = "completed" if success else "failed"
            assert store.history(job["id"])[0]["status"] == status
            assert [e["code"] for e in service_events.read_service_events(tmp_path)["events"]] == [
                "cron." + status
            ]
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_cancelled_startup_journal_still_closes_web_resources(tmp_path, monkeypatch):
    entered_write, release = pause_first_write(monkeypatch)
    closed = []

    class Service:
        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(server, "build_interaction_service", lambda home: Service())
    application = SimpleNamespace(state=SimpleNamespace(kairos_home=tmp_path))

    async def scenario():
        entered_context = False

        async def lifespan():
            nonlocal entered_context
            async with server._lifespan(application):
                entered_context = True
                await asyncio.Event().wait()

        task = asyncio.create_task(lifespan())
        try:
            while not entered_write.is_set():
                await asyncio.sleep(0.001)
            assert not entered_context, "startup must await the offloaded write"
            cron_task = application.state.cron_task
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed == [True]
            assert cron_task.done()
            assert not hasattr(application.state, "cron_task")
            assert not hasattr(application.state, "interaction_service")
            assert [e["code"] for e in service_events.read_service_events(tmp_path)["events"]] == [
                "web.stopped",
                "web.started",
            ]
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())
