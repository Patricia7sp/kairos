"""Schema v5: links de leitura compartilhada de conversas.

Cada compartilhamento guarda apenas o hash do token (nunca o token em
texto claro), a sessão de origem e o ciclo de vida (criação, expiração
opcional e revogação). O intervalo entre criar e revogar é auditável —
a revogação é marca suave (``revoked_at``), não apaga histórico.
"""

from __future__ import annotations

import sqlite3

__all__ = ["SHARES_SCHEMA_SQL", "execute_schema"]


SHARES_SCHEMA_SQL = """
CREATE TABLE session_shares (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    expires_at REAL,
    revoked_at REAL
);
CREATE INDEX idx_session_shares_session ON session_shares(session_id);
CREATE INDEX idx_session_shares_active ON session_shares(revoked_at, expires_at);
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
