"""Os 7 eventos tipados de streaming.

`_reversa_sdd/providers-gateway/` §3 (Tarefa 11).

⚠️ A spec registra que uma versão anterior listava sete nomes que **não
existem no repositório** (`StreamDelta`, `ToolCallComplete`, `TurnFinished`…).
Estes são os verdadeiros, todos `frozen=True`: um evento de stream que pode
ser mutado depois de emitido produz corrida entre o consumidor e o produtor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "STREAM_EVENTS",
    "Commentary",
    "GatewayNotice",
    "LongToolHint",
    "MessageChunk",
    "MessageStop",
    "StreamEvent",
    "ToolCallChunk",
    "ToolCallFinished",
]


@dataclass(frozen=True)
class StreamEvent:
    """Base. Imutável por construção."""


@dataclass(frozen=True)
class MessageChunk(StreamEvent):
    text: str


@dataclass(frozen=True)
class MessageStop(StreamEvent):
    #: `False` fecha um bloco; `True` fecha o turno.
    final: bool = False


@dataclass(frozen=True)
class Commentary(StreamEvent):
    """Fora da resposta principal — não entra no transcript do assistente."""

    text: str


@dataclass(frozen=True)
class ToolCallChunk(StreamEvent):
    tool_name: str
    preview: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    index: int = 0


@dataclass(frozen=True)
class ToolCallFinished(StreamEvent):
    tool_name: str
    duration: float = 0.0
    ok: bool = True
    index: int = 0


@dataclass(frozen=True)
class LongToolHint(StreamEvent):
    """Aviso de ferramenta demorada.

    Existe porque silêncio prolongado é indistinguível de travamento para
    quem está do outro lado de um chat.
    """

    tool_name: str
    duration: float


@dataclass(frozen=True)
class GatewayNotice(StreamEvent):
    kind: str
    text: str
    extra: dict[str, Any] = field(default_factory=dict)


STREAM_EVENTS: tuple[type[StreamEvent], ...] = (
    MessageChunk,
    MessageStop,
    Commentary,
    ToolCallChunk,
    ToolCallFinished,
    LongToolHint,
    GatewayNotice,
)
