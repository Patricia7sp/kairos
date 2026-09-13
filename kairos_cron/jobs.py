"""Atomic JSON desired state and an occurrence ledger in the canonical database."""

from __future__ import annotations

import json
import logging
import os
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from kairos_cron.lifecycle_guard import check_gateway_lifecycle
from kairos_cron.monitor import default_monitor_state, validate_monitor, validate_monitor_state
from kairos_cron.schedule import compute_next_run
from kairos_security.credentials.io import credential_file_lock, secure_atomic_write_text
from kairos_state import connect, migrate

logger = logging.getLogger(__name__)


def timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise ValueError("data exige formato ISO com fuso horário") from exc


def validate_schedule(schedule: dict, now: datetime) -> dict:
    if not isinstance(schedule, dict):
        raise ValueError("agenda inválida")
    kind = schedule.get("kind")
    if kind == "once" and set(schedule) == {"kind", "run_at"}:
        return {"kind": kind, "run_at": timestamp(schedule["run_at"]).isoformat()}
    if kind == "interval" and set(schedule) == {"kind", "minutes"}:
        minutes = schedule["minutes"]
        if type(minutes) is int and 1 <= minutes <= 525600:
            return dict(schedule)
    if kind == "cron" and set(schedule) == {"kind", "expr"}:
        expr = schedule["expr"]
        if (
            isinstance(expr, str)
            and len(expr) <= 200
            and len(expr.split()) == 5
            and compute_next_run(schedule, now.isoformat()) is not None
        ):
            return dict(schedule)
    raise ValueError("agenda inválida ou croniter indisponível")


def _repeat_limit(kind: str, times: int | None) -> int | None:
    if times is not None and (type(times) is not int or not 1 <= times <= 1_000_000):
        raise ValueError("limite de ocorrências deve ser inteiro entre 1 e 1000000")
    if kind == "once":
        if times not in (None, 1):
            raise ValueError("agendamento único permite somente uma ocorrência")
        return 1
    return times


