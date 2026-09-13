"""One scheduler per data volume, with bounded canonical model turns."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import aclosing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from kairos_cron.dispatch import is_job_runnable
from kairos_cron.jobs import JobStore, timestamp
from kairos_cron.monitor import (
    MonitorOutcome,
    decide_for_source,
    monitor_state_dict,
    monitor_state_from_job,
)
from kairos_cron.notepad import render_notepad_section
from kairos_integration.interaction_contract import InteractionEnvelope
from kairos_observability.service_events import record_service_event_async

logger = logging.getLogger(__name__)


@contextmanager
def tick_lock(home: Path):
    import fcntl

    directory = home / "cron"
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "tick.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


class Scheduler:
    def __init__(self, home: Path, service, *, timeout: float = 300):
        self.home = Path(home)
        self.store = JobStore(home)
        self.service = service
        self.timeout = timeout
        self.last_tick = None
        self.last_error = None

    async def tick(  # noqa: PLR0912, PLR0915 - um tick por job, alternando monitor e execução; separar romperia a atomicidade
        self, *, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(UTC)
        report = {
            "busy": False,
            "executed": 0,
            "failed": 0,
            "recovered": 0,
            "monitored": 0,
            "suppressed": 0,
            "source_errors": 0,
        }
        with tick_lock(self.home) as acquired:
            if not acquired:
                return {**report, "busy": True}
            report["recovered"] = self.store.recover()
            if report["recovered"]:
                await record_service_event_async(
                    self.home, "cron.unknown", results=report["recovered"]
                )
            for candidate in self.store.list():
                if candidate.get("monitor") is not None:
                    claimed = await self.run_monitor(candidate, now, report)
                else:
                    claimed = self.store.claim(candidate["id"], now)
                if claimed is None:
                    continue
                job, execution_id = claimed
                self.store.running(execution_id)
                content = job["prompt"]
                notepad_section = render_notepad_section(self.home, job["id"])
                if notepad_section:
                    content = notepad_section + content
                envelope = InteractionEnvelope(
                    conversation_id="cron-" + execution_id, source="cron", content=content
                )
                try:
                    success = False
                    failed = False
                    async with (
                        asyncio.timeout(self.timeout),
                        aclosing(self.service.stream(envelope)) as stream,
                    ):
                        async for event in stream:
                            if event.kind == "turn_error":
                                failed = True
                            elif event.kind == "turn_end":
                                success = True
                    if success and not failed:
                        self.store.finish(execution_id, "completed")
                        event_code = "cron.completed"
                    else:
                        self.store.finish(
                            execution_id,
                            "failed",
                            "O turno não terminou com sucesso; consulte a conversa.",
                        )
                        report["failed"] += 1
                        event_code = "cron.failed"
                except asyncio.CancelledError:
                    try:
                        self.store.finish(
                            execution_id,
                            "unknown",
                            "Execução interrompida; efeitos anteriores desconhecidos.",
                        )
                        await record_service_event_async(self.home, "cron.unknown")
                    except Exception:  # noqa: BLE001 - preserve cancellation; next tick recovers the durable row
                        logger.error("could not persist interrupted scheduler execution")
                    raise
                except Exception:  # noqa: BLE001 - one failed job must not stop other jobs; never expose secrets
                    self.store.finish(
                        execution_id,
                        "failed",
                        "Não foi possível concluir o turno; consulte a conversa.",
                    )
                    report["failed"] += 1
                    event_code = "cron.failed"
                await record_service_event_async(self.home, event_code)
                report["executed"] += 1
            self.last_tick = now.isoformat()
            self.last_error = None
        return report

    @staticmethod
    def _monitor_due(job: dict, now: datetime) -> bool:
        """A fonte só roda para job devido **e** executável — mesma condição
        que o claim aplicaria. Evita consumir a cadência por um job pausado ou
        esgotado, e evita disparar a fonte antes do horário."""
        if not is_job_runnable(enabled=job["enabled"], paused=job["paused"]):
            return False
        due = job.get("next_run_at")
        return bool(due) and timestamp(due) <= now

    async def run_monitor(self, job: dict, now: datetime, report: dict) -> tuple[dict, str] | None:
        """Roda a fonte do monitor e decide se o agente roda.

        A fonte roda **primeiro**, em thread própria; só para tick suprimido
        (``no_change``/`source_error`) a agenda avança sem consumir orçamento
        nem criar ocorrência no ledger.
        """
        from kairos_cron.source import run_script

        if not self._monitor_due(job, now):
            return None
        source_result = await asyncio.to_thread(
            run_script, job["monitor"]["script"], home=self.home
        )
        decision = decide_for_source(
            monitor_state_from_job(job), source_result, now=now.isoformat()
        )
        if decision.outcome in (MonitorOutcome.NO_CHANGE, MonitorOutcome.SOURCE_ERROR):
            self.store.record_suppressed_tick(job["id"], now=now)
            if decision.outcome is MonitorOutcome.NO_CHANGE:
                report["suppressed"] += 1
                await record_service_event_async(self.home, "cron.no_change")
            else:
                report["source_errors"] += 1
                await record_service_event_async(self.home, "cron.monitor_error", results=1)
            return None
        next_state = monitor_state_dict(decision.next_state, checked_at=now.isoformat())
        claimed = self.store.claim(job["id"], now, monitor_state=next_state)
        if claimed is None:
            return None
        report["monitored"] += 1
        return claimed

    async def run(self, *, interval: float = 60) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - retry future ticks; preserve corrupt data and report unavailability
                self.last_error = "Não foi possível verificar os agendamentos."
                logger.error("scheduler tick failed; job definitions were preserved")
            await asyncio.sleep(interval)
