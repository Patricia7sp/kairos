"""Micro-compactação — opt-in, e o motivo do default vale como regra.

`_reversa_sdd/agent/` (Tarefa 13).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MicroCompactionConfig", "should_micro_compact"]


@dataclass(frozen=True)
class MicroCompactionConfig:
    """Três dials, e o default é **desligado**.

    A passada **reescreve histórico já enviado**, o que quebra o prefixo do
    prompt-cache do provedor **a cada turno** em vez de numa fronteira
    episódica. Esse é exatamente o custo que `proactive_prune_min_reclaim_tokens`
    existe para amortizar — por isso o recurso só liga quando um operador
    aceita o tradeoff conscientemente.
    """

    enabled: bool = False
    #: Cadência em turnos concluídos. 1 = todo turno (reclamação máxima, uma
    #: quebra de cache por turno); 5 = uma quebra a cada cinco.
    every_n_turns: int = 1
    #: Limiar de defrag do resumo rolante.
    defrag_threshold_tokens: int = 2000

    def __post_init__(self) -> None:
        if self.every_n_turns < 1:
            raise ValueError(
                "every_n_turns é travado em >= 1: zero significaria "
                "reescrever o histórico continuamente"
            )
        if self.defrag_threshold_tokens < 1:
            raise ValueError("defrag_threshold_tokens deve ser positivo")


def should_micro_compact(cfg: MicroCompactionConfig, completed_turns: int) -> bool:
    if not cfg.enabled:
        return False
    if completed_turns <= 0:
        return False
    return completed_turns % cfg.every_n_turns == 0
