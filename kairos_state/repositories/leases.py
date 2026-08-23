"""Lease de turno e lock de compactação.

RF-07, RF-08, RF-09. Reconstruído de `_reversa_sdd/hermes-state/` §3.

**O lease é uma TABELA, não uma coluna** — é a materialização durável do
ADR 004. A chave é a identidade de conversa resolvida (`sessions.id`), nunca
a chave de roteamento: foi confundir as duas que produziu o #64934.
"""

from __future__ import annotations

import sqlite3
import time

from kairos_state.contention import Budget
from kairos_state.writes import write_with_retry

__all__ = ["LeaseRepository", "LeaseNotHeld"]


class LeaseNotHeld(RuntimeError):
    """Operação de lease por quem não o detém."""


class LeaseRepository:
    """Duas tabelas de mesma forma, propósitos separados.

    O lease de turno e o lock de compactação são **distintos de propósito**
    (RF-09): uma compactação em curso não pode impedir o turno seguinte de
    adquirir o seu lease, senão comprimir travaria a conversa.
    """

    def __init__(self, conn: sqlite3.Connection, *, table: str = "session_turn_leases",
                 key_column: str = "conversation_id") -> None:
        if table not in ("session_turn_leases", "compression_locks"):
            raise ValueError(f"tabela de lease desconhecida: {table!r}")
        self._conn = conn
        self._table = table
        self._key = key_column if table == "session_turn_leases" else "session_id"

    @classmethod
    def turn_leases(cls, conn: sqlite3.Connection) -> "LeaseRepository":
        return cls(conn, table="session_turn_leases")

    @classmethod
    def compression_locks(cls, conn: sqlite3.Connection) -> "LeaseRepository":
        return cls(conn, table="compression_locks")

    def try_acquire(self, key: str, holder: str, *, ttl_seconds: float = 300.0,
                    now: float | None = None) -> bool:
        """Adquire se livre ou **expirado**. Não bloqueia.

        Um lease expirado é adquirível por qualquer holder novo, sem
        intervenção (RF-08): o processo dono pode ter morrido sem liberar, e
        exigir liberação limpa deixaria a conversa travada até alguém
        perceber.
        """
        moment = now if now is not None else time.time()

        def op():
            with self._conn:
                # Uma instrução só: a limpeza do expirado e a aquisição
                # precisam ser atômicas, senão dois processos limpam o mesmo
                # lease e ambos acham que o adquiriram.
                cur = self._conn.execute(
                    f"INSERT INTO {self._table}({self._key}, holder, acquired_at, expires_at) "
                    f"VALUES (:k, :h, :now, :exp) "
                    f"ON CONFLICT({self._key}) DO UPDATE SET "
                    f"  holder = excluded.holder, "
                    f"  acquired_at = excluded.acquired_at, "
                    f"  expires_at = excluded.expires_at "
                    f"WHERE {self._table}.expires_at <= :now "
                    f"   OR {self._table}.holder = excluded.holder",
                    {"k": key, "h": holder, "now": moment, "exp": moment + ttl_seconds},
                )
            return cur.rowcount > 0

        return write_with_retry(op, budget=Budget.ROUTINE, detail=f"acquire:{self._table}")

    def holder(self, key: str, *, now: float | None = None) -> str | None:
        """Quem detém o lease **não expirado**, ou `None`."""
        moment = now if now is not None else time.time()
        row = self._conn.execute(
            f"SELECT holder, expires_at FROM {self._table} WHERE {self._key} = ?", (key,)
        ).fetchone()
        if row is None or row["expires_at"] <= moment:
            return None
        return row["holder"]

    def refresh(self, key: str, holder: str, *, ttl_seconds: float = 300.0,
                now: float | None = None) -> bool:
        """Estende o prazo. Só o holder atual consegue."""
        moment = now if now is not None else time.time()

        def op():
            with self._conn:
                cur = self._conn.execute(
                    f"UPDATE {self._table} SET expires_at = ? "
                    f"WHERE {self._key} = ? AND holder = ? AND expires_at > ?",
                    (moment + ttl_seconds, key, holder, moment),
                )
            return cur.rowcount > 0

        return write_with_retry(op, budget=Budget.ROUTINE, detail=f"refresh:{self._table}")

    def release(self, key: str, holder: str) -> bool:
        """Libera. Só o holder atual — senão um processo atrasado poderia
        liberar o lease que outro já adquiriu."""
        def op():
            with self._conn:
                cur = self._conn.execute(
                    f"DELETE FROM {self._table} WHERE {self._key} = ? AND holder = ?",
                    (key, holder),
                )
            return cur.rowcount > 0

        return write_with_retry(op, budget=Budget.ROUTINE, detail=f"release:{self._table}")
