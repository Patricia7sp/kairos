"""Mensagens, papéis e as três visibilidades.

Reconstruído de ``_reversa_sdd/domain.md`` §2.1-2.2 e §4, e
``_reversa_sdd/data-dictionary.md`` §6.2 (Tarefa 02).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Role",
    "Visibility",
    "Message",
    "RoleAlternationError",
    "SyntheticUserMessageError",
    "ToolPairError",
    "check_role_alternation",
    "check_no_synthetic_user_message",
    "check_tool_pairs_intact",
]


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class Visibility(str, Enum):
    """As três visibilidades, derivadas de ``active``/``compacted``.

    A combinação ``active=1, compacted=1`` não existe: uma mensagem não pode
    estar simultaneamente no contexto do modelo e arquivada por compressão.
    """

    ACTIVE = "active"        # active=1 compacted=0 — busca ✅, contexto ✅
    ARCHIVED = "archived"    # active=0 compacted=1 — busca ✅, contexto ❌
    REWOUND = "rewound"      # active=0 compacted=0 — busca ❌, contexto ❌

    @property
    def in_model_context(self) -> bool:
        return self is Visibility.ACTIVE

    @property
    def searchable(self) -> bool:
        return self is not Visibility.REWOUND


class DomainRuleViolation(Exception):
    """Base de todas as violações de regra de domínio."""


class RoleAlternationError(DomainRuleViolation):
    """Invariante 2 — nunca duas mensagens do mesmo papel seguidas."""


class SyntheticUserMessageError(DomainRuleViolation):
    """Invariante 3 — nunca uma mensagem de usuário sintética mid-loop."""


class ToolPairError(DomainRuleViolation):
    """Invariante 4 — compactação nunca corta um par tool_call/tool_result."""


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None = None
    #: SIDECAR — o que vai para a API, quando difere do que é exibido.
    #: É o mecanismo que sustenta a Lei 1: reescrever ``content`` para
    #: exibição não altera o prefixo enviado ao provedor.
    api_content: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[str, ...] = ()
    tool_name: str | None = None
    active: bool = True
    compacted: bool = False
    #: Verdadeiro quando a mensagem foi fabricada pelo sistema em vez de
    #: enviada por uma pessoa. Ver :func:`check_no_synthetic_user_message`.
    synthetic: bool = False

    def __post_init__(self) -> None:
        if self.active and self.compacted:
            raise ValueError(
                "active=1 e compacted=1 é estado impossível: uma mensagem não "
                "pode estar no contexto do modelo e arquivada ao mesmo tempo"
            )
        if self.role is Role.TOOL and not self.tool_call_id:
            raise ValueError("mensagem de papel 'tool' exige tool_call_id")

    @property
    def visibility(self) -> Visibility:
        if self.active:
            return Visibility.ACTIVE
        return Visibility.ARCHIVED if self.compacted else Visibility.REWOUND

    @property
    def for_api(self) -> str | None:
        """O que efetivamente vai para o provedor."""
        return self.api_content if self.api_content is not None else self.content


# ---------------------------------------------------------------------------
# Invariantes de sequência
# ---------------------------------------------------------------------------

def check_role_alternation(messages: list[Message]) -> None:
    """Invariante 2 — *"never two same-role messages in a row"*.

    Mensagens de papel ``tool`` são isentas: uma chamada do assistente pode
    produzir várias respostas de ferramenta em sequência, e isso é a forma
    normal do protocolo, não uma violação.
    """
    previous: Role | None = None
    for index, message in enumerate(messages):
        if message.role is Role.TOOL:
            previous = None
            continue
        if previous is not None and message.role is previous:
            raise RoleAlternationError(
                f"duas mensagens de papel '{message.role.value}' em sequência "
                f"na posição {index}"
            )
        previous = message.role


def check_no_synthetic_user_message(messages: list[Message]) -> None:
    """Invariante 3 — nenhuma mensagem de usuário sintética mid-loop.

    O motivo é o contrato de cache: injetar um turno de usuário fabricado no
    meio do laço altera o prefixo que o provedor tem em cache. E é também
    honestidade de transcript — o histórico não deve conter falas que a
    pessoa não disse.
    """
    for index, message in enumerate(messages):
        if message.role is Role.USER and message.synthetic:
            raise SyntheticUserMessageError(
                f"mensagem de usuário sintética na posição {index}"
            )


def check_tool_pairs_intact(messages: list[Message]) -> None:
    """Invariante 4 / regra 1 de compactação.

    Todo ``tool_call`` anunciado por um assistente precisa do seu
    ``tool_result``, e todo resultado precisa da sua chamada. Um corte que
    quebre o par produz histórico que o provedor rejeita — por isso a
    fronteira de compactação é **empurrada ou puxada**, nunca aplicada no
    meio de um par.
    """
    announced: list[str] = []
    for message in messages:
        if message.role is Role.ASSISTANT:
            announced.extend(message.tool_calls)

    answered = {m.tool_call_id for m in messages if m.role is Role.TOOL}

    orphan_calls = [c for c in announced if c not in answered]
    if orphan_calls:
        raise ToolPairError(f"tool_call sem tool_result: {orphan_calls}")

    orphan_results = [r for r in sorted(answered) if r not in announced]
    if orphan_results:
        raise ToolPairError(f"tool_result sem tool_call: {orphan_results}")
