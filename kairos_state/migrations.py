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
    "CANONICAL_TABLES",
    "DERIVED_OBJECTS",
    "CanonicalRowsModified",
    "canonical_fingerprint",
    "repair_derived_objects",
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


# ---------------------------------------------------------------------------
# Invariante 6 — o reparo NUNCA modifica linha canônica
# ---------------------------------------------------------------------------

#: Tabelas cujas linhas são o dado do usuário. O reparo pode recriar índices,
#: gatilhos, tabelas FTS e objetos derivados — nunca tocar nestas.
CANONICAL_TABLES = frozenset({"sessions", "messages", "system_prompts"})

#: Derivados: reconstrutíveis a partir das canônicas, e portanto descartáveis
#: no reparo.
DERIVED_OBJECTS = frozenset({
    "messages_fts", "messages_fts_trigram", "messages_fts_cjk",
    "messages_fts_trigram_src", "messages_fts_cjk_src",
})


class CanonicalRowsModified(RuntimeError):
    """Invariante 6 violado: o reparo mexeu em dado do usuário."""


def canonical_fingerprint(conn: sqlite3.Connection) -> dict[str, tuple[int, int | None]]:
    """`(contagem, max(rowid))` por tabela canônica.

    É barato e suficiente: qualquer `DELETE`, `INSERT` ou re-sequenciamento
    move um dos dois. Não pretende detectar edição de conteúdo em linha
    existente — o reparo não tem caminho que faça isso, e um hash de conteúdo
    inteiro custaria uma varredura completa a cada verificação.
    """
    out: dict[str, tuple[int, int | None]] = {}
    for table in sorted(CANONICAL_TABLES):
        try:
            row = conn.execute(
                f"SELECT COUNT(*), MAX(rowid) FROM {table}"  # noqa: S608 — nome de tabela fixo
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        out[table] = (int(row[0]), row[1])
    return out


def repair_derived_objects(conn: sqlite3.Connection) -> list[str]:
    """Recria os objetos derivados. **Não toca em linha canônica.**

    O reparo é o caminho em que essa distinção mais importa: um índice FTS
    corrompido é reconstruível, mas o transcript não. Confundir os dois num
    momento de pânico é como se perde o dado do usuário.

    Devolve os objetos recriados, e levanta se a impressão digital canônica
    mudou — a verificação é a imposição, não um comentário.
    """
    antes = canonical_fingerprint(conn)

    recriados: list[str] = []
    with conn:
        for obj in ("messages_fts_cjk", "messages_fts_trigram", "messages_fts"):
            existe = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (obj,)
            ).fetchone()
            if existe:
                conn.execute(f"DROP TABLE IF EXISTS {obj}")
                recriados.append(obj)
        for view in ("messages_fts_trigram_src", "messages_fts_cjk_src"):
            conn.execute(f"DROP VIEW IF EXISTS {view}")
        conn.executescript(_schema.FTS_SQL)
        conn.executescript(_schema.FTS_TRIGGERS)

    depois = canonical_fingerprint(conn)
    if antes != depois:
        raise CanonicalRowsModified(
            f"o reparo alterou linhas canônicas: antes={antes} depois={depois}"
        )
    return recriados
