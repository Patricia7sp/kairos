"""Ledger de obrigação de entrega e circuit breaker de destinos mortos.

`_reversa_sdd/providers-gateway/` §4 (Tarefa 11).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "COOLDOWN_LADDER",
    "DeadTargets",
    "DeliveryLedger",
    "DeliveryState",
    "FailureKind",
    "Obligation",
]


class DeliveryState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"
    ABANDONED = "abandoned"


class FailureKind(StrEnum):
    #: Rede, 5xx, timeout — vale tentar de novo.
    TRANSIENT = "transient"
    #: Usuário bloqueou o bot, canal deletado — tentar de novo é desperdício
    #: que nunca converge.
    PERMANENT = "permanent"


@dataclass
class Obligation:
    obligation_id: str
    target: str
    payload: str
    state: DeliveryState = DeliveryState.PENDING
    attempts: int = 0
    owner_pid: int | None = None
    owner_started_at: int | None = None
    created_at: float = field(default_factory=time.time)
    delivered_at: float | None = None


class DeliveryLedger:
    """Mensagem crítica gerada em background só é dada como entregue **depois
    da confirmação do adapter**.

    O modo de falha que isto cobre: um cron job produz uma resposta, o
    processo morre antes do envio, e ninguém percebe — porque não havia
    registro de que a entrega era devida.
    """

    def __init__(self) -> None:
        self._obligations: dict[str, Obligation] = {}

    def record(self, obligation_id: str, target: str, payload: str) -> Obligation:
        ob = Obligation(obligation_id=obligation_id, target=target, payload=payload)
        self._obligations[obligation_id] = ob
        return ob

    def claim(self, obligation_id: str, *, pid: int, started_at: int) -> bool:
        ob = self._obligations.get(obligation_id)
        if ob is None or ob.state is not DeliveryState.PENDING:
            return False
        ob.state = DeliveryState.CLAIMED
        ob.owner_pid, ob.owner_started_at = pid, started_at
        ob.attempts += 1
        return True

    def confirm(self, obligation_id: str, *, now: float | None = None) -> bool:
        """**Só o adapter confirma.** Marcar antes seria fingir a entrega."""
        ob = self._obligations.get(obligation_id)
        if ob is None or ob.state is not DeliveryState.CLAIMED:
            return False
        ob.state = DeliveryState.DELIVERED
        ob.delivered_at = now if now is not None else time.time()
        return True

    def release(self, obligation_id: str) -> bool:
        """Devolve à fila: o dono morreu ou falhou de forma transitória."""
        ob = self._obligations.get(obligation_id)
        if ob is None or ob.state is not DeliveryState.CLAIMED:
            return False
        ob.state = DeliveryState.PENDING
        ob.owner_pid = ob.owner_started_at = None
        return True

    def pending(self) -> list[Obligation]:
        return [o for o in self._obligations.values() if o.state is DeliveryState.PENDING]

    def reclaim_dead(self, live_pids: set[int]) -> list[str]:
        """Devolve obrigações cujo dono está **provado morto**.

        Prova é o par `pid` + `started_at`, a mesma convenção do cron: o PID
        sozinho é reciclado pelo SO e daria um processo alheio como dono.
        """
        recuperadas = []
        for ob in self._obligations.values():
            if ob.state is DeliveryState.CLAIMED and ob.owner_pid not in live_pids:
                ob.state = DeliveryState.PENDING
                ob.owner_pid = ob.owner_started_at = None
                recuperadas.append(ob.obligation_id)
        return recuperadas


#: Cooldown progressivo, em segundos. Satura no último degrau.
COOLDOWN_LADDER: tuple[int, ...] = (60, 300, 900, 3600)


@dataclass
class _TargetState:
    consecutive_failures: int = 0
    suspended_until: float = 0.0
    permanently_dead: bool = False


class DeadTargets:
    """Circuit breaker por destino.

    Sem ele, um usuário que bloqueou o bot faz o gateway tentar a cada
    mensagem, para sempre — cada tentativa consumindo uma conexão do pool e
    um slot de rate limit que a conversa **viva** ao lado precisava.
    """

    def __init__(self) -> None:
        self._targets: dict[str, _TargetState] = {}

    def record_failure(self, target: str, kind: FailureKind, *, now: float | None = None) -> float:
        moment = now if now is not None else time.time()
        st = self._targets.setdefault(target, _TargetState())

        if kind is FailureKind.PERMANENT:
            # Não há cooldown que conserte um canal deletado.
            st.permanently_dead = True
            st.suspended_until = float("inf")
            return st.suspended_until

        st.consecutive_failures += 1
        idx = min(st.consecutive_failures, len(COOLDOWN_LADDER)) - 1
        st.suspended_until = moment + COOLDOWN_LADDER[idx]
        return st.suspended_until

    def record_success(self, target: str) -> None:
        """Sucesso **zera** o contador: a escada mede falha consecutiva, não
        acumulada — um destino que se recuperou não deve carregar a punição."""
        self._targets.pop(target, None)

    def is_suspended(self, target: str, *, now: float | None = None) -> bool:
        st = self._targets.get(target)
        if st is None:
            return False
        moment = now if now is not None else time.time()
        return moment < st.suspended_until

    def is_permanently_dead(self, target: str) -> bool:
        st = self._targets.get(target)
        return bool(st and st.permanently_dead)
