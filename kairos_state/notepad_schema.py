"""Schema v4: durable per-job notepad scratchpad for scheduled jobs."""

from __future__ import annotations

import sqlite3

__all__ = ["NOTEPAD_SCHEMA_SQL", "execute_schema"]


NOTEPAD_SCHEMA_SQL = """
CREATE TABLE cron_notepad (
    job_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, key)
);
CREATE INDEX idx_cron_notepad_job ON cron_notepad(job_id, key);
"""


def execute_schema(conn: sqlite3.Connection, script: str) -> None:
    """Execute complete SQLite statements without ``executescript`` commits.

    Shared with the runtime schema so a multi-statement block migrates under
    a single transaction instead of the implicit commits of ``executescript``.
    """

    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete schema statement")
