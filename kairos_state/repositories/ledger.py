"""Ledger durável de obrigações de entrega.

RF do `_reversa_sdd/providers-gateway/design.md` §B: a obrigação é gravada
em `state.db` **antes** do envio, e reentregue no boot seguinte se o processo
morrer no meio (`_redeliver_pending_obligations`).

O `DeliveryLedger` de `kairos_gateway` guarda o mesmo modelo em memória e é
onde vive a regra (só o adapter confirma; prova de morte é `pid` +
`started_at`). Este repositório é a metade durável: sem ele, a obrigação
existe só enquanto o processo vive — que é exatamente o modo de falha que o
ledger foi criado para cobrir.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from kairos_gateway.delivery import DeliveryState, Obligation
from kairos_state.contention import Budget
from kairos_state.writes import write_with_retry

__all__ = ["LedgerRepository"]


def _row_to_obligation(row: sqlite3.Row) -> Obligation:
    payload = row["payload_json"]
    return Obligation(
        obligation_id=row["obligation_id"],
        target=row["target"],
        payload=payload if payload is not None else "",
        state=DeliveryState(row["state"]),
        attempts=row["attempts"],
        owner_pid=row["owner_pid"],
        owner_started_at=row["owner_started_at"],
        created_at=row["created_at"],
        delivered_at=row["delivered_at"],
    )


class LedgerRepository:
    """Persiste `delivery_obligations`.

    Espelha a API do `DeliveryLedger` em memória de propósito: o gateway
    chama os dois com a mesma sequência, e divergir os nomes convidaria a
    gravar uma transição no lugar errado.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def record(
        self,
        obligation_id: str,
        target: str,
        payload: Any,
        *,
        session_id: str | None = None,
        now: float | None = None,
    ) -> Obligation:
        """Grava como `pending`. Chamado **antes** da tentativa de envio."""
        moment = now if now is not None else time.time()
        texto = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

        def _op() -> None:
            self._conn.execute(
                "INSERT OR REPLACE INTO delivery_obligations "
                "(obligation_id, session_id, target, payload_json, state, attempts, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
                (obligation_id, session_id, target, texto, moment, moment),
            )
            self._conn.commit()

        write_with_retry(_op, budget=Budget.ROUTINE, detail="ledger.record")
        return Obligation(
            obligation_id=obligation_id, target=target, payload=texto, created_at=moment
        )

    def claim(self, obligation_id: str, *, pid: int, started_at: int) -> bool:
        """Toma posse. O `WHERE state='pending'` é o que torna a corrida segura:
        dois gateways competindo, só um vê `rowcount == 1`."""

        def _op() -> int:
            cur = self._conn.execute(
                "UPDATE delivery_obligations "
                "SET state='claimed', owner_pid=?, owner_started_at=?, "
                "    attempts=attempts+1, updated_at=? "
                "WHERE obligation_id=? AND state='pending'",
                (pid, started_at, time.time(), obligation_id),
            )
            self._conn.commit()
            return cur.rowcount

        return write_with_retry(_op, budget=Budget.ROUTINE, detail="ledger.claim") == 1

    def confirm(self, obligation_id: str, *, now: float | None = None) -> bool:
        """Quita. Só sai de `claimed`: confirmar o que não foi reivindicado
        seria dar por entregue algo que ninguém enviou."""
        moment = now if now is not None else time.time()

        def _op() -> int:
            cur = self._conn.execute(
                "UPDATE delivery_obligations "
                "SET state='delivered', delivered_at=?, updated_at=? "
                "WHERE obligation_id=? AND state='claimed'",
                (moment, moment, obligation_id),
            )
            self._conn.commit()
            return cur.rowcount

        return write_with_retry(_op, budget=Budget.ROUTINE, detail="ledger.confirm") == 1

    def release(self, obligation_id: str) -> bool:
        """Devolve à fila após falha transitória."""

        def _op() -> int:
            cur = self._conn.execute(
                "UPDATE delivery_obligations "
                "SET state='pending', owner_pid=NULL, owner_started_at=NULL, updated_at=? "
                "WHERE obligation_id=? AND state='claimed'",
                (time.time(), obligation_id),
            )
            self._conn.commit()
            return cur.rowcount

        return write_with_retry(_op, budget=Budget.ROUTINE, detail="ledger.release") == 1

    def abandon(self, obligation_id: str) -> bool:
        """Alvo permanentemente morto: insistir nunca converge."""

        def _op() -> int:
            cur = self._conn.execute(
                "UPDATE delivery_obligations SET state='abandoned', updated_at=? "
                "WHERE obligation_id=? AND state IN ('pending','claimed')",
                (time.time(), obligation_id),
            )
            self._conn.commit()
            return cur.rowcount

        return write_with_retry(_op, budget=Budget.ROUTINE, detail="ledger.abandon") == 1

    def pending(self, limit: int = 100) -> list[Obligation]:
        """Mais antigas primeiro: a que já esperou mais é a que mais arrisca
        virar entrega tardia sem valor."""
        rows = self._conn.execute(
            "SELECT * FROM delivery_obligations WHERE state='pending' "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_obligation(r) for r in rows]

    def reclaim_dead(self, live_pids: set[int]) -> list[str]:
        """Devolve à fila o que ficou preso em `claimed` sob dono morto.

        É este método que implementa a reentrega no boot: um gateway que
        morreu sujo deixa obrigações `claimed` que nenhum processo vivo está
        cumprindo, e sem isto elas nunca mais seriam tentadas.
        """
        rows = self._conn.execute(
            "SELECT obligation_id, owner_pid FROM delivery_obligations WHERE state='claimed'"
        ).fetchall()
        orfas = [r["obligation_id"] for r in rows if r["owner_pid"] not in live_pids]
        if not orfas:
            return []

        def _op() -> None:
            self._conn.executemany(
                "UPDATE delivery_obligations "
                "SET state='pending', owner_pid=NULL, owner_started_at=NULL, updated_at=? "
                "WHERE obligation_id=?",
                [(time.time(), oid) for oid in orfas],
            )
            self._conn.commit()

        write_with_retry(_op, budget=Budget.ROUTINE, detail="ledger.reclaim_dead")
        return orfas

    def counts_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM delivery_obligations GROUP BY state"
        ).fetchall()
        counts = {s.value: 0 for s in DeliveryState}
        for r in rows:
            counts[r["state"]] = r["n"]
        return counts
