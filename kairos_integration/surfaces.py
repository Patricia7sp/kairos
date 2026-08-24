"""As seis superfícies sobre o mesmo núcleo, e as duas leis que as governam.

`_reversa_sdd/architecture.md` (Tarefa 20).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "PROCESS_TOPOLOGIES",
    "SURFACES",
    "CoreIsALibrary",
    "LawViolation",
    "ProcessTopology",
    "Surface",
    "SurfaceProfile",
    "assert_narrow_waist",
    "assert_prompt_cache_intact",
]


class Surface(StrEnum):
    CLI = "cli"
    GATEWAY = "gateway"
    TUI = "tui"
    ACP = "acp"
    DESKTOP = "desktop"
    DASHBOARD = "dashboard"


SURFACES: tuple[Surface, ...] = tuple(Surface)


class ProcessTopology(StrEnum):
    """As cinco topologias em que o núcleo roda."""

    FOREGROUND_CLI = "foreground_cli"
    LONG_LIVED_GATEWAY = "long_lived_gateway"
    SUPERVISED_CONTAINER = "supervised_container"
    EDITOR_CHILD = "editor_child"
    SCHEDULED_JOB = "scheduled_job"


PROCESS_TOPOLOGIES: tuple[ProcessTopology, ...] = tuple(ProcessTopology)


@dataclass(frozen=True)
class SurfaceProfile:
    """O que cada superfície liga e o que não liga.

    A tabela existe para tornar auditável uma frase que, solta, não é
    verificável: *"a guarda de aprovação é do editor"*. Aqui dá para
    perguntar **quais** superfícies a ligam.
    """

    surface: Surface
    #: Liga o guard de aprovação de edição do ACP?
    binds_edit_approval: bool = False
    #: Tem interlocutor humano para perguntar?
    interactive: bool = False
    #: Escreve no `state.db` compartilhado?
    writes_state_db: bool = True


SURFACE_PROFILES: dict[Surface, SurfaceProfile] = {
    Surface.CLI: SurfaceProfile(Surface.CLI, interactive=True),
    Surface.GATEWAY: SurfaceProfile(Surface.GATEWAY, interactive=True),
    Surface.TUI: SurfaceProfile(Surface.TUI, interactive=True),
    Surface.ACP: SurfaceProfile(Surface.ACP, binds_edit_approval=True, interactive=True),
    Surface.DESKTOP: SurfaceProfile(Surface.DESKTOP, interactive=True),
    Surface.DASHBOARD: SurfaceProfile(Surface.DASHBOARD, interactive=True),
}


# ---------------------------------------------------------------------------
# O núcleo é uma BIBLIOTECA, não um serviço
# ---------------------------------------------------------------------------


class CoreIsALibrary(AssertionError):
    """Alguém tratou o núcleo como serviço."""


@dataclass
class ProcessMap:
    """Quem escreve no `state.db`.

    O achado central da arquitetura: **não há "agent server"**. Cada
    superfície importa o núcleo no próprio processo, e há **sete processos
    escrevendo no mesmo SQLite**.

    Isto não é acidente a corrigir — é a forma do sistema, e é o que torna a
    contenção de escrita (Tarefa 05) uma preocupação de primeira ordem em vez
    de detalhe de persistência.
    """

    writers: set[str] = field(default_factory=set)

    def register_writer(self, name: str) -> None:
        self.writers.add(name)

    def assert_no_central_server(self) -> None:
        """Um único escritor significaria que alguém pôs um servidor no meio.

        Não é ilegal — é uma mudança de arquitetura que precisa ser decisão,
        não deriva. A verificação existe para que apareça.
        """
        if len(self.writers) == 1:
            raise CoreIsALibrary(
                "um único escritor no state.db sugere que o núcleo virou serviço. "
                "Se foi decisão, registre-a; se foi deriva, é regressão."
            )


# ---------------------------------------------------------------------------
# As duas leis
# ---------------------------------------------------------------------------


class LawViolation(AssertionError):
    """Violação de uma das duas leis que governam a arquitetura."""


def assert_prompt_cache_intact(
    *,
    mutated_past_context: bool = False,
    swapped_toolset: bool = False,
    rebuilt_system_prompt: bool = False,
    is_compaction: bool = False,
) -> None:
    """**Lei 1** — o cache de prompt por conversa é sagrado.

    Alterar contexto passado, trocar toolset ou reconstruir o system prompt no
    meio da conversa invalida o prefixo cacheado e **multiplica o custo do
    usuário**. A compactação é a **única** exceção autorizada.

    A verificação vale para as seis superfícies: a lei não é do agente, é do
    sistema — e uma superfície que a viole custa dinheiro real ao usuário sem
    que nada na tela indique isso.
    """
    if is_compaction:
        return

    violacoes = [
        nome
        for nome, ocorreu in (
            ("alterou contexto passado", mutated_past_context),
            ("trocou toolset", swapped_toolset),
            ("reconstruiu o system prompt", rebuilt_system_prompt),
        )
        if ocorreu
    ]
    if violacoes:
        raise LawViolation(
            f"Lei 1 violada ({', '.join(violacoes)}): o prefixo cacheado é invalidado "
            f"e o custo do usuário se multiplica. A única exceção é a compactação."
        )


def assert_narrow_waist(
    *, new_core_tool: bool, solvable_by_terminal_and_file: bool, solvable_by_skill: bool
) -> None:
    """**Lei 2** — o núcleo é uma cintura estreita.

    *"Every model tool we add is sent on every API call"*, então o critério
    para ferramenta **core** é alto. Capacidade nova deve chegar como comando
    de CLI + skill, ferramenta com portão de serviço, ou plugin.
    """
    if not new_core_tool:
        return
    if solvable_by_terminal_and_file or solvable_by_skill:
        raise LawViolation(
            "Lei 2 violada: ferramenta core nova cujo problema terminal+file ou uma "
            "skill já resolvem. Toda ferramenta core vai em TODA chamada de API — "
            "o custo é permanente e pago por cada usuário, em cada turno."
        )
