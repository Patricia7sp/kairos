"""Atomic JSON desired state and an occurrence ledger in the canonical database."""

from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from kairos_cron.dispatch import reject_gateway_restart_job
from kairos_cron.schedule import compute_next_run
from kairos_security.credentials.io import credential_file_lock, secure_atomic_write_text
from kairos_state import connect, migrate


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
        reject_gateway_restart_job(job["prompt"])
        if job["next_run_at"] is not None:
            timestamp(job["next_run_at"])
        repeat = job["repeat"]
        if (
            not isinstance(repeat, dict)
            or type(repeat["completed"]) is not int
            or repeat["completed"] < 0
        ):
            raise ValueError("invalid repeat counter")
        expected_times = 1 if job["schedule"]["kind"] == "once" else None
        if (
            "times" not in repeat
            or repeat["times"] != expected_times
            or (repeat["times"] is not None and type(repeat["times"]) is not int)
        ):
            raise ValueError("unsupported repeat budget")

    def _write(self, document: dict) -> None:
        secure_atomic_write_text(self.path, json.dumps(document, ensure_ascii=False, indent=2))

    def list(self) -> list[dict]:
        return self._read()["jobs"]

    def create(
        self, *, name: str, prompt: str, schedule: dict, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(UTC)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("nome deve conter de 1 a 120 caracteres")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 32000:
            raise ValueError("instrução deve conter de 1 a 32000 caracteres")
        reject_gateway_restart_job(prompt)
        schedule = validate_schedule(schedule, now)
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
            "repeat": {"times": 1 if schedule["kind"] == "once" else None, "completed": 0},
            "next_run_at": next_run,
            "created_at": now.isoformat(),
            "last_run_at": None,
        }
        with credential_file_lock(self.path):
            document = self._read()
            document["jobs"].append(job)
            self._write(document)
        return job

    def set_paused(self, job_id: str, paused: bool) -> dict:
        if type(paused) is not bool:
            raise ValueError("pausa deve ser booleana")
        with credential_file_lock(self.path):
            document = self._read()
            job = self._find(document, job_id)
            job.update(paused=paused, enabled=not paused)
            self._write(document)
            return job

    def remove(self, job_id: str) -> None:
        with credential_file_lock(self.path):
            document = self._read()
            job = self._find(document, job_id)
            document["jobs"].remove(job)
            self._write(document)

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

    def claim(self, job_id: str, now: datetime) -> tuple[dict, str] | None:
        # Caller holds the tick lock for the entire effect. JSON and SQLite cannot
        # commit together: the unique occurrence ledger is written FIRST. A crash
        # between stores can consume an occurrence, but never execute it twice.
        with credential_file_lock(self.path):
            document = self._read()
            try:
                job = self._find(document, job_id)
            except KeyError:
                return None
            due = job["next_run_at"]
            if not job["enabled"] or job["paused"] or due is None or timestamp(due) > now:
                return None
            repeat = job["repeat"]
            if repeat["times"] is not None and repeat["completed"] >= repeat["times"]:
                return None
            execution_id = str(uuid4())
            with closing(self._db()) as db, db:
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
            job["last_run_at"] = now.isoformat()
            job["next_run_at"] = compute_next_run(job["schedule"], now.isoformat())
            if job["schedule"]["kind"] == "once":
                job["repeat"]["completed"] = 1
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
