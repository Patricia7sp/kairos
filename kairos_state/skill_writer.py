"""Capability explícita dos clientes que entendem as duas famílias do journal."""

import sqlite3


def register_skill_writer(connection: sqlite3.Connection) -> None:
    connection.create_function("kairos_skill_writer_version", 0, lambda: 8)
