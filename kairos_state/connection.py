"""Abertura de conexão e inicialização do ``state.db``.

Reconstruído a partir de ``_reversa_sdd/erd-complete.md`` §6 e
``_reversa_sdd/data-dictionary.md`` §6 (Tarefa 01).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

from kairos_state import schema as _schema

__all__ = [
    "connect",
    "initialize_schema",
    "default_db_path",
    "CJKExtensionUnavailable",
    "apply_wal_with_fallback",
    "read_connection",
    "BUSY_TIMEOUT_MS",
]

#: Curto de propósito: a paciência real é da escada de retry da aplicação
#: (``kairos_state.writes``), não deste timeout. O handler embutido do SQLite
#: escalona de forma determinística e produz efeito comboio.
BUSY_TIMEOUT_MS = 1_000


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
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    apply_wal_with_fallback(conn)

    if sys.platform == "darwin":
        # Barreira de flush reforçada: no macOS o ``fsync`` simples não
        # garante que os dados deixaram o cache do disco; ``F_FULLFSYNC``
        # garante. Sem isto, `synchronous=FULL` promete mais do que cumpre.
        conn.execute("PRAGMA fullfsync=ON")

    return conn


def apply_wal_with_fallback(conn: sqlite3.Connection, *, db_label: str = "state.db") -> str:
    """Aplica WAL quando o sistema de arquivos suporta; senão, cai para DELETE.

    🔧 [Tarefa 05] Corrige a Tarefa 01, que aplicava WAL incondicionalmente.
    WAL exige memória compartilhada e falha em NFS, em alguns FUSE e em
    montagens de rede — casos reais, não exóticos: o `state.db` num diretório
    home montado por rede é configuração comum em ambiente corporativo.

    Cair para ``DELETE`` (o default pré-WAL) degrada a concorrência mas
    mantém a durabilidade. Levantar aqui tornaria o Kairos inutilizável nesses
    hosts por causa de uma otimização.

    Devolve o modo efetivamente em vigor.
    """
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = (row[0] if row else "").lower()
    except sqlite3.OperationalError:
        mode = ""

    if mode != "wal":
        # Não insistir: flipar journal_mode com outras conexões abertas é
        # caminho conhecido de corrupção.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        return "delete"

    # FULL é o default sob WAL neste projeto: perder o último turno num
    # crash de máquina é pior que o custo de fsync.
    conn.execute("PRAGMA synchronous=FULL")
    return "wal"


@contextmanager
def read_connection(db_path: str | os.PathLike[str] | None = None):
    """Conexão **somente leitura**, separada da de escrita (RF-03).

    Sob WAL, leitores não bloqueiam o escritor nem são bloqueados por ele:
    cada leitor vê um snapshot consistente do início da sua transação. Manter
    a leitura fora da conexão transacional de escrita é o que permite ao
    dashboard listar sessões enquanto um turno grava, sem nenhum dos dois
    esperar.

    Abre em modo ``ro`` via URI para que uma escrita acidental por este
    caminho falhe alto, em vez de disputar o lock silenciosamente.
    """
    path = Path(db_path) if db_path is not None else default_db_path()
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        yield conn
    finally:
        conn.close()


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
    """A versão registrada, ou ``None``.

    ``None`` cobre os dois casos em que não há versão: tabela ausente (banco
    virgem) e tabela vazia. O migrador precisa dessa tolerância — ele roda
    justamente antes de a tabela existir.
    """
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else int(row["version"])
