"""Monitor de cron — a fonte roda antes, e o agente frequentemente NÃO roda.

`_reversa_sdd/cron/` §monitor (Tarefa 14).
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DIFF_MAX_LINES",
    "MonitorOutcome",
    "MonitorState",
    "evaluate",
    "output_hash",
    "render_change_block",
]

DIFF_MAX_LINES = 200


class MonitorOutcome(StrEnum):
    FIRST_RUN = "first_run"
    CHANGED = "changed"
    #: A execução **inteira** do agente é suprimida — sem LLM, sem entrega.
    #: O tick é registrado como `no_change`.
    NO_CHANGE = "no_change"
    SOURCE_ERROR = "source_error"


@dataclass(frozen=True)
class MonitorState:
    last_output_hash: str | None = None
    last_changed_at: str | None = None


def output_hash(output: str) -> str:
    """Hash por **bytes exatos**.

    Sem remoção de timestamp e sem normalização de espaços — normalizar seria
    adivinhar o que o usuário considera ruído. A consequência prática está no
    docstring do legado: scripts precisam emitir saída estável (ordenar
    resultados, omitir "generated at"), senão *todo tick parecerá mudança*.
    """
    return hashlib.sha256(output.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MonitorDecision:
    outcome: MonitorOutcome
    #: O estado a persistir. Em erro de fonte é o **mesmo** de antes.
    next_state: MonitorState
    run_agent: bool


def evaluate(
    state: MonitorState,
    current_output: str | None,
    *,
    source_failed: bool = False,
    now: str | None = None,
) -> MonitorDecision:
    """Decide se o agente roda neste tick.

    **Falha da fonte é ERRO, nunca mudança**, e o hash armazenado **não é
    tocado**. A consequência é a que importa: uma fonte que cai e volta com a
    saída anterior **continua suprimindo**. Se o erro atualizasse o hash, a
    volta ao normal pareceria mudança — alerta falso exatamente quando o
    sistema observado se recuperou.
    """
    if source_failed or current_output is None:
        return MonitorDecision(MonitorOutcome.SOURCE_ERROR, state, run_agent=False)

    atual = output_hash(current_output)

    if state.last_output_hash is None:
        return MonitorDecision(
            MonitorOutcome.FIRST_RUN,
            MonitorState(last_output_hash=atual, last_changed_at=now),
            run_agent=True,
        )

    if atual == state.last_output_hash:
        return MonitorDecision(MonitorOutcome.NO_CHANGE, state, run_agent=False)

    return MonitorDecision(
        MonitorOutcome.CHANGED,
        MonitorState(last_output_hash=atual, last_changed_at=now),
        run_agent=True,
    )


def render_change_block(previous: str, current: str) -> str:
    """O bloco injetado no prompt quando há mudança.

    Diff unificado **limitado**: uma saída que mudou por inteiro produziria um
    diff do tamanho da saída, e o prompt do agente é o recurso escasso aqui.
    """
    linhas = list(
        difflib.unified_diff(
            previous.splitlines(),
            current.splitlines(),
            fromfile="anterior",
            tofile="atual",
            lineterm="",
        )
    )
    cortado = len(linhas) > DIFF_MAX_LINES
    if cortado:
        linhas = linhas[:DIFF_MAX_LINES]
        linhas.append(f"... [diff truncado em {DIFF_MAX_LINES} linhas]")
    return "MONITOR CHANGE DETECTED\n\n" + "\n".join(linhas) + "\n\n" + current
