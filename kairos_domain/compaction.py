"""As 10 regras de compactação.

``_reversa_sdd/domain.md`` §2.2. A compactação é **a única exceção
autorizada** ao contrato de cache (§2.1) — por isso suas regras são o
conjunto mais denso do domínio.
"""

from __future__ import annotations

from dataclasses import dataclass

from kairos_domain.message import (
    DomainRuleViolation,
    Message,
    Role,
    check_tool_pairs_intact,
)

__all__ = [
    "CompactionPolicy",
    "CompactionError",
    "SummaryProvenanceError",
    "plan_boundary",
    "effective_protect_first_n",
    "validate_summary_provenance",
    "next_cooldown_seconds",
    "COOLDOWN_LADDER",
]


class CompactionError(DomainRuleViolation):
    """Violação de uma regra de compactação."""


class SummaryProvenanceError(CompactionError):
    """Regra 4 — o sumarizador não pode fabricar turnos de usuário."""


#: Regra 6 — falhas consecutivas escalam o cooldown (escada compartilhada).
COOLDOWN_LADDER: tuple[int, ...] = (60, 300, 900)


@dataclass(frozen=True)
class CompactionPolicy:
    """Limiar por modelo, com piso e teto (domain §2.2)."""

    threshold_percent: float = 0.75
    #: Piso para contexto pequeno. É **raise-only**: nunca abaixa o limiar
    #: efetivo, só o levanta — um modelo de contexto pequeno precisa comprimir
    #: mais cedo, nunca mais tarde.
    small_context_floor_percent: float | None = None
    #: Teto absoluto opcional, em tokens.
    absolute_ceiling_tokens: int | None = None
    #: Tokens reservados para a saída, subtraídos da janela disponível.
    reserved_output_tokens: int = 0
    #: Quantas mensagens da cabeça proteger, antes do decaimento.
    protect_first_n: int = 2
    #: Quantas mensagens reais da cauda sempre sobrevivem (regra 2).
    protect_last_n: int = 3
    enabled: bool = True

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold_percent <= 1.0:
            raise ValueError("threshold_percent deve estar em (0, 1]")
        if self.reserved_output_tokens < 0:
            raise ValueError("reserved_output_tokens não pode ser negativo")

    def effective_threshold(self) -> float:
        """O piso é *raise-only*."""
        if self.small_context_floor_percent is None:
            return self.threshold_percent
        return max(self.threshold_percent, self.small_context_floor_percent)

    def trigger_tokens(self, context_window: int) -> int:
        """A partir de quantos tokens a compactação proativa dispara."""
        usable = max(0, context_window - self.reserved_output_tokens)
        trigger = int(usable * self.effective_threshold())
        if self.absolute_ceiling_tokens is not None:
            trigger = min(trigger, self.absolute_ceiling_tokens)
        return trigger


def effective_protect_first_n(base: int, cycle: int) -> int:
    """Regra 3 — a proteção da cabeça **decai** a cada ciclo.

    Sem o decaimento, uma cabeça protegida grande trava a compactação
    indefinidamente: o compressor não consegue liberar espaço suficiente e
    tenta de novo, ciclo após ciclo, gastando uma chamada de sumarização
    cada vez.
    """
    if cycle < 0:
        raise ValueError("cycle não pode ser negativo")
    return max(0, base - cycle)


def plan_boundary(messages: list[Message], policy: CompactionPolicy) -> int:
    """Devolve o índice de corte que respeita as regras 1 e 2.

    Regra 1 — nunca cortar no meio de um par ``tool_call``/``tool_result``:
    a fronteira é **empurrada** para frente até sair do par.
    Regra 2 — as últimas ``protect_last_n`` mensagens **reais** sobrevivem,
    independentemente da pressão de tokens.
    """
    total = len(messages)
    if total == 0:
        return 0

    real_indices = [i for i, m in enumerate(messages) if m.role in (Role.USER, Role.ASSISTANT)]
    tail = real_indices[-policy.protect_last_n:] if policy.protect_last_n else []
    ceiling = tail[0] if tail else total

    boundary = min(policy.protect_first_n, ceiling)

    # Empurra para fora de um par tool_call/tool_result aberto.
    while boundary < ceiling:
        head = messages[:boundary]
        try:
            check_tool_pairs_intact(head)
        except DomainRuleViolation:
            boundary += 1
            continue
        break

    return min(boundary, ceiling)


def validate_summary_provenance(summary_messages: list[Message]) -> None:
    """Regra 4 — o sumarizador **não pode fabricar turnos de usuário**.

    Um sumário que inventa uma fala do usuário contamina o transcript com
    algo que a pessoa não disse — e, pior, o modelo passa a tratar a
    invenção como instrução recebida nos turnos seguintes.
    """
    for index, message in enumerate(summary_messages):
        if message.role is Role.USER:
            raise SummaryProvenanceError(
                f"o sumário contém um turno de usuário na posição {index}; "
                "sumarizador não fabrica fala de usuário"
            )


def next_cooldown_seconds(consecutive_failures: int) -> int:
    """Regra 6 — escada de cooldown, saturando no último degrau."""
    if consecutive_failures <= 0:
        return 0
    index = min(consecutive_failures, len(COOLDOWN_LADDER)) - 1
    return COOLDOWN_LADDER[index]
