"""Contratos abstratos e modelos para a camada universal de provedores LLM."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProviderType(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    DEEPSEEK = "deepseek"
    GROQ = "groq"
    CUSTOM = "custom"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class StreamChunk:
    delta_text: str = ""
    delta_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    raw_event: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelDescriptor:
    """Metadados legados retornados pelos adapters.

    Novos consumidores usam ``contracts.CatalogModel``; a conversão dos
    adapters será feita sem quebrar esta interface pública.
    """

    id: str
    name: str
    provider: str
    context_length: int = 128_000
    max_output_tokens: int = 8_192
    supports_tools: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True
    cost_input_per_million: float = 0.0
    cost_output_per_million: float = 0.0


@dataclass
class ConnectionStatus:
    ok: bool
    provider: str
    message: str
    models_found: int = 0
    auth_method: str = "api_key"
    details: dict[str, Any] = field(default_factory=dict)
    state: str = "available"


class BaseLLMProvider(ABC):
    """Interface universal para provedores de LLM."""

    def __init__(self, name: str, base_url: str, api_key: str | None = None, **kwargs: Any) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra = kwargs

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Gera streaming padronizado de texto e tool calls."""
        ...

    @abstractmethod
    async def test_connection(self) -> ConnectionStatus:
        """Verifica a conexão e validade das credenciais."""
        ...

    @abstractmethod
    def list_models(self) -> list[ModelDescriptor]:
        """Lista os modelos suportados pelo provedor."""
        ...
