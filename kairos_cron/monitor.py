"""Monitor de cron — a fonte roda antes, e o agente frequentemente NÃO roda.

`_reversa_sdd/cron/` §monitor (Tarefa 14).
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from enum import StrEnum

from kairos_cron.source import validate_script

__all__ = [
    "DIFF_MAX_LINES",
    "MonitorOutcome",
    "MonitorState",
    "decide_for_source",
    "default_monitor_state",
    "evaluate",
    "monitor_state_dict",
    "monitor_state_from_job",
    "output_hash",
    "validate_monitor",
    "validate_monitor_state",
]

DIFF_MAX_LINES = 200
#: A saída da fonte é truncada antes de comparar; notificar por uma mudança
#: que só existe além do corte seria comparar bytes que o agente nunca veria.
MONITOR_MAX_OUTPUT_CHARS = 65_536


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


def validate_monitor(monitor: object) -> dict:
    """Valida a configuração de fonte de um job antes da persistência.

    Hoje só o tipo ``script`` existe; a forma é estrita para que um campo
    novo nunca seja aceito em silêncio. A fonte é executada sem shell, com
    orçamentos fixos (ver ``kairos_cron.source``).
    """
    if not isinstance(monitor, dict) or set(monitor) != {"type", "script"}:
        raise ValueError("monitor inválido")
    if monitor["type"] != "script":
        raise ValueError("tipo de monitor não suportado")
    validate_script(monitor["script"])
    return {"type": "script", "script": monitor["script"].strip()}


def validate_monitor_state(state: object) -> dict:
    """Estado persistido de um monitor: hash, último marco e última verificação."""
    if not isinstance(state, dict) or set(state) != {
        "last_output_hash",
        "last_changed_at",
        "last_checked_at",
    }:
        raise ValueError("estado de monitor inválido")
    hash_value = state["last_output_hash"]
    if hash_value is not None and (not isinstance(hash_value, str) or len(hash_value) != 64):
        raise ValueError("hash de saída de monitor inválido")
    for key in ("last_changed_at", "last_checked_at"):
        value = state[key]
        if value is not None and (not isinstance(value, str) or len(value) > 40):
            raise ValueError(f"{key} inválido")
    return dict(state)


def default_monitor_state() -> dict:
    return {"last_output_hash": None, "last_changed_at": None, "last_checked_at": None}


def monitor_state_from_job(job: dict) -> MonitorState:
    """Converte o estado persistido no objeto de decisão (sem estado → primeiro run)."""
    raw = job.get("monitor_state")
    if raw is None:
        return MonitorState()
    validated = validate_monitor_state(raw)
    return MonitorState(
        last_output_hash=validated["last_output_hash"],
        last_changed_at=validated["last_changed_at"],
    )


def monitor_state_dict(state: MonitorState, *, checked_at: str | None = None) -> dict:
    """Converte o estado de decisão no dicionário persistido, com a verificação."""
    return {
        "last_output_hash": state.last_output_hash,
        "last_changed_at": state.last_changed_at,
        "last_checked_at": checked_at,
    }


def decide_for_source(
    state: MonitorState, source_result, *, now: str | None = None
) -> MonitorDecision:
    """Decide a partir do resultado real da fonte.

    Uma fonte que falhou (timeout, saída não nula, executável ausente) é
    **erro, nunca mudança** — a mesma consequência de ``evaluate`` com
    ``source_failed=True``: o hash fica intocado.
    """
    if source_result.ok:
        return evaluate(state, source_result.output, now=now)
    return evaluate(state, None, source_failed=True, now=now)


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
