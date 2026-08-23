"""Abertura de conexão e inicialização do ``state.db``.

Reconstruído a partir de ``_reversa_sdd/erd-complete.md`` §6 e
``_reversa_sdd/data-dictionary.md`` §6 (Tarefa 01).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from kairos_state import schema as _schema

__all__ = ["connect", "initialize_schema", "default_db_path", "CJKExtensionUnavailable"]


class CJKExtensionUnavailable(RuntimeError):
    """A extensão ``fts5_cjk`` não pôde ser carregada."""


def default_db_path() -> Path:
    """``$KAIROS_HOME/state.db``, com ``~/.kairos`` como padrão.

    ``KAIROS_HOME`` é o mecanismo de isolamento entre instalações
    concorrentes (unit ``hermes-cli``); a camada de estado apenas o respeita.
    """
    home = os.environ.get("KAIROS_HOME")
    root = Path(home).expanduser() if home else Path.home() / ".kairos"
    return root / "state.db"


def connect(db_path: str | os.PathLike[str] | None = None, *, timeout: float = 1.0) -> sqlite3.Connection:
    """Abre uma conexão com os pragmas de durabilidade e integridade.

    ``timeout`` fica deliberadamente **curto** (1 s). O handler de ocupado
    embutido do SQLite usa um escalonamento determinístico que produz efeito
    comboio sob concorrência alta; a paciência real é responsabilidade da
    escada de retry com jitter da camada de aplicação (unit ``hermes-state``,
    T-13), não deste timeout.
    """
    path = Path(db_path) if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.row_factory = sqlite3.Row

    # ``foreign_keys`` é OFF por padrão no SQLite, e é por CONEXÃO. Sem isto,
    # toda FK declarada no schema é decorativa. Herdado do legado
    # (hermes_state.py:3430) — a migração pode desligá-lo numa janela
    # controlada, mas o caminho normal sempre o liga.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")

    if sys.platform == "darwin":
        # Barreira de flush reforçada: no macOS, ``fsync`` não garante que os
        # dados deixaram o cache do disco; ``F_FULLFSYNC`` garante.
        conn.execute("PRAGMA fullfsync=ON")

    return conn


def initialize_schema(
    conn: sqlite3.Connection,
    *,
    deferred_indexes: bool = True,
    cjk: bool = False,
) -> int:
    """Cria tabelas, índices e FTS. Idempotente.

    ``cjk`` só deve ser ligado quando a extensão ``fts5_cjk`` (Tarefa 04)
    estiver carregada na conexão. O padrão é desligado, e a busca degrada
    para FTS5 base → trigram → ``LIKE``: **fail-open**, nunca fail-closed —
    um índice ausente reduz a qualidade da busca, não derruba o agente.
    """
    with conn:
        conn.executescript(_schema.SCHEMA_SQL)
        conn.executescript(_schema.FTS_SQL)
        conn.executescript(_schema.FTS_TRIGGERS)

        if deferred_indexes:
            conn.executescript(_schema.DEFERRED_INDEX_SQL)

        if cjk:
            try:
                conn.executescript(_schema.FTS_CJK_SQL)
                conn.executescript(_schema.FTS_CJK_TRIGGERS)
            except sqlite3.OperationalError as exc:
                raise CJKExtensionUnavailable(
                    "tokenizer 'cjk_unicode61' indisponível — a extensão fts5_cjk "
                    "não está carregada nesta conexão"
                ) from exc

        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (_schema.SCHEMA_VERSION,))
            return _schema.SCHEMA_VERSION
        return int(row["version"])


def read_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return None if row is None else int(row["version"])
