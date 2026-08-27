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
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    cost_status: str | None = None
    cost_source: str | None = None

    def merge(self, other: TokenDelta) -> None:
        first = self.cost_status is None and self.is_empty()
        for name in _COUNTERS:
            setattr(self, name, getattr(self, name) + getattr(other, name))
        if first:
            self.estimated_cost_usd = other.estimated_cost_usd
            self.actual_cost_usd = other.actual_cost_usd
            self.cost_status = other.cost_status
            self.cost_source = other.cost_source
            return
        (
            self.estimated_cost_usd,
            self.actual_cost_usd,
            self.cost_status,
            self.cost_source,
        ) = _merge_cost_fields(
            self.estimated_cost_usd,
            self.actual_cost_usd,
            self.cost_status,
            self.cost_source,
            incoming_estimated=other.estimated_cost_usd,
            incoming_actual=other.actual_cost_usd,
            incoming_status=other.cost_status,
            incoming_source=other.cost_source,
        )

    def is_empty(self) -> bool:
        return (
            all(getattr(self, n) == 0 for n in _COUNTERS)
            and self.estimated_cost_usd is None
            and self.actual_cost_usd is None
            and self.cost_status is None
        )


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
        estimated_cost_usd: float | None = None,
        actual_cost_usd: float | None = None,
        cost_status: str = "unknown",
        cost_source: str | None = None,
    ) -> None:
        delta = TokenDelta(
            api_call_count=api_call_count,
            input_tokens=usage.input_tokens if usage is not None else 0,
            output_tokens=usage.output_tokens if usage is not None else 0,
            cache_read_tokens=usage.cache_read_tokens if usage is not None else 0,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens if usage is not None else 0,
            estimated_cost_usd=estimated_cost_usd,
            actual_cost_usd=actual_cost_usd,
            cost_status=cost_status,
            cost_source=cost_source,
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
                    current = self._conn.execute(
                        "SELECT estimated_cost_usd, actual_cost_usd, cost_status, cost_source "
                        "FROM session_model_usage WHERE session_id = ? AND model = ? "
                        "AND billing_provider = ? AND billing_base_url = ? "
                        "AND billing_mode = ? AND task = ?",
                        route.as_key(),
                    ).fetchone()
                    route_cost = _merge_row_cost(current, delta)
                    self._conn.execute(
                        f"INSERT INTO session_model_usage("  # noqa: S608 — nomes de coluna vêm de _COUNTERS, constante do módulo
                        f"  session_id, model, billing_provider, billing_base_url, "
                        f"  billing_mode, task, {columns}, estimated_cost_usd, "
                        f"  actual_cost_usd, cost_status, cost_source, first_seen, last_seen) "
                        f"VALUES (?, ?, ?, ?, ?, ?, {holes}, ?, ?, ?, ?, ?, ?) "
                        f"ON CONFLICT(session_id, model, billing_provider, "
                        f"            billing_base_url, billing_mode, task) DO UPDATE SET "
                        f"  {increments}, estimated_cost_usd = excluded.estimated_cost_usd, "
                        f"  actual_cost_usd = excluded.actual_cost_usd, "
                        f"  cost_status = excluded.cost_status, cost_source = excluded.cost_source, "
                        f"  last_seen = excluded.last_seen",
                        (
                            *route.as_key(),
                            *[getattr(delta, n) for n in _COUNTERS],
                            *route_cost,
                            moment,
                            moment,
                        ),
                    )
                    session_row = self._conn.execute(
                        "SELECT billing_provider, billing_base_url, billing_mode, "
                        "estimated_cost_usd, actual_cost_usd, cost_status, cost_source "
                        "FROM sessions WHERE id = ?",
                        (route.session_id,),
                    ).fetchone()
                    session_cost = _merge_row_cost(session_row, delta)
                    session_route = _session_route_identity(session_row, route)
                    assignments = ", ".join(f"{name} = {name} + ?" for name in _COUNTERS)
                    self._conn.execute(
                        f"UPDATE sessions SET {assignments}, billing_provider = ?, "  # noqa: S608 - colunas constantes
                        "billing_base_url = ?, billing_mode = ?, estimated_cost_usd = ?, "
                        "actual_cost_usd = ?, cost_status = ?, cost_source = ? WHERE id = ?",
                        (
                            *[getattr(delta, name) for name in _COUNTERS],
                            *session_route,
                            *session_cost,
                            route.session_id,
                        ),
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


def _merge_row_cost(row: sqlite3.Row | None, delta: TokenDelta) -> tuple:
    if row is None or row["cost_status"] is None:
        return (
            delta.estimated_cost_usd,
            delta.actual_cost_usd,
            delta.cost_status or "unknown",
            delta.cost_source,
        )
    return _merge_cost_fields(
        row["estimated_cost_usd"],
        row["actual_cost_usd"],
        row["cost_status"],
        row["cost_source"],
        incoming_estimated=delta.estimated_cost_usd,
        incoming_actual=delta.actual_cost_usd,
        incoming_status=delta.cost_status,
        incoming_source=delta.cost_source,
    )


def _merge_cost_fields(
    current_estimated: float | None,
    current_actual: float | None,
    current_status: str | None,
    current_source: str | None,
    *,
    incoming_estimated: float | None,
    incoming_actual: float | None,
    incoming_status: str | None,
    incoming_source: str | None,
) -> tuple[float | None, float | None, str, str | None]:
    estimated = _sum_optional(current_estimated, incoming_estimated)
    actual = _sum_optional(current_actual, incoming_actual)
    statuses = (current_status, incoming_status)
    if "unknown" in statuses:
        status = "unknown"
    elif any(status == "estimated" and estimated is not None for status in statuses):
        status = "estimated"
    elif any(status == "actual" and actual is not None for status in statuses):
        status = "actual"
    else:
        status = "unknown"
    source = current_source if current_source == incoming_source else None
    return estimated, actual, status, source


def _sum_optional(current: float | None, incoming: float | None) -> float | None:
    if current is None:
        return incoming
    if incoming is None:
        return current
    return current + incoming


def _session_route_identity(
    row: sqlite3.Row | None, incoming: BillingRoute
) -> tuple[str, str, str]:
    if row is None:
        return (
            incoming.billing_provider,
            incoming.billing_base_url,
            incoming.billing_mode,
        )
    current = (row["billing_provider"], row["billing_base_url"], row["billing_mode"])
    new = (incoming.billing_provider, incoming.billing_base_url, incoming.billing_mode)
    if current[0] == "mixed" or (current[0] is not None and current != new):
        return ("mixed", "", "mixed")
    return new
