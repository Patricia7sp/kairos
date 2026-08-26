"""Contabilidade de tokens, coalescida em gravação de fundo.

RF-14, RF-15, RF-16. Reconstruído de `_reversa_sdd/hermes-state/` §6.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass

from kairos_providers.base import TokenUsage
from kairos_providers.contracts import ResolvedModelSelection
from kairos_state.contention import Budget
from kairos_state.writes import write_with_retry

__all__ = ["BillingRoute", "TokenDelta", "UsageRepository"]

_COUNTERS = (
    "api_call_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


@dataclass(frozen=True)
class BillingRoute:
    """A chave de rateio: **rota de billing**, não só sessão (RF-16).

    Uma sessão que troca de provedor no meio produz linhas distintas. Sem
    isso, o custo de uma conversa que migrou de um provedor caro para um
    barato viraria uma média que não corresponde a nenhuma fatura.
    """

    session_id: str
    model: str
    billing_provider: str
    billing_base_url: str
    billing_mode: str
    task: str = "chat"

    def as_key(self) -> tuple:
        return (
            self.session_id,
            self.model,
            self.billing_provider,
            self.billing_base_url,
            self.billing_mode,
            self.task,
        )


@dataclass
class TokenDelta:
    api_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    def merge(self, other: TokenDelta) -> None:
        for name in _COUNTERS:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def is_empty(self) -> bool:
        return all(getattr(self, n) == 0 for n in _COUNTERS)


class UsageRepository:
    """Fila coalescida em memória, drenada em lote.

    **Por que coalescer.** Streaming produz dezenas de deltas por turno. Um
    `UPDATE` por delta transformaria a contabilidade — que é acessória — na
    principal fonte de contenção de escrita no `state.db` compartilhado, no
    exato momento em que o turno precisa gravar o transcript.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._pending: dict[tuple, TokenDelta] = defaultdict(TokenDelta)
        self._routes: dict[tuple, BillingRoute] = {}

    def queue(self, route: BillingRoute, delta: TokenDelta) -> None:
        """Enfileira. **Não toca o banco** — é caminho de turno."""
        if delta.is_empty():
            return
        key = route.as_key()
        with self._lock:
            self._pending[key].merge(delta)
            self._routes[key] = route

    def record_event(
        self,
        session_id: str,
        selection: ResolvedModelSelection,
        *,
        billing_provider: str,
        billing_base_url: str,
        billing_mode: str,
        usage: TokenUsage | None = None,
        api_call_count: int = 0,
        cache_write_tokens: int = 0,
        task: str = "chat",
    ) -> None:
        delta = TokenDelta(
            api_call_count=api_call_count,
            input_tokens=usage.input_tokens if usage is not None else 0,
            output_tokens=usage.output_tokens if usage is not None else 0,
            cache_read_tokens=usage.cache_read_tokens if usage is not None else 0,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens if usage is not None else 0,
        )
        self.queue(
            BillingRoute(
                session_id=session_id,
                model=selection.ref.model,
                billing_provider=billing_provider,
                billing_base_url=billing_base_url,
                billing_mode=billing_mode,
                task=task,
            ),
            delta,
        )

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def flush(self, *, now: float | None = None) -> int:
        """Grava o acumulado em lote. Devolve quantas rotas foram gravadas."""
        import time as _time

        moment = now if now is not None else _time.time()

        with self._lock:
            batch = list(self._pending.items())
            routes = dict(self._routes)
            self._pending.clear()
            self._routes.clear()

        if not batch:
            return 0

        increments = ", ".join(f"{n} = {n} + excluded.{n}" for n in _COUNTERS)
        columns = ", ".join(_COUNTERS)
        holes = ", ".join("?" for _ in _COUNTERS)

        def op():
            with self._conn:
                for key, delta in batch:
                    route = routes[key]
                    self._conn.execute(
                        f"INSERT INTO session_model_usage("  # noqa: S608 — nomes de coluna vêm de _COUNTERS, constante do módulo
                        f"  session_id, model, billing_provider, billing_base_url, "
                        f"  billing_mode, task, {columns}, first_seen, last_seen) "
                        f"VALUES (?, ?, ?, ?, ?, ?, {holes}, ?, ?) "
                        f"ON CONFLICT(session_id, model, billing_provider, "
                        f"            billing_base_url, billing_mode, task) DO UPDATE SET "
                        f"  {increments}, last_seen = excluded.last_seen",
                        (*route.as_key(), *[getattr(delta, n) for n in _COUNTERS], moment, moment),
                    )
            return len(batch)

        try:
            return write_with_retry(op, budget=Budget.ROUTINE, detail="flush_token_usage")
        except Exception:
            # DELIBERADO: qualquer falha devolve o lote à fila e RE-LEVANTA.
            # Estreitar aqui faria uma exceção não prevista descartar o lote
            # em silêncio — contabilidade perdida é irrecuperável,
            # e uma falha transitória não deve custar os números do turno.
            with self._lock:
                for key, delta in batch:
                    self._pending[key].merge(delta)
                    self._routes[key] = routes[key]
            raise

    def drain_at_exit(self) -> int:
        """Dreno forçado no encerramento (RF-15).

        Sem isto, tudo que ficou na fila entre o último flush e o exit é
        perdido — e o usuário vê uma sessão cujo custo não bate com a fatura.
        """
        try:
            return self.flush()
        except Exception:  # noqa: BLE001
            # DELIBERADO: no encerramento não há para quem propagar. Engolir
            # aqui é a escolha certa — levantar num atexit vira traceback
            # confuso que esconde o motivo real do shutdown.
            return 0
