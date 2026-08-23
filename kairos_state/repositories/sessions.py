"""Sessões e a linhagem de compactação.

RF-06, RF-10, RF-11. Reconstruído de `_reversa_sdd/hermes-state/` §4.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time

from kairos_state.contention import Budget
from kairos_state.writes import write_with_retry

__all__ = ["SessionRepository", "COMPRESSION_LINEAGE_SQL"]


# ---------------------------------------------------------------------------
# A regra mais valiosa da unit
# ---------------------------------------------------------------------------
#
# A linhagem de compactação NÃO é uma tabela — é uma CTE recursiva sobre
# ``sessions.parent_session_id``. E a recursão só sobe enquanto QUATRO
# condições valem simultaneamente:
#
#   1. o pai encerrou por ``end_reason = 'compression'``
#   2. o filho NÃO tem ``model_config -> '$._branched_from'``   (não é branch)
#   3. o filho NÃO tem ``model_config -> '$._delegate_from'``   (não é delegação)
#   4. o filho NÃO tem ``source = 'tool'``                      (não é sessão de ferramenta)
#
# Os três filtros negativos são o que impede o transcript reconstruído de
# arrastar histórico alheio. Sem eles, um branch traria a conversa de onde
# ramificou, e uma delegação traria a do pai que a despachou — em ambos os
# casos o usuário veria, no seu próprio transcript, falas que nunca fez.
#
# A spec registra que esta regra estava AUSENTE da versão anterior dela, e a
# chama de "a regra de negócio mais valiosa da unit".

COMPRESSION_LINEAGE_SQL = """
WITH RECURSIVE lineage(id, parent_session_id, depth) AS (
    SELECT id, parent_session_id, 0
      FROM sessions
     WHERE id = :session_id

    UNION ALL

    SELECT p.id, p.parent_session_id, l.depth + 1
      FROM sessions p
      JOIN lineage l ON p.id = l.parent_session_id
      JOIN sessions c ON c.id = l.id
     WHERE p.end_reason = 'compression'
       AND json_extract(c.model_config, '$._branched_from') IS NULL
       AND json_extract(c.model_config, '$._delegate_from') IS NULL
       AND COALESCE(c.source, '') <> 'tool'
       AND l.depth < :max_depth
)
SELECT id, depth FROM lineage ORDER BY depth DESC
"""

#: Teto de recursão. Uma linhagem legítima tem dezenas de degraus; milhares
#: indicam ciclo por corrupção, e a CTE giraria até estourar memória.
MAX_LINEAGE_DEPTH = 1_000


class SessionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- prompts ------------------------------------------------------------

    @staticmethod
    def prompt_hash(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def intern_system_prompt(self, prompt: str) -> str:
        """Deduplica por hash e devolve a chave (RF-06).

        Prompts de sistema são grandes e quase sempre idênticos entre sessões;
        armazená-los inline multiplicaria megabytes por conversa.
        """
        digest = self.prompt_hash(prompt)

        def op():
            with self._conn:
                self._conn.execute(
                    "INSERT INTO system_prompts(hash, prompt) VALUES (?, ?) "
                    "ON CONFLICT(hash) DO NOTHING",
                    (digest, prompt),
                )
            return digest

        return write_with_retry(op, budget=Budget.TRANSCRIPT, detail="intern_system_prompt")

    # -- ciclo de vida ------------------------------------------------------

    def create(self, session_id: str, source: str, *, started_at: float | None = None,
               system_prompt: str | None = None, parent_session_id: str | None = None,
               **columns) -> str:
        prompt_hash = self.intern_system_prompt(system_prompt) if system_prompt else None
        fields = {
            "id": session_id,
            "source": source,
            "started_at": started_at if started_at is not None else time.time(),
            "system_prompt_hash": prompt_hash,
            "parent_session_id": parent_session_id,
            **columns,
        }
        names = ", ".join(fields)
        holes = ", ".join(f":{k}" for k in fields)

        def op():
            with self._conn:
                self._conn.execute(f"INSERT INTO sessions({names}) VALUES ({holes})", fields)
            return session_id

        # Criação de sessão é caminho de transcript: a falha aborta o turno.
        return write_with_retry(op, budget=Budget.TRANSCRIPT, detail="create_session")

    def get(self, session_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()

    def end(self, session_id: str, end_reason: str, *, ended_at: float | None = None) -> None:
        def op():
            with self._conn:
                self._conn.execute(
                    "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                    (ended_at if ended_at is not None else time.time(), end_reason, session_id),
                )

        write_with_retry(op, budget=Budget.ROUTINE, detail="end_session")

    # -- linhagem -----------------------------------------------------------

    def compression_lineage(self, session_id: str) -> list[str]:
        """Ancestrais alcançados **só** por compactação, da raiz até a sessão.

        Ver `COMPRESSION_LINEAGE_SQL` para os quatro predicados e o porquê
        dos três filtros negativos.
        """
        rows = self._conn.execute(
            COMPRESSION_LINEAGE_SQL,
            {"session_id": session_id, "max_depth": MAX_LINEAGE_DEPTH},
        ).fetchall()
        return [r["id"] for r in rows]
