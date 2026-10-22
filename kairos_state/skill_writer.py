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
    observed = rows[0][0] if len(rows) == 1 and type(rows[0][0]) is int else 0
    if observed == 11:
        from kairos_state.skill_publications_schema import validate_publication_schema

        connection.create_function("kairos_skill_writer_version", 0, lambda: 0)
        validate_publication_schema(connection)
    version = 11 if observed == 11 else (10 if observed >= 10 else (9 if observed >= 9 else 8))
    connection.create_function("kairos_skill_writer_version", 0, lambda: version)
