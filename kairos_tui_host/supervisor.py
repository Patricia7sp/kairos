"""Supervisor do processo de compute — isolamento contra GIL starvation.

`_reversa_sdd/ui-tui/` §1 e ADR 012 (Tarefa 16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "GIL_STARVATION_RATIONALE",
    "HostState",
    "HostSupervisor",
    "RestartPolicy",
]

GIL_STARVATION_RATIONALE = """\
O laço de turno da LLM e a execução de ferramentas rodam num processo FILHO,
não numa thread.

Uma thread não bastaria: uma ferramenta em C que não solta o GIL, ou um laço
de CPU puro em Python, monopoliza o interpretador — e a TUI, que precisa
desenhar a 60 FPS, congela. O usuário vê a interface travar e não tem como
distinguir isso de um travamento real do agente.

Processo separado significa que o congelamento do compute é **observável e
interrompível** pela TUI, em vez de arrastá-la junto.
"""


class HostState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    #: Vivo, mas sem responder ao heartbeat.
    UNRESPONSIVE = "unresponsive"
    CRASHED = "crashed"


@dataclass(frozen=True)
class RestartPolicy:
    max_restarts: int = 3
    #: Janela em que os reinícios são contados. Fora dela o contador zera —
    #: um processo que roda bem por horas e cai uma vez não é um processo que
    #: falha em laço.
    window_seconds: float = 300.0
    #: Depois de estourar, o supervisor **para de tentar**: reiniciar em laço
    #: consome CPU e esconde o erro real numa enxurrada de logs iguais.
    give_up_after_window: bool = True


@dataclass
class HostSupervisor:
    policy: RestartPolicy = field(default_factory=RestartPolicy)
    state: HostState = HostState.STOPPED
    _restarts: list[float] = field(default_factory=list)
    _gave_up: bool = False

    def note_crash(self, *, now: float) -> bool:
        """Registra uma queda. Devolve se deve reiniciar."""
        self.state = HostState.CRASHED
        limite = now - self.policy.window_seconds
        self._restarts = [t for t in self._restarts if t >= limite]

        if len(self._restarts) >= self.policy.max_restarts:
            self._gave_up = self.policy.give_up_after_window
            return not self._gave_up

        self._restarts.append(now)
        self.state = HostState.STARTING
        return True

    def note_ready(self) -> None:
        self.state = HostState.READY

    def note_heartbeat_missed(self) -> None:
        """Sem heartbeat ≠ morto.

        Um compute host ocupado num laço de CPU está **vivo e travado** — o
        supervisor precisa distinguir os dois, porque a resposta é diferente:
        morto se reinicia, travado se interrompe.
        """
        if self.state is HostState.READY:
            self.state = HostState.UNRESPONSIVE

    @property
    def gave_up(self) -> bool:
        return self._gave_up

    @property
    def restart_count(self) -> int:
        return len(self._restarts)
