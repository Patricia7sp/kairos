"""Durable per-job notepad for scheduled jobs.

A tiny KV scratchpad each cron job can use to carry state across scheduled
wake-ups (cursors, watermarks, watchlists). Stored in ``state.db`` (migration
v4, table ``cron_notepad``), following the same connection/migrate pattern as
the executions ledger in ``kairos_cron.jobs``.

Size caps (documented contract):

- ``MAX_VALUE_BYTES`` (16 KiB): per-key value cap, measured in UTF-8 bytes.
- ``MAX_JOB_TOTAL_BYTES`` (64 KiB): per-job cap over the sum of key+value
  bytes. Oversized writes raise ``ValueError`` and leave the store untouched —
  the notepad is prompt-injected each run, so unbounded growth would bloat
  every wake-up's prompt.

The write path is the CLI (``kairos cron notepad <job_id> set <key> <value>``);
no model tool is added.
"""

from __future__ import annotations

from pathlib import Path

from kairos_state import connect, migrate

MAX_VALUE_BYTES = 16 * 1024
MAX_KEY_CHARS = 128
MAX_JOB_TOTAL_BYTES = 64 * 1024

_HEADER = "## Job notepad (persistent across runs)\n"
_INSTRUCTION = (
    "This durable scratchpad survives between scheduled runs of this job. "
    "Update it via the CLI, e.g.:\n"
)
_INSTRUCTION_TMPL = (
    "`kairos cron notepad {job_id} set <key> <value>` (also: get/delete/list;\n"
    "`kairos cron notepad {job_id} delete <key>` removes an entry).\n\n"
)


class NotepadStore:
    def __init__(self, home: Path):
        self.home = Path(home)

    def _db(self):
        db = connect(self.home / "state.db")
        try:
            migrate(db)
            return db
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _validate(job_id: str, key: str, value: str) -> None:
        if not job_id:
            raise ValueError("identificador de agendamento vazio")
        if not key:
            raise ValueError("chave vazia")
        if len(key) > MAX_KEY_CHARS:
            raise ValueError(f"chave longa demais (maximo {MAX_KEY_CHARS} caracteres)")
        if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError(f"valor grande demais (maximo {MAX_VALUE_BYTES} bytes por chave)")

    def set(self, job_id: str, key: str, value: str, *, now: str | None = None) -> dict:
        """Upsert uma chave. ``ValueError`` quando um limite de tamanho seria
        excedido — a operação inteira é descartada, sem escrita parcial."""
        job_id, key, value = str(job_id), str(key), str(value)
        self._validate(job_id, key, value)
        from datetime import UTC, datetime

        now = now or datetime.now(UTC).isoformat()
        with self._db() as db:
            total = db.execute(
                """SELECT COALESCE(SUM(LENGTH(CAST(key AS BLOB))
                     + LENGTH(CAST(value AS BLOB))), 0)
                   FROM cron_notepad WHERE job_id=? AND key<>?""",
                (job_id, key),
            ).fetchone()[0]
            entry_bytes = len(key.encode("utf-8")) + len(value.encode("utf-8"))
            if int(total) + entry_bytes > MAX_JOB_TOTAL_BYTES:
                raise ValueError(
                    f"bloco cheio: o job '{job_id}' excederia {MAX_JOB_TOTAL_BYTES} bytes; "
                    "apague chaves nao usadas antes"
                )
            db.execute(
                """INSERT INTO cron_notepad (job_id, key, value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id, key)
                   DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (job_id, key, value, now),
            )
        return {"job_id": job_id, "key": key, "value": value, "updated_at": now}

    def get(self, job_id: str, key: str) -> str | None:
        with self._db() as db:
            row = db.execute(
                "SELECT value FROM cron_notepad WHERE job_id=? AND key=?",
                (str(job_id), str(key)),
            ).fetchone()
        return None if row is None else row["value"]

    def delete(self, job_id: str, key: str) -> bool:
        with self._db() as db:
            cursor = db.execute(
                "DELETE FROM cron_notepad WHERE job_id=? AND key=?",
                (str(job_id), str(key)),
            )
        return cursor.rowcount > 0

    def list(self, job_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT job_id, key, value, updated_at FROM cron_notepad "
                "WHERE job_id=? ORDER BY key",
                (str(job_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self, job_id: str) -> int:
        with self._db() as db:
            cursor = db.execute("DELETE FROM cron_notepad WHERE job_id=?", (str(job_id),))
        return cursor.rowcount


def render_notepad_section(home: Path, job_id: str) -> str:
    """Seção de prompt do notepad de um job, ou ``''`` quando vazio/indisponível.

    Notepad vazio **precisa** devolver a string vazia: jobs que nunca usam a
    funcionalidade mantêm o prompt byte-idêntico (prompt-cache e deriva).
    """
    try:
        notes = NotepadStore(home).list(job_id)
    except Exception:  # noqa: BLE001 - injeção de prompt nunca deve derrubar o tick
        return ""
    if not notes:
        return ""
    lines = [f"- {note['key']}: {note['value']}" for note in notes]
    return (
        _HEADER + _INSTRUCTION + _INSTRUCTION_TMPL.format(job_id=job_id) + "\n".join(lines) + "\n\n"
    )
