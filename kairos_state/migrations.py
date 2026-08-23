"""Migração de schema incremental e idempotente, e reparo de corrupção.

RF-17, RF-18. Reconstruído de `_reversa_sdd/hermes-state/` (Tarefa 05).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from kairos_state import schema as _schema
from kairos_state.connection import read_schema_version

__all__ = [
    "Migration",
    "MIGRATIONS",
    "migrate",
    "backup_corrupt_db",
    "is_corruption_error",
    "CORRUPTION_MARKERS",
]

#: Marcadores de dano estrutural. Distintos de "disco cheio" e de "ocupado":
#: liberar espaço não conserta um arquivo malformado, e repetir a escrita
#: também não — o caminho é reparo, não retry.
CORRUPTION_MARKERS = (
    "database disk image is malformed",
    "file is not a database",
    "file is encrypted or is not a database",
    "database corruption",
)


def is_corruption_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in CORRUPTION_MARKERS)


class Migration:
    def __init__(self, version: int, description: str,
                 apply: Callable[[sqlite3.Connection], None]) -> None:
        self.version = version
        self.description = description
        self.apply = apply


def _v1_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_schema.SCHEMA_SQL)
    conn.executescript(_schema.FTS_SQL)
    conn.executescript(_schema.FTS_TRIGGERS)


#: Cada degrau é aplicado **uma vez**, em ordem, e grava a versão na mesma
#: transação. Rodar duas vezes não altera o resultado (RF-17): a versão
#: registrada faz o segundo passe não encontrar degrau pendente.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "schema base — forma final do v26 do legado", _v1_base_schema),
)


def migrate(conn: sqlite3.Connection, *, target: int | None = None) -> int:
    """Aplica os degraus pendentes. Devolve a versão final.

    Idempotente por construção: cada degrau roda dentro de uma transação que
    também grava a nova versão. Interromper no meio deixa o banco na versão
    anterior, íntegro — nunca num estado intermediário sem nome.
    """
    ceiling = target if target is not None else _schema.SCHEMA_VERSION
    current = read_schema_version(conn)

    if current is None:
        current = 0
        with conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            conn.execute("INSERT INTO schema_version(version) VALUES (0)")

    for migration in MIGRATIONS:
        if migration.version <= current or migration.version > ceiling:
            continue
        with conn:
            migration.apply(conn)
            conn.execute("UPDATE schema_version SET version = ?", (migration.version,))
        current = migration.version

    return current


def backup_corrupt_db(db_path: str | os.PathLike[str], *, now: float | None = None) -> Path:
    """Preserva o arquivo danificado antes de recriar (RF-18).

    Nome: ``<arquivo>.zeroed-<timestamp>-<pid>.bak``, com sufixo ``-<n>`` em
    colisão. Reparar sem preservar destruiria a única cópia do transcript do
    usuário — e um banco malformado costuma ter a maior parte dos dados
    recuperável por ferramenta externa.
    """
    source = Path(db_path)
    stamp = int(now if now is not None else time.time())
    base = source.with_name(f"{source.name}.zeroed-{stamp}-{os.getpid()}.bak")

    target = base
    counter = 1
    while target.exists():
        target = source.with_name(f"{base.name}-{counter}")
        counter += 1

    shutil.copy2(source, target)
    return target
