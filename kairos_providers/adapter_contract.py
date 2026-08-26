"""Contrato canônico entre o gateway e adapters de providers.

Adapters recebem somente mensagens normalizadas e nunca devolvem payloads do
upstream. Isto mantém os detalhes de protocolos externos fora do gateway e
impede que credenciais de respostas de erro atravessem a fronteira pública.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from kairos_providers.base import ConnectionStatus, TokenUsage
from kairos_providers.contracts import CatalogModel, ProviderDescriptor, ProviderModelRef

__all__ = [
    "AdapterRequest",
    "CanonicalMessage",
    "CanonicalToolCall",
    "ContentPart",
    "ProviderAdapter",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderEvent",
]


class ProviderErrorKind(StrEnum):
    AUTH = "auth"
    MODEL = "model"
    LIMIT = "limit"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    INCOMPATIBLE = "incompatible"
    INTERNAL = "internal"


_DEFAULT_ERROR_MESSAGES: Mapping[ProviderErrorKind, str] = {
    ProviderErrorKind.AUTH: "credencial inválida ou ausente",
    ProviderErrorKind.MODEL: "modelo indisponível",
    ProviderErrorKind.LIMIT: "limite do provedor atingido",
    ProviderErrorKind.RATE_LIMIT: "limite de requisições atingido",
    ProviderErrorKind.NETWORK: "falha de rede ao acessar o provedor",
    ProviderErrorKind.INCOMPATIBLE: "requisição incompatível com o provedor",
    ProviderErrorKind.INTERNAL: "falha interna do provedor",
}
class ProviderError(Exception):
    """Falha normalizada e segura para logs e superfícies de cliente."""

    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str | None = None,
        *,
        retryable: bool,
    ) -> None:
        # Mantém a assinatura pública, mas texto de provider pode conter um
        # segredo opaco que não é possível identificar de forma confiável.
        del message
        self.kind = kind
        self.retryable = retryable
        self.message = _DEFAULT_ERROR_MESSAGES[kind]
        super().__init__(self.message)

    @classmethod
    def from_upstream(
        cls,
        kind: ProviderErrorKind,
        _payload: object,
        *,
        retryable: bool,
    ) -> ProviderError:
        """Descarta payloads externos, que podem conter headers ou credenciais."""
        return cls(kind, retryable=retryable)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(kind={self.kind!r}, message={self.message!r}, "
            f"retryable={self.retryable!r})"
        )


@dataclass(frozen=True)
class ContentPart:
    kind: str
    value: Any


@dataclass(frozen=True)
class CanonicalToolCall:
    id: str
    name: str
    arguments: str = ""


@dataclass(frozen=True)
class CanonicalMessage:
    role: str
    content: tuple[ContentPart, ...]
    tool_call_id: str | None = None
    tool_calls: tuple[CanonicalToolCall, ...] = ()


@dataclass(frozen=True)
class AdapterRequest:
    model: ProviderModelRef
    messages: tuple[CanonicalMessage, ...]
    tools: tuple[dict[str, Any], ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderEvent:
    kind: str
    text: str = ""
    reasoning: str = ""
    tool_call: CanonicalToolCall | None = None
    usage: TokenUsage | None = None
    finish_reason: str | None = None


class ProviderAdapter(Protocol):
    descriptor: ProviderDescriptor

    async def discover_models(self) -> tuple[CatalogModel, ...]: ...

    async def test_connection(self) -> ConnectionStatus: ...

    def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]: ...
