"""`CapabilityDescriptor` — o contrato do adapter de plataforma.

`_reversa_sdd/providers-gateway/` §3 (Tarefa 11).

Substitui a herança cega de um `BasePlatformAdapter` com 131 métodos, que a
`architecture.md` marca como violação da Lei 2 (cintura estreita). Um adapter
**declara** o que sabe fazer, em vez de sobrescrever o que não sabe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["CapabilityDescriptor", "MarkdownDialect", "Operation", "WakeupMode"]


class MarkdownDialect(StrEnum):
    NONE = "none"
    COMMONMARK = "commonmark"
    TELEGRAM_MARKDOWN_V2 = "telegram_markdown_v2"
    SLACK_MRKDWN = "slack_mrkdwn"
    DISCORD = "discord"


class Operation(StrEnum):
    SEND = "send"
    EDIT = "edit"
    DELETE = "delete"
    REACT = "react"
    TYPING = "typing"
    UPLOAD = "upload"
    THREAD = "thread"


class WakeupMode(StrEnum):
    #: O gateway despacha em background e responde ao webhook na hora.
    ASYNC_DELIVERY = "async"
    #: O webhook **bloqueia** até o turno terminar e responde no payload HTTP.
    SYNC_DELIVERY = "sync"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """O que este adapter sabe fazer.

    `contract_version` é o que permite ao gateway servir adapters de gerações
    diferentes sem adivinhação: um descritor de versão futura com campos
    desconhecidos é legível na parte que importa, em vez de quebrar.
    """

    contract_version: int = 1
    max_message_length: int = 4_000
    supports_draft_streaming: bool = False
    supports_edit: bool = False
    supports_threads: bool = False
    markdown_dialect: MarkdownDialect = MarkdownDialect.NONE
    supported_ops: frozenset[Operation] = field(default_factory=frozenset)
    wakeup_mode: WakeupMode = WakeupMode.ASYNC_DELIVERY

    def supports(self, op: Operation) -> bool:
        return op in self.supported_ops

    def __post_init__(self) -> None:
        if self.max_message_length <= 0:
            raise ValueError("max_message_length deve ser positivo")
        # Coerência: prometer edição progressiva sem saber editar produz
        # streaming que nunca atualiza nada.
        if self.supports_draft_streaming and not self.supports_edit:
            raise ValueError(
                "supports_draft_streaming exige supports_edit: o streaming "
                "progressivo é implementado editando a mensagem já enviada"
            )