class JobStore:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.path = self.home / "cron/jobs.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "jobs": []}
        try:
            document = json.loads(self.path.read_text())
            if (
                not isinstance(document, dict)
                or type(document.get("version")) is not int
                or document.get("version") != 1
                or not isinstance(document.get("jobs"), list)
            ):
                raise ValueError("invalid document")
            ids = set()
            for job in document["jobs"]:
                self._validate_job(job)
                if job["id"] in ids:
                    raise ValueError("duplicate identifier")
                ids.add(job["id"])
            return document
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("arquivo de agendamentos inválido; conteúdo preservado") from exc

    @staticmethod
    def _validate_job(job: dict) -> None:
        if not isinstance(job, dict):
            raise ValueError("invalid job")
        for key, limit in (("id", 128), ("name", 120), ("prompt", 32000)):
            if not isinstance(job[key], str) or not 1 <= len(job[key].strip()) <= limit:
                raise ValueError("invalid text")
        if type(job["enabled"]) is not bool or type(job["paused"]) is not bool:
            raise ValueError("invalid state")
        validate_schedule(job["schedule"], datetime.now(UTC))
        if job["schedule"]["kind"] == "once" and job.get("monitor") is not None:
            raise ValueError("monitor exige agendamento recorrente")
        if job["next_run_at"] is not None:
            timestamp(job["next_run_at"])
        repeat = job["repeat"]
        if (
            not isinstance(repeat, dict)
            or type(repeat["completed"]) is not int
            or repeat["completed"] < 0
        ):
            raise ValueError("invalid repeat counter")
        if (
            "times" not in repeat
            or _repeat_limit(job["schedule"]["kind"], repeat["times"]) != repeat["times"]
        ):
            raise ValueError("invalid repeat budget")
        if job.get("monitor") is not None:
            validate_monitor(job["monitor"])
        if job.get("monitor_state") is not None:
            validate_monitor_state(job["monitor_state"])

    def _write(self, document: dict) -> None:
        secure_atomic_write_text(self.path, json.dumps(document, ensure_ascii=False, indent=2))

    def list(self) -> list[dict]:
        return self._read()["jobs"]

    def get(self, job_id: str) -> dict:
        """Um job, ou KeyError imediato — melhor erro para o operador na hora."""
        with credential_file_lock(self.path):
            return self._find(self._read(), job_id)

    def create(
        self,
        *,
        name: str,
        prompt: str,
        schedule: dict,
        now: datetime | None = None,
        times: int | None = None,
        monitor: dict | None = None,
    ) -> dict:
        now = now or datetime.now(UTC)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("nome deve conter de 1 a 120 caracteres")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 32000:
            raise ValueError("instrução deve conter de 1 a 32000 caracteres")
        schedule = validate_schedule(schedule, now)
        if monitor is not None:
            if schedule["kind"] == "once":
                raise ValueError("monitor exige agendamento recorrente")
            monitor = validate_monitor(monitor)
        check_gateway_lifecycle(prompt, monitor["script"] if monitor is not None else None)
        times = _repeat_limit(schedule["kind"], times)
        next_run = (
            schedule["run_at"]
            if schedule["kind"] == "once"
            else compute_next_run(schedule, now.isoformat())
        )
        job = {
            "id": str(uuid4()),
            "name": name.strip(),
            "prompt": prompt.strip(),
            "schedule": schedule,
            "enabled": True,
            "paused": False,
            "repeat": {"times": times, "completed": 0},
            "next_run_at": next_run,
            "created_at": now.isoformat(),
            "last_run_at": None,
            "monitor": monitor,
            "monitor_state": default_monitor_state() if monitor is not None else None,
        }
        with credential_file_lock(self.path):
            document = self._read()
            document["jobs"].append(job)
            self._write(document)
        return job

    def set_monitor(self, job_id: str, script: str) -> dict:
        check_gateway_lifecycle(script)
        monitor = validate_monitor({"type": "script", "script": script})
        with credential_file_lock(self.path):
            document = self._read()
            job = self._find(document, job_id)
            if job["schedule"]["kind"] == "once":
                raise ValueError("monitor exige agendamento recorrente")
            job["monitor"] = monitor
            job["monitor_state"] = default_monitor_state()
            self._write(document)
            return job

    def clear_monitor(self, job_id: str) -> dict:
        with credential_file_lock(self.path):
            document = self._read()
            job = self._find(document, job_id)
            if "monitor" not in job:
                raise KeyError("agendamento não monitorado")
            job.pop("monitor", None)
            job["monitor_state"] = None
            self._write(document)
            return job

    def set_paused(self, job_id: str, paused: bool) -> dict:
        if type(paused) is not bool:
            raise ValueError("pausa deve ser booleana")
        with credential_file_lock(self.path):
            document = self._read()
            job = self._find(document, job_id)
            with closing(self._db()) as db:
                self._reconcile_repeat(job, db)
            job.update(paused=paused, enabled=not paused)
            self._write(document)
            return job

    def remove(self, job_id: str) -> None:
        with credential_file_lock(self.path):
            document = self._read()
            job = self._find(document, job_id)
            document["jobs"].remove(job)
            self._write(document)
        # Best effort: um bloco órfão após remover o job não pode bloquear a
        # remoção. Só abre o banco canônico se já existir, para não criar
        # `state.db` num volume virgem só por causa de uma remoção.
        notepad_db = self.home / "state.db"
        if notepad_db.exists():
            try:
                from kairos_cron.notepad import NotepadStore

                NotepadStore(self.home).clear(job_id)
            except Exception:  # noqa: BLE001 - remoção é o que conta; posto órfão é aceitável
                logger.debug("could not clear notepad for removed job %s", job_id)

    @staticmethod
    def _find(document: dict, job_id: str) -> dict:
        for job in document["jobs"]:
            if job["id"] == job_id:
                return job
        raise KeyError("agendamento não encontrado")

    def _db(self):
        db = connect(self.home / "state.db")
        try:
            migrate(db)
            return db
        except BaseException:
            db.close()
            raise

    def history(self, job_id: str | None = None, *, limit: int = 100) -> list[dict]:
        with closing(self._db()) as db:
            query = "SELECT id,job_id,status,claimed_at,started_at,finished_at,error,conversation_id FROM executions"
            params = []
            if job_id is not None:
                query += " WHERE job_id=?"
                params.append(job_id)
            query += " ORDER BY claimed_at DESC,id DESC LIMIT ?"
            params.append(min(max(limit, 1), 500))
            return [dict(row) for row in db.execute(query, params)]

    def recover(self) -> int:
        # Caller owns the cross-process tick lock. No live executor can own a row,
        # even when containers see different PID namespaces on the same data volume.
        with closing(self._db()) as db, db:
            cursor = db.execute(
                "UPDATE executions SET status='unknown',finished_at=?,error=? WHERE status IN ('claimed','running')",
                (
                    datetime.now(UTC).isoformat(),
                    "Execução interrompida; efeitos anteriores desconhecidos.",
                ),
            )
            return cursor.rowcount

    @staticmethod
    def _reconcile_repeat(job: dict, db) -> bool:
        repeat = job["repeat"]
        before = (repeat["completed"], job["next_run_at"])
        reserved = db.execute(
            "SELECT COUNT(*) FROM executions WHERE job_id=?", (job["id"],)
        ).fetchone()[0]
        repeat["completed"] = max(repeat["completed"], reserved)
        if repeat["times"] is not None and repeat["completed"] >= repeat["times"]:
            job["next_run_at"] = None
        return before != (repeat["completed"], job["next_run_at"])

    def record_suppressed_tick(self, job_id: str, *, now: datetime) -> dict:
        """Tick de monitor que **não** executou o agente.

        A agenda avança pelo próximo horário da cadência sem consumir o
        orçamento e sem criar linha no ledger: o tick vira ``no_change`` ou
        erro de fonte, nunca uma ocorrência reivindicada. A última verificação
        fica registrada para a UI e o CLI.
        """
        with credential_file_lock(self.path):
            document = self._read()
            job = self._find(document, job_id)
            if "monitor" not in job:
                raise KeyError("agendamento não monitorado")
            if job.get("monitor_state") is None:
                job["monitor_state"] = default_monitor_state()
            state = validate_monitor_state(job["monitor_state"])
            state["last_checked_at"] = now.isoformat()
            job["monitor_state"] = state
            if job["enabled"] and not job["paused"] and job["next_run_at"] is not None:
                job["next_run_at"] = compute_next_run(job["schedule"], now.isoformat())
            self._write(document)
            return job

    def claim(
        self, job_id: str, now: datetime, *, monitor_state: dict | None = None
    ) -> tuple[dict, str] | None:
        # Caller holds the tick lock for the entire effect. JSON and SQLite cannot
        # commit together: the unique occurrence ledger is written FIRST. A crash
        # between stores can consume an occurrence, but never execute it twice.
        with credential_file_lock(self.path):
            document = self._read()
            try:
                job = self._find(document, job_id)
            except KeyError:
                return None
            with closing(self._db()) as db, db:
                changed = self._reconcile_repeat(job, db)
                due = job["next_run_at"]
                if not job["enabled"] or job["paused"] or due is None or timestamp(due) > now:
                    if changed:
                        self._write(document)
                    return None
                execution_id = str(uuid4())
                cursor = db.execute(
                    """INSERT OR IGNORE INTO executions
                    (id,job_id,source,process_id,pid,status,scheduled_at,claimed_at,conversation_id)
                    VALUES (?,?,?,?,?,'claimed',?,?,?)""",
                    (
                        execution_id,
                        job_id,
                        "cron",
                        str(os.getpid()),
                        os.getpid(),
                        due,
                        now.isoformat(),
                        "cron-" + execution_id,
                    ),
                )
                created = cursor.rowcount == 1
            repeat = job["repeat"]
            if created:
                repeat["completed"] += 1
            job["last_run_at"] = now.isoformat()
            job["next_run_at"] = (
                None
                if repeat["times"] is not None and repeat["completed"] >= repeat["times"]
                else compute_next_run(job["schedule"], now.isoformat())
            )
            if created and monitor_state is not None:
                job["monitor_state"] = validate_monitor_state(monitor_state)
            self._write(document)
            return (job, execution_id) if created else None

    def running(self, execution_id: str) -> None:
        with closing(self._db()) as db, db:
            db.execute(
                "UPDATE executions SET status='running',started_at=? WHERE id=? AND status='claimed'",
                (datetime.now(UTC).isoformat(), execution_id),
            )

    def finish(self, execution_id: str, status: str, error: str | None = None) -> None:
        if status not in ("completed", "failed", "unknown"):
            raise ValueError("estado terminal inválido")
        with closing(self._db()) as db, db:
            db.execute(
                "UPDATE executions SET status=?,finished_at=?,error=? WHERE id=? AND status IN ('claimed','running')",
                (status, datetime.now(UTC).isoformat(), error, execution_id),
            )
