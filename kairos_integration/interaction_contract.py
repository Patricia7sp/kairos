"""Contratos agnósticos de transporte para a rota de interação.

Este módulo é a fronteira entre as superfícies (Web, terminal e futuros
canais) e a execução de um turno.  Os tipos não conhecem FastAPI, CLI ou uma
implementação concreta de provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from kairos_providers.adapter_contract import (
    CanonicalToolCall,
    ProviderError,
    ProviderEvent,
)
from kairos_providers.base import TokenUsage
from kairos_providers.contracts import ProviderModelRef, SelectionReason

__all__ = [
    "InteractionEnvelope",
    "InteractionEvent",
    "InteractionEventKind",
    "InteractionResult",
    "InteractionSelectionSnapshot",
]


class InteractionEventKind(StrEnum):
    """Eventos canônicos emitidos durante um turno."""

    TURN_START = "turn_start"
    DELTA = "delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    USAGE = "usage"
    TURN_ERROR = "turn_error"
    TURN_END = "turn_end"


def _immutable_parameters(parameters: Mapping[str, Any]) -> Mapping[str, Any]:
    """Take a shallow immutable copy of caller-owned parameters."""

    if not isinstance(parameters, Mapping):
        raise TypeError("parameters deve ser um mapping")
    return MappingProxyType(dict(parameters))


@dataclass(frozen=True)
class InteractionEnvelope:
    """A normalized user turn, independent of its transport."""

    conversation_id: str
    source: str
    content: str
    profile: str | None = None
    activity: str | None = None
    override: ProviderModelRef | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id é obrigatório")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source é obrigatório")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content é obrigatório")
        if self.profile is not None and not isinstance(self.profile, str):
            raise TypeError("profile deve ser uma string ou None")
        if self.activity is not None and not isinstance(self.activity, str):
            raise TypeError("activity deve ser uma string ou None")
        object.__setattr__(self, "parameters", _immutable_parameters(self.parameters))


@dataclass(frozen=True)
class InteractionSelectionSnapshot:
    """The effective provider selection captured for one turn."""

    ref: ProviderModelRef
    reason: SelectionReason = SelectionReason.CONVERSATION_OVERRIDE
    parameters: Mapping[str, Any] = field(default_factory=dict)
    credential_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _immutable_parameters(self.parameters))


@dataclass(frozen=True)
class InteractionResult:
    """Immutable accumulated result of a completed or partial turn."""

    text: str = ""
    reasoning: str = ""
    tool_calls: tuple[CanonicalToolCall, ...] = ()
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    error: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class InteractionEvent:
    """A version-neutral event suitable for WebSocket and CLI translators."""

    kind: InteractionEventKind
    conversation_id: str | None = None
    snapshot: InteractionSelectionSnapshot | None = None
    text: str = ""
    reasoning: str = ""
    tool_call: CanonicalToolCall | None = None
    tool_result: Any = None
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    error: str | None = None
    error_kind: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, InteractionEventKind):
            try:
                object.__setattr__(self, "kind", InteractionEventKind(self.kind))
            except ValueError as exc:
                raise ValueError(f"evento de interação desconhecido: {self.kind!r}") from exc

    @classmethod
    def turn_start(
        cls,
        snapshot: InteractionSelectionSnapshot,
        conversation_id: str | None = None,
    ) -> InteractionEvent:
        return cls(
            kind=InteractionEventKind.TURN_START,
            conversation_id=conversation_id,
            snapshot=snapshot,
        )

    @classmethod
    def from_provider(cls, event: ProviderEvent) -> InteractionEvent | None:
        """Translate one provider event; finish is closed by the service.

        Provider ``finish`` is deliberately not emitted here: the service
        persists its accumulator first and then emits one canonical
        ``turn_end`` event.
        """

        kind = event.kind
        if kind in {"text_delta", "delta"}:
            return cls(kind=InteractionEventKind.DELTA, text=event.text)
        if kind in {"reasoning_delta", "reasoning"}:
            return cls(kind=InteractionEventKind.REASONING_DELTA, reasoning=event.reasoning)
        if kind == "tool_call":
            return cls(kind=InteractionEventKind.TOOL_CALL, tool_call=event.tool_call)
        if kind == "tool_result":
            return cls(
                kind=InteractionEventKind.TOOL_RESULT,
                tool_result=getattr(event, "tool_result", None),
            )
        if kind == "usage":
            return cls(kind=InteractionEventKind.USAGE, usage=event.usage)
        if kind == "finish":
            return None
        return None

    @classmethod
    def turn_error(cls, error: Exception) -> InteractionEvent:
        if isinstance(error, ProviderError):
            return cls(
                kind=InteractionEventKind.TURN_ERROR,
                error=error.message,
                error_kind=error.kind.value,
                retryable=error.retryable,
            )
        return cls(kind=InteractionEventKind.TURN_ERROR, error="falha ao executar interação")

    @classmethod
    def turn_end(cls, finish_reason: str | None = None) -> InteractionEvent:
        return cls(kind=InteractionEventKind.TURN_END, finish_reason=finish_reason)
