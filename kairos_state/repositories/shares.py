"""Compartilhamentos de conversa por link (tabela ``session_shares``).

Somente o hash do token vive no banco — o token em texto claro aparece uma
única vez, na resposta de criação. A revogação é marca suave (``revoked_at``)
para manter o histórico de quem já teve acesso e quando.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from typing import Any


def token_digest(token: str) -> str:
    """Hash do token em texto claro. Comparação por digest, nunca por valor."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """Token de leitura com entropia suficiente para ser segredo."""
    return secrets.token_urlsafe(32)


def create_share(
    conn: sqlite3.Connection,
    session_id: str,
    token: str,
    *,
    expires_at: float | None = None,
    created_at: float | None = None,
) -> int:
    """Registra um compartilhamento e devolve o id da linha.

    ``token`` é a forma em texto claro (retornada ao criador), guardada aqui
    apenas como digest.
    """
    atual = time.time() if created_at is None else created_at
    conn.execute(
        "INSERT INTO session_shares(session_id, token_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, token_digest(token), atual, expires_at),
    )
    rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return int(rowid)


def get_share(conn: sqlite3.Connection, share_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM session_shares WHERE id = ?", (share_id,)).fetchone()


def find_active_share(
    conn: sqlite3.Connection,
    token: str,
    *,
    now: float | None = None,
) -> sqlite3.Row | None:
    """Devolve o compartilhamento ativo (não revogado, não expirado) do token."""
    atual = time.time() if now is None else now
    return conn.execute(
        "SELECT * FROM session_shares "
        "WHERE token_hash = ? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (token_digest(token), atual),
    ).fetchone()


def list_shares(conn: sqlite3.Connection, session_id: str) -> list[Any]:
    return conn.execute(
        "SELECT * FROM session_shares WHERE session_id = ? ORDER BY created_at DESC, id DESC",
        (session_id,),
    ).fetchall()


def revoke_share(
    conn: sqlite3.Connection,
    share_id: int,
    *,
    revoked_at: float | None = None,
) -> bool:
    """Revoga o compartilhamento. Devolve True se alguma linha foi tocada."""
    atual = time.time() if revoked_at is None else revoked_at
    cursor = conn.execute(
        "UPDATE session_shares SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (atual, share_id),
    )
    return cursor.rowcount > 0
