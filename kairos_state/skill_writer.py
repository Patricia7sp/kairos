"""Capability explícita dos clientes que entendem a autoridade revogável."""

import sqlite3


def register_skill_writer(connection: sqlite3.Connection) -> None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='schema_version'"
    ).fetchone()
    rows = (
        []
        if exists is None
        else connection.execute("SELECT version FROM schema_version").fetchall()
    )
    version = 9 if len(rows) == 1 and type(rows[0][0]) is int and rows[0][0] >= 9 else 8
    connection.create_function("kairos_skill_writer_version", 0, lambda: version)
