"""Carga, gate e backfill do índice CJK.

Reconstruído de ``_reversa_sdd/native/`` (Tarefa 04).

**A regra que governa este módulo é a degradação.** A extensão é opcional e
sempre foi: a maioria das instalações não terá o ``.so`` compilado. Toda
operação do caminho CJK é condicionada a ``is_loaded``, e a ausência resulta
em busca mais pobre — nunca em erro.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kairos_state import schema as _schema

__all__ = [
    "CJK_SO_ENV",
    "CJK_SO_BASENAME",
    "find_extension",
    "load_extension",
    "is_loaded",
    "cjk_enabled",
    "ensure_cjk_index",
    "drop_cjk_index",
    "RebuildStatus",
    "rebuild_status",
    "rebuild_step",
]

#: Sobrescreve a localização do ``.so``, para empacotamento alternativo.
CJK_SO_ENV = "KAIROS_FTS5_CJK_SO"
CJK_SO_BASENAME = "libfts5_cjk.so"

#: Chave de meta que guarda o progresso do backfill.
_PROGRESS_KEY = "fts_cjk_rebuild_progress"


def _kairos_home() -> Path:
    home = os.environ.get("KAIROS_HOME")
    return Path(home).expanduser() if home else Path.home() / ".kairos"


def find_extension() -> Path | None:
    """Onde está o ``.so``, ou ``None``.

    Precedência: variável de ambiente primeiro, porque é o mecanismo de
    override — se ela aponta para um caminho inexistente, isso é um erro de
    configuração que deve aparecer, não ser silenciosamente contornado pelo
    caminho padrão.
    """
    override = os.environ.get(CJK_SO_ENV)
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None

    default = _kairos_home() / "lib" / CJK_SO_BASENAME
    return default if default.is_file() else None


def cjk_enabled(config: dict | None = None) -> bool:
    """O gate de configuração: ``sessions.cjk_fts``.

    Default é ligado. Desligar é escape hatch operacional — existe para o
    caso de a extensão causar problema, não como opt-in.
    """
    if not config:
        return True
    sessions = config.get("sessions") or {}
    value = sessions.get("cjk_fts", True)
    return bool(value)


def load_extension(conn: sqlite3.Connection, *, config: dict | None = None) -> bool:
    """Tenta carregar a extensão. Devolve se o caminho CJK está disponível.

    **Nunca levanta.** Um ``.so`` ausente, ilegível, compilado para outra
    arquitetura ou contra um SQLite incompatível resulta em ``False`` — e a
    busca segue pelas outras rotas. Falhar aqui derrubaria a abertura do
    banco por causa de um índice opcional.
    """
    if not cjk_enabled(config):
        return False

    path = find_extension()
    if path is None:
        return False

    try:
        conn.enable_load_extension(True)
        conn.load_extension(str(path))
    except (sqlite3.OperationalError, AttributeError):
        # AttributeError: build do Python sem suporte a extensão carregável.
        return False
    finally:
        try:
            conn.enable_load_extension(False)
        except (sqlite3.OperationalError, AttributeError):
            pass

    return is_loaded(conn)


def is_loaded(conn: sqlite3.Connection) -> bool:
    """O tokenizador está registrado NESTA conexão?

    Testado criando uma tabela temporária — é a única checagem honesta, já
    que o registro é por conexão e um ``load_extension`` bem-sucedido não
    garante que o tokenizador foi criado.
    """
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE temp._cjk_probe USING fts5(c, tokenize='cjk_unicode61')"
        )
    except sqlite3.OperationalError:
        return False
    else:
        conn.execute("DROP TABLE temp._cjk_probe")
        return True


def ensure_cjk_index(conn: sqlite3.Connection) -> bool:
    """Cria índice, view e gatilhos CJK. Ativação **por presença**.

    Idempotente. Devolve ``False`` sem efeito se o tokenizador não estiver
    carregado nesta conexão.
    """
    if not is_loaded(conn):
        return False

    novo = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='messages_fts_cjk'"
    ).fetchone() is None

    with conn:
        conn.executescript(_schema.FTS_CJK_SQL)
        conn.executescript(_schema.FTS_CJK_TRIGGERS)
        if novo:
            # Fixa a fronteira com o gatilho no maior id existente. Num banco
            # vazio isto dá -1 e o gatilho cobre tudo — sem backfill a fazer.
            # Num banco populado, separa o que é do gatilho do que é do
            # backfill, para que os dois nunca disputem a mesma linha.
            fronteira = conn.execute(
                "SELECT COALESCE(MAX(id), -1) FROM messages WHERE role <> 'tool'"
            ).fetchone()[0]
            _set_meta(conn, _schema.FTS_CJK_HIGH_WATER_KEY, str(fronteira))
            _set_meta(conn, _CURSOR_KEY, "-1")
    return True


def drop_cjk_index(conn: sqlite3.Connection) -> None:
    """Remoção limpa: nem índice, nem view, nem gatilho, nem meta.

    Não exige a extensão — desativar precisa funcionar justamente quando o
    ``.so`` foi removido.
    """
    with conn:
        for trigger in ("insert", "delete", "update"):
            conn.execute(f"DROP TRIGGER IF EXISTS messages_fts_cjk_{trigger}")
        conn.execute("DROP TABLE IF EXISTS messages_fts_cjk")
        conn.execute("DROP VIEW IF EXISTS messages_fts_cjk_src")
        conn.execute(
            "DELETE FROM state_meta WHERE key IN (?, ?, ?, ?)",
            (_schema.FTS_CJK_HIGH_WATER_KEY, _CURSOR_KEY, _PROGRESS_KEY,
             _schema.FTS_CJK_STALE_KEY),
        )


# ---------------------------------------------------------------------------
# Backfill retomável
# ---------------------------------------------------------------------------
#
# A DIVISÃO DE TRABALHO, que é o ponto sutil desta unit:
#
#   id  >  high_water   →  responsabilidade do GATILHO (indexação ao vivo)
#   id  <= high_water   →  responsabilidade do BACKFILL
#
# O gatilho de INSERT do schema consulta ``fts_cjk_rebuild_high_water`` e só
# indexa acima dela. Então a marca não é "até onde o backfill chegou" — é a
# **fronteira fixa** entre os dois, definida no instante em que o índice
# nasce sobre um banco já populado.
#
# Num banco vazio a marca fica em -1 e o gatilho cobre tudo: não há backfill
# a fazer. Num banco populado, ``ensure_cjk_index`` fixa a marca no maior id
# existente, e daí em diante mensagens novas entram ao vivo enquanto o
# backfill caminha por trás, sem que os dois se cruzem.
#
# O avanço do backfill é rastreado por um CURSOR separado
# (``fts_cjk_rebuild_cursor``) — confundi-lo com a marca faz o backfill
# reindexar exatamente as linhas que o gatilho já inseriu, e o índice recusa
# com "constraint failed".

_CURSOR_KEY = "fts_cjk_rebuild_cursor"


@dataclass(frozen=True)
class RebuildStatus:
    """Estado do backfill.

    ``high_water`` é a fronteira com o gatilho; ``cursor`` é até onde o
    backfill chegou. ``remaining`` conta o que falta entre os dois.
    """

    high_water: int
    cursor: int
    remaining: int
    total: int
    loaded: bool

    @property
    def complete(self) -> bool:
        return self.loaded and self.remaining == 0

    @property
    def progress(self) -> float:
        if self.total == 0:
            return 1.0
        return (self.total - self.remaining) / self.total


def _meta_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute("SELECT value FROM state_meta WHERE key=?", (key,)).fetchone()
    if row is None or row[0] is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def rebuild_status(conn: sqlite3.Connection) -> RebuildStatus:
    loaded = is_loaded(conn)
    high_water = _meta_int(conn, _schema.FTS_CJK_HIGH_WATER_KEY, -1)
    cursor = _meta_int(conn, _CURSOR_KEY, -1)
    total = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE role <> 'tool' AND id <= ?", (high_water,)
    ).fetchone()[0]
    remaining = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE role <> 'tool' AND id > ? AND id <= ?",
        (cursor, high_water),
    ).fetchone()[0]
    return RebuildStatus(
        high_water=high_water, cursor=cursor,
        remaining=remaining, total=total, loaded=loaded,
    )


def rebuild_step(conn: sqlite3.Connection, batch: int = 500) -> RebuildStatus:
    """Avança o backfill em um passo e **persiste** o cursor.

    Retomável por construção: o cursor é gravado na mesma transação das
    linhas indexadas. Uma interrupção no meio nunca perde nem duplica — ou o
    lote inteiro entrou e o cursor avançou, ou nada aconteceu.
    """
    if batch <= 0:
        raise ValueError("batch deve ser positivo")
    if not is_loaded(conn):
        return rebuild_status(conn)

    high_water = _meta_int(conn, _schema.FTS_CJK_HIGH_WATER_KEY, -1)
    cursor = _meta_int(conn, _CURSOR_KEY, -1)

    rows = conn.execute(
        "SELECT id, content, tool_name, tool_calls FROM messages "
        "WHERE role <> 'tool' AND id > ? AND id <= ? ORDER BY id LIMIT ?",
        (cursor, high_water, batch),
    ).fetchall()

    if rows:
        with conn:
            conn.executemany(
                "INSERT INTO messages_fts_cjk(rowid, content, tool_name, tool_calls) "
                "VALUES (?, ?, ?, ?)",
                [(r["id"], r["content"], r["tool_name"], r["tool_calls"]) for r in rows],
            )
            _set_meta(conn, _CURSOR_KEY, str(rows[-1]["id"]))

    status = rebuild_status(conn)
    with conn:
        _set_meta(conn, _PROGRESS_KEY, f"{status.progress:.4f}")
    return status
