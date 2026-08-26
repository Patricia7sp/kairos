"""Tipos canônicos compartilhados por catálogo, seleção e adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class ModelKind(StrEnum):
    MODEL = "model"
    REMOTE_AGENT = "remote_agent"


class ModelStability(StrEnum):
    STABLE = "stable"
    PREVIEW = "preview"
    DEPRECATED = "deprecated"


class CatalogOrigin(StrEnum):
    CURATED = "curated"
    DYNAMIC = "dynamic"
    CACHE = "cache"


class SelectionScope(StrEnum):
    MESSAGE = "message"
    CONVERSATION = "conversation"
    ACTIVITY = "activity"
    PROFILE = "profile"
    GLOBAL = "global"


class SelectionReason(StrEnum):
    MESSAGE_OVERRIDE = "message_override"
    CONVERSATION_OVERRIDE = "conversation_override"
    ACTIVITY_RULE = "activity_rule"
    PROFILE_DEFAULT = "profile_default"
    GLOBAL_DEFAULT = "global_default"


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    display_name: str
    auth_methods: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("provider id é obrigatório")
        if not self.display_name.strip():
            raise ValueError("provider display_name é obrigatório")


@dataclass(frozen=True)
class ProviderModelRef:
    provider: str
    model: str
    kind: ModelKind = ModelKind.MODEL

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider é obrigatório")
        if not self.model.strip():
            raise ValueError("model é obrigatório")


@dataclass(frozen=True)
class ModelCapabilities:
    chat: bool | None = None
    tools: bool | None = None
    vision: bool | None = None
    streaming: bool | None = None
    context_length: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ModelPrice:
    """Preço normalizado de prompt, completion ou requisição.

    ``None`` significa que a fonte não informou aquele componente de preço.
    """

    prompt: Decimal | None = None
    completion: Decimal | None = None
    request: Decimal | None = None


@dataclass(frozen=True)
class CatalogModel:
    ref: ProviderModelRef
    display_name: str
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    stability: ModelStability = ModelStability.STABLE
    origins: frozenset[CatalogOrigin] = frozenset()
    price: ModelPrice = field(default_factory=ModelPrice)

    def is_selectable(self, *, include_preview: bool = False) -> bool:
        if self.capabilities.chat is not True or self.stability is ModelStability.DEPRECATED:
            return False
        return include_preview or self.stability is not ModelStability.PREVIEW

    @property
    def is_free(self) -> bool:
        """Indica modelos explicitamente gratuitos ou com preço integralmente zero."""
        if self.ref.model.endswith(":free"):
            return True
        prices = (self.price.prompt, self.price.completion, self.price.request)
        return any(price is not None for price in prices) and all(
            price in (None, Decimal("0")) for price in prices
        )


@dataclass(frozen=True)
class ResolvedModelSelection:
    ref: ProviderModelRef
    reason: SelectionReason
