"""Claim atômico e o duplo portão de estado do job.

`_reversa_sdd/cron/` (Tarefa 14).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kairos_domain.scheduling import JobState

__all__ = [
    "ClaimResult",
    "DispatchClaimer",
    "LifecycleGuardError",
    "is_job_runnable",
    "reject_gateway_restart_job",
]


@dataclass
class ClaimResult:
    claimed: bool
    reason: str
    completed_after: int | None = None


@dataclass
class DispatchClaimer:
    """*At-most-times*: o claim roda **antes** do efeito colateral.

    O claim incrementa `repeat.completed` sob lock e **persiste
    imediatamente**. Se o tick morrer no meio — kill do gateway, OOM,
    segfault, hard-timeout — o dispatch **não é perdido**: já foi debitado.

    Isto converte *at-least-once* em *at-most-times*, e o escopo é
    deliberadamente cirúrgico (ADR 008, #38758): só `kind == "once"` **e**
    `repeat.times > 0`. Ampliar o escopo tornaria todo job recorrente sujeito
    a perder disparos por crash, trocando um problema raro por um comum.
    """

    _completed: dict[str, int] = field(default_factory=dict)

    def claim_dispatch(
        self, job_id: str, *, kind: str, repeat_times: int | None, in_store: bool = True
    ) -> ClaimResult:
        if not in_store:
            # Job cujo id não está no store prossegue **sem** claim: não há
            # onde debitar, e bloquear seria pior — o job simplesmente não
            # rodaria, sem explicação.
            return ClaimResult(True, "job fora do store: prossegue sem claim")

        if kind != "once" or not repeat_times or repeat_times <= 0:
            return ClaimResult(True, "fora do escopo do claim (só 'once' com repeat)")

        feitos = self._completed.get(job_id, 0)
        if feitos >= repeat_times:
            return ClaimResult(False, f"orçamento esgotado: {feitos}/{repeat_times}")

        self._completed[job_id] = feitos + 1
        return ClaimResult(True, "reivindicado", completed_after=feitos + 1)

    def record_catch_up_occurrence(self, job_id: str) -> int:
        """O catch-up **consome uma unidade** do orçamento.

        Sem isto, um job `once` atrasado disparia sem debitar nada — e
        poderia disparar de novo.
        """
        self._completed[job_id] = self._completed.get(job_id, 0) + 1
        return self._completed[job_id]

    def completed(self, job_id: str) -> int:
        return self._completed.get(job_id, 0)


def is_job_runnable(*, enabled: bool, paused: bool, state: JobState | None = None) -> bool:
    """**Duplo portão**: `enabled` mais os marcadores de pausa.

    *"So a contradictory half-paused record never fires."* Um registro em que
    `enabled` e os marcadores discordam é dado corrompido, e a leitura segura
    é não disparar.
    """
    if not enabled or paused:
        return False
    return state not in (JobState.PAUSED, JobState.DISABLED)


class LifecycleGuardError(ValueError):
    """Job que reiniciaria o gateway."""


#: Formas de comando que derrubariam o próprio processo que as executa.
_RESTART_MARKERS = (
    "kairos gateway restart",
    "kairos restart",
    "systemctl restart kairos",
    "docker restart",
    "kill -9",
    "pkill",
)


def reject_gateway_restart_job(command: str) -> None:
    """Rejeitado **na criação**, não na execução (#30719).

    Um job que reinicia o gateway mata o processo que o está executando: a
    execução nunca chega a um estado terminal durável, e no boot seguinte o
    scheduler a reconcilia como `unknown` — e o job dispara de novo. É laço
    de reinício disfarçado de automação.

    Rejeitar na execução seria tarde: o job já estaria salvo, e o usuário
    descobriria pelo sintoma.
    """
    baixo = command.lower()
    for marcador in _RESTART_MARKERS:
        if marcador in baixo:
            raise LifecycleGuardError(
                f"comando {command!r} reiniciaria o gateway. Um job assim mata o "
                f"processo que o executa: a execução nunca alcança estado terminal "
                f"durável, é reconciliada como 'unknown' no boot e dispara de novo — "
                f"laço de reinício disfarçado de automação."
            )
