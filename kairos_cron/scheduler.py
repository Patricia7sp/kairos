"""One scheduler per data volume, with bounded canonical model turns."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import aclosing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from kairos_cron.jobs import JobStore
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

    async def tick(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        report = {"busy": False, "executed": 0, "failed": 0, "recovered": 0}
        with tick_lock(self.home) as acquired:
            if not acquired:
                return {**report, "busy": True}
            report["recovered"] = self.store.recover()
            if report["recovered"]:
                await record_service_event_async(
                    self.home, "cron.unknown", results=report["recovered"]
                )
            for candidate in self.store.list():
                claimed = self.store.claim(candidate["id"], now)
                if claimed is None:
                    continue
                job, execution_id = claimed
                self.store.running(execution_id)
                envelope = InteractionEnvelope(
                    conversation_id="cron-" + execution_id, source="cron", content=job["prompt"]
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

    async def run(self, *, interval: float = 60) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - retry future ticks; preserve corrupt data and report unavailability
                self.last_error = "Não foi possível verificar os agendamentos."
                logger.error("scheduler tick failed; job definitions were preserved")
            await asyncio.sleep(interval)
