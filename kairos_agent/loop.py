"""O laço de conversa — parada, orçamento e as 12 razões de saída.

`_reversa_sdd/agent/` (Tarefa 13). O corpo de `conversation_loop.py` foi lido
na verificação de lacunas; o que segue reproduz o que aquela leitura devolveu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "ExitReason",
    "IterationBudget",
    "LoopLimits",
    "TurnOutcome",
    "should_continue",
]


class ExitReason(StrEnum):
    """As 12 saídas **antecipadas**, nomeadas.

    A saída normal — resposta sem chamadas de ferramenta — **não recebe
    rótulo**, e isso é deliberado: o campo existe para explicar por que um
    turno terminou antes do esperado. Rotular o caminho feliz diluiria o
    sinal.
    """

    INTERRUPTED_BY_USER = "interrupted_by_user"
    INTERRUPTED_DURING_API_CALL = "interrupted_during_api_call"
    BUDGET_EXHAUSTED = "budget_exhausted"
    GUARDRAIL_HALT = "guardrail_halt"
    COMPACTION_HANDOFF_NOT_ACTIONABLE = "compaction_handoff_not_actionable"
    SESSION_PERSISTENCE_FAILED = "session_persistence_failed"
    EMPTY_RESPONSE_EXHAUSTED = "empty_response_exhausted"
    ALL_RETRIES_EXHAUSTED_NO_RESPONSE = "all_retries_exhausted_no_response"
    PARTIAL_STREAM_RECOVERY = "partial_stream_recovery"
    FALLBACK_PRIOR_TURN_CONTENT = "fallback_prior_turn_content"
    OLLAMA_RUNTIME_CONTEXT_TOO_SMALL = "ollama_runtime_context_too_small"
    UNKNOWN = "unknown"


@dataclass
class IterationBudget:
    """Orçamento consumível, com **estorno**.

    O estorno existe porque nem toda iteração chega ao provedor: um preflight
    abortado ou uma compressão adiada gastariam orçamento sem gastar chamada,
    e o turno morreria antes de fazer o trabalho.
    """

    max_total: int = 30
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_total - self.used)

    def consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.used += 1
        return True

    def refund(self) -> None:
        """Devolve uma unidade. Só para caminho que não tocou o provedor."""
        self.used = max(0, self.used - 1)


@dataclass
class LoopLimits:
    """Os **dois** limites independentes, mais o *grace call*."""

    max_iterations: int = 30
    budget: IterationBudget = field(default_factory=IterationBudget)
    #: Quando o orçamento esgota, o modelo ganha UMA última chamada. A flag é
    #: consumida no início dela, garantindo que o laço saia depois — qualquer
    #: que seja o resultado.
    grace_call: bool = False

    def offer_grace(self) -> None:
        if self.budget.remaining <= 0:
            self.grace_call = True


def should_continue(limits: LoopLimits, api_call_count: int) -> bool:
    """A condição do laço.

    São dois limites **independentes**: `max_iterations` é teto absoluto de
    chamadas, `budget` é consumível e estornável. Um só não bastaria — o teto
    protege contra laço infinito, o orçamento contra custo, e eles se esgotam
    por motivos diferentes.
    """
    dentro = api_call_count < limits.max_iterations and limits.budget.remaining > 0
    return dentro or limits.grace_call


@dataclass
class TurnOutcome:
    completed: bool
    api_calls: int
    exit_reason: ExitReason | None = None

    @property
    def ended_early(self) -> bool:
        return self.exit_reason is not None
