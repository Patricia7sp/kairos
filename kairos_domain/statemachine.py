"""Primitiva de máquina de estados.

As 13 máquinas de ``_reversa_sdd/state-machines.md`` são declaradas como
dados, não como cadeias de ``if``. O ganho não é elegância: é que uma
transição **não declarada** passa a ser rejeitada por construção, em vez de
cair silenciosamente num ``else``.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field

__all__ = [
    "IllegalTransition",
    "UnknownState",
    "Transition",
    "StateMachine",
]


def _label(state: object) -> str:
    """Rótulo legível de um estado.

    ``str()`` de um membro de enum devolve ``Classe.MEMBRO``, que é ruído em
    mensagem de erro — quem depura quer ver o valor que aparece na spec e no
    banco.
    """
    value = getattr(state, "value", state)
    return str(value)


class IllegalTransition(Exception):
    """Transição existente no código mas ausente da máquina declarada."""


class UnknownState(Exception):
    """Estado que a máquina não conhece."""


@dataclass(frozen=True)
class Transition:
    source: Hashable
    target: Hashable
    #: O evento/chamada que a provoca, como aparece na spec.
    trigger: str
    #: Condição verbal que precisa valer. Documental — a imposição real vive
    #: na função de domínio correspondente, referenciada aqui.
    guard: str | None = None


@dataclass
class StateMachine:
    name: str
    states: frozenset[Hashable]
    initial: Hashable
    transitions: tuple[Transition, ...]
    #: Estados a partir dos quais nada mais sai.
    terminal: frozenset[Hashable] = field(default_factory=frozenset)
    source_ref: str = ""

    def __post_init__(self) -> None:
        unknown = {t.source for t in self.transitions} | {t.target for t in self.transitions}
        unknown -= self.states
        if unknown:
            raise UnknownState(f"{self.name}: transições citam estados não declarados: {sorted(map(_label, unknown))}")
        if self.initial not in self.states:
            raise UnknownState(f"{self.name}: estado inicial {self.initial!r} não declarado")
        if not self.terminal <= self.states:
            raise UnknownState(f"{self.name}: estados terminais fora da declaração")

        leaving_terminal = [t for t in self.transitions if t.source in self.terminal]
        if leaving_terminal:
            raise IllegalTransition(
                f"{self.name}: estado terminal com saída declarada: "
                f"{[(_label(t.source), _label(t.target)) for t in leaving_terminal]}"
            )

    # -- consulta ----------------------------------------------------------

    def can(self, source: Hashable, target: Hashable) -> bool:
        return any(t.source == source and t.target == target for t in self.transitions)

    def targets_from(self, source: Hashable) -> frozenset[Hashable]:
        self._assert_known(source)
        return frozenset(t.target for t in self.transitions if t.source == source)

    def transition(self, source: Hashable, target: Hashable) -> Transition:
        """Devolve a transição declarada, ou levanta.

        É o ponto único por onde qualquer mudança de estado deve passar.
        """
        self._assert_known(source)
        self._assert_known(target)
        for t in self.transitions:
            if t.source == source and t.target == target:
                return t
        if source in self.terminal:
            raise IllegalTransition(
                f"{self.name}: {_label(source)} é terminal e não pode virar {_label(target)}"
            )
        raise IllegalTransition(
            f"{self.name}: transição {_label(source)} → {_label(target)} não é declarada; "
            f"a partir de {_label(source)} só existem "
            f"{sorted(map(_label, self.targets_from(source)))}"
        )

    def unreachable(self) -> frozenset[Hashable]:
        """Estados que nenhuma transição alcança e que não são o inicial.

        Um estado inalcançável é quase sempre um erro de transcrição da spec
        ou um caminho que se perdeu numa refatoração.
        """
        reached = {self.initial} | {t.target for t in self.transitions}
        return frozenset(self.states - reached)

    def dead_ends(self) -> frozenset[Hashable]:
        """Estados sem saída que **não** foram declarados terminais."""
        with_exit = {t.source for t in self.transitions}
        return frozenset(self.states - with_exit - self.terminal)

    def _assert_known(self, state: Hashable) -> None:
        if state not in self.states:
            raise UnknownState(f"{self.name}: estado desconhecido {_label(state)!r}")


def validate(machines: Iterable[StateMachine]) -> None:
    """Levanta na primeira máquina malformada."""
    for m in machines:
        if m.unreachable():
            raise UnknownState(f"{m.name}: estados inalcançáveis {sorted(map(_label, m.unreachable()))}")
        if m.dead_ends():
            raise IllegalTransition(
                f"{m.name}: estados sem saída não declarados terminais: "
                f"{sorted(map(_label, m.dead_ends()))}"
            )
