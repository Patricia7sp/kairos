"""Busca textual em quatro rotas, com degradação.

RF-12, RF-13. Reconstruído de `_reversa_sdd/hermes-state/` §5.

**A cadeia é FTS5 base → trigram → bigrama CJK → `LIKE`.** A spec corrige
explicitamente uma versão anterior que dizia "duas rotas": são três índices
mais o fallback. E cada degrau é opcional de forma independente — a ausência
do trigram não afeta o CJK, e a ausência dos dois não afeta o índice base.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

__all__ = ["Route", "SearchCapabilities", "SearchIndex", "probe"]

#: Teto de tamanho de consulta FTS5. Query gigante não é uso legítimo e o
#: parser do FTS5 degrada mal.
MAX_FTS5_QUERY_CHARS = 2_048


class Route(str, Enum):
    FTS5 = "fts5"
    TRIGRAM = "trigram"
    CJK = "cjk"
    LIKE = "like"


@dataclass(frozen=True)
class SearchCapabilities:
    """O que este banco consegue fazer agora.

    Sondado em runtime, não assumido: o mesmo código roda contra SQLite com e
    sem FTS5, e contra host com e sem a extensão CJK compilada.
    """

    fts5: bool
    trigram: bool
    cjk: bool

    @property
    def reason(self) -> str | None:
        return None if self.fts5 else "fts5_unavailable"

    def routes(self) -> tuple[Route, ...]:
        out: list[Route] = []
        if self.fts5:
            out.append(Route.FTS5)
        if self.trigram:
            out.append(Route.TRIGRAM)
        if self.cjk:
            out.append(Route.CJK)
        out.append(Route.LIKE)   # sempre disponível, sempre por último
        return tuple(out)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone() is not None


def probe(conn: sqlite3.Connection) -> SearchCapabilities:
    """Descobre as rotas disponíveis. **Nunca levanta.**

    *"The trigram tokenizer being unavailable is not fatal."* Nem o FTS5
    inteiro: sem ele a sondagem devolve `fts5_unavailable` e o `LIKE` assume.
    """
    return SearchCapabilities(
        fts5=_table_exists(conn, "messages_fts"),
        trigram=_table_exists(conn, "messages_fts_trigram"),
        cjk=_table_exists(conn, "messages_fts_cjk"),
    )


class SearchIndex:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.capabilities = probe(conn)

    def search(self, term: str, *, limit: int = 50) -> list[sqlite3.Row]:
        """Tenta as rotas em ordem; a primeira que responde vence.

        Não é união das rotas: são estratégias alternativas para o mesmo
        termo, e cada uma que falha por indisponibilidade cede à seguinte.
        """
        if not term or not term.strip():
            return []

        for route in self.capabilities.routes():
            try:
                rows = self._run(route, term, limit)
            except sqlite3.OperationalError:
                # Índice presente mas inutilizável (corrompido, tokenizador
                # sumiu entre a sondagem e a consulta). Degrada em vez de
                # propagar: busca pior é melhor que busca quebrada.
                continue
            if rows:
                return rows
        return []

    def _run(self, route: Route, term: str, limit: int) -> list[sqlite3.Row]:
        if route is Route.LIKE:
            # Fallback final: varredura. Lento, mas sempre correto — e é o
            # único caminho que existe quando o SQLite não tem FTS5.
            return self._conn.execute(
                "SELECT m.id, m.session_id, m.content FROM messages m "
                "WHERE m.content LIKE ? ESCAPE '\\' AND m.active = 1 "
                "ORDER BY m.timestamp DESC LIMIT ?",
                (f"%{_escape_like(term)}%", limit),
            ).fetchall()

        table = {
            Route.FTS5: "messages_fts",
            Route.TRIGRAM: "messages_fts_trigram",
            Route.CJK: "messages_fts_cjk",
        }[route]
        query = term[:MAX_FTS5_QUERY_CHARS]
        return self._conn.execute(
            f"SELECT m.id, m.session_id, m.content FROM {table} f "
            f"JOIN messages m ON m.id = f.rowid "
            f"WHERE f MATCH ? AND m.active = 1 "
            f"ORDER BY m.timestamp DESC LIMIT ?",
            (query, limit),
        ).fetchall()


def _escape_like(term: str) -> str:
    """`%` e `_` do usuário são literais, não curingas."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
