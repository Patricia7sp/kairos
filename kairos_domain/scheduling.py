"""Regras de agendamento — ``_reversa_sdd/domain.md`` §2.5."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kairos_domain.message import DomainRuleViolation

__all__ = [
    "ExecutionStatus",
    "JobState",
    "SchedulingError",
    "DeadSubprocessReportedSuccess",
    "TerminalStateMutated",
    "effective_job_state",
    "collapse_backlog",
    "assert_terminal_immutable",
    "finish_status_for_dead_owner",
    "monitor_hash_after_source_failure",
]


class ExecutionStatus(str, Enum):
    """Os 5 estados do CHECK constraint (Tarefa 01)."""

    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.UNKNOWN,
        )


class JobState(str, Enum):
    ENABLED = "enabled"
    PAUSED = "paused"
    DISABLED = "disabled"


class SchedulingError(DomainRuleViolation):
    """Violação de uma regra de agendamento."""


class DeadSubprocessReportedSuccess(SchedulingError):
    """Invariante 15 — job com subprocesso morto nunca reporta sucesso."""


class TerminalStateMutated(SchedulingError):
    """Invariante 7 — estados terminais do ledger são imutáveis."""


def effective_job_state(*, enabled: bool, paused: bool) -> JobState:
    """*"The 07-30 outage failure mode"*: um job ``enabled=true`` **nunca**
    pode aparecer como pausado.

    ``enabled`` é a intenção declarada do usuário e vence a flag derivada.
    Na falha de 2026-07-30 a precedência estava invertida, e jobs habilitados
    apareciam pausados — de modo que ninguém percebeu que não rodavam.
    """
    if enabled:
        return JobState.ENABLED
    return JobState.PAUSED if paused else JobState.DISABLED


def collapse_backlog(missed_occurrences: int) -> int:
    """Backlog acumulado **colapsa**, mas o job dispara **uma vez**.

    Sem o colapso, um gateway que ficou dias fora dispara uma rajada no
    restart; com colapso mas sem o disparo, o job é adiado perpetuamente.
    A regra resolve os dois de uma vez.
    """
    if missed_occurrences < 0:
        raise ValueError("missed_occurrences não pode ser negativo")
    return 1 if missed_occurrences else 0


def assert_terminal_immutable(current: ExecutionStatus, new: ExecutionStatus) -> None:
    """Invariante 7 — o que já terminou não muda de ideia."""
    if current.is_terminal and new is not current:
        raise TerminalStateMutated(
            f"execução em estado terminal {current.value!r} não pode virar {new.value!r}"
        )


def finish_status_for_dead_owner(*, owner_is_live: bool) -> ExecutionStatus:
    """Invariante 8 e 15 — "abandonado" exige **prova de morte**.

    A prova é o par PID + hora de início: o PID sozinho é reciclado pelo SO
    e acusaria como vivo um processo que já morreu.

    O destino é ``unknown``, nunca ``failed`` e nunca ``completed``. Perdida
    a observação, não se sabe se os efeitos colaterais rodaram — e afirmar
    sucesso seria a violação do invariante 15.
    """
    if owner_is_live:
        raise SchedulingError("dono vivo: a execução não pode ser reconciliada")
    return ExecutionStatus.UNKNOWN


def assert_not_reporting_success_when_dead(
    *, owner_is_live: bool, status: ExecutionStatus
) -> None:
    """Invariante 15, na forma de guarda."""
    if not owner_is_live and status is ExecutionStatus.COMPLETED:
        raise DeadSubprocessReportedSuccess(
            "subprocesso morto não pode reportar 'completed'"
        )


@dataclass(frozen=True)
class MonitorState:
    last_output_hash: str | None
    last_changed_at: float | None


def monitor_hash_after_source_failure(state: MonitorState) -> MonitorState:
    """Falha da fonte de monitor é **ERRO, nunca mudança**.

    Se a fonte cai e o hash for atualizado com o erro, o monitor reporta
    "mudou" — e o usuário recebe um alerta falso justamente quando o sistema
    observado está indisponível. O hash fica **intocado**.
    """
    return state
