"""Mensagens, sidecar e compactação não-destrutiva.

RF-05, RF-11. Reconstruído de `_reversa_sdd/hermes-state/` §2 e §4.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from kairos_providers.contracts import ResolvedModelSelection
from kairos_state.contention import Budget
from kairos_state.writes import write_with_retry

__all__ = ["MessageRepository"]


class MessageRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(
        self,
        session_id: str,
        role: str,
        *,
        content: str | None = None,
        api_content: str | None = None,
        timestamp: float | None = None,
        **columns,
    ) -> int:
        """Grava uma mensagem. **Orçamento de transcript** (RF-05).

        `content` é o que se exibe; `api_content` é o que vai ao provedor.
        Manter os dois separados é o que permite reescrever a exibição —
        redigir um segredo, encurtar um dump — sem mexer no prefixo que o
        provedor tem em cache. É a Lei 1 materializada em duas colunas.
        """
        fields = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "api_content": api_content,
            "timestamp": timestamp if timestamp is not None else time.time(),
            **columns,
        }
        names = ", ".join(fields)
        holes = ", ".join(f":{k}" for k in fields)

        def op():
            with self._conn:
                cur = self._conn.execute(f"INSERT INTO messages({names}) VALUES ({holes})", fields)  # noqa: S608 — nomes de coluna vêm das chaves do dict construído aqui, não de entrada externa
            return int(cur.lastrowid)

        # Perder o transcript é pior que atrasar: aguenta a paciência longa.
        return write_with_retry(op, budget=Budget.TRANSCRIPT, detail="append_message")

    def append_turn_message(
        self,
        session_id: str,
        role: str,
        content: str,
        selection: ResolvedModelSelection,
        *,
        api_content: str | None = None,
        timestamp: float | None = None,
        **columns: Any,
    ) -> int:
        metadata = json.dumps(
            {
                "model": selection.ref.model,
                "provider": selection.ref.provider,
                "reason": selection.reason.value,
            },
            sort_keys=True,
        )
        return self.append(
            session_id,
            role,
            content=content,
            api_content=api_content,
            timestamp=timestamp,
            display_metadata=metadata,
            **columns,
        )

    def for_api(self, session_id: str) -> list[sqlite3.Row]:
        """O que vai ao modelo: só as ativas, em ordem."""
        return self._conn.execute(
            "SELECT id, role, COALESCE(api_content, content) AS payload "
            "FROM messages WHERE session_id = ? AND active = 1 "
            "ORDER BY timestamp, id",
            (session_id,),
        ).fetchall()

    def active_watermark(self, session_id: str) -> int:
        """Maior id ativo no momento.

        A compactação sumariza **até** esta marca. Mensagens que chegam
        durante a sumarização — que é lenta, é uma chamada de LLM — ficam
        acima dela e não são sumarizadas, em vez de desaparecerem num
        resumo que começou antes delas existirem.
        """
        row = self._conn.execute(
            "SELECT COALESCE(MAX(id), -1) FROM messages WHERE session_id = ? AND active = 1",
            (session_id,),
        ).fetchone()
        return int(row[0])

    def archive_and_compact(
        self, session_id: str, watermark: int, *, lock_holder: str | None = None
    ) -> int:
        """Arquiva o que está até a marca. **Não apaga nada** (RF-11).

        As mensagens continuam na tabela e mudam de visibilidade:
        `active=0, compacted=1` — fora do contexto do modelo, dentro da
        busca. Um `DELETE` tornaria o histórico irrecuperável e quebraria a
        regra de que a conversa mantém um id para a vida.

        `lock_holder` é verificado **dentro** da transação de commit: entre
        adquirir o lock e chegar aqui passou uma chamada de LLM inteira, e o
        lease pode ter expirado e sido tomado nesse intervalo. Verificar
        fora seria uma janela de corrida do tamanho da sumarização.
        """

        def op():
            with self._conn:
                if lock_holder is not None:
                    row = self._conn.execute(
                        "SELECT holder FROM compression_locks WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                    if row is None or row["holder"] != lock_holder:
                        raise CompressionLockLost(
                            f"o lock de compactação de {session_id!r} não é mais de "
                            f"{lock_holder!r}; abortando para não sobrescrever "
                            "trabalho de outro escritor"
                        )
                cur = self._conn.execute(
                    "UPDATE messages SET active = 0, compacted = 1 "
                    "WHERE session_id = ? AND id <= ? AND active = 1",
                    (session_id, watermark),
                )
            return cur.rowcount

        return write_with_retry(op, budget=Budget.TRANSCRIPT, detail="archive_and_compact")

    def rewind(self, session_id: str, after_id: int) -> int:
        """Rebobina: `active=0, compacted=0` — some da busca também."""

        def op():
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE messages SET active = 0, compacted = 0 WHERE session_id = ? AND id > ?",
                    (session_id, after_id),
                )
            return cur.rowcount

        return write_with_retry(op, budget=Budget.ROUTINE, detail="rewind")


class CompressionLockLost(RuntimeError):
    """O holder do lock mudou entre a aquisição e o commit."""
