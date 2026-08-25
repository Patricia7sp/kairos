"""Perfis de provedor — declarativos, imutáveis em runtime.

`_reversa_sdd/providers-gateway/` §1 (Tarefa 11).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from kairos_providers.base import (
    BaseLLMProvider,
    ConnectionStatus,
    ModelDescriptor,
    ProviderType,
    StreamChunk,
    TokenUsage,
)
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelKind,
    ModelStability,
    ProviderDescriptor,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
    SelectionScope,
)
from kairos_providers.manager import ProviderManager, get_google_adc_token
from kairos_providers.provider_registry import (
    ProviderAdapterRegistry,
    RegisteredProvider,
    UnknownProviderError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "OMIT_TEMPERATURE",
    "BaseLLMProvider",
    "CatalogModel",
    "CatalogOrigin",
    "ConnectionStatus",
    "DiscoveryLayer",
    "ModelCapabilities",
    "ModelDescriptor",
    "ModelKind",
    "ModelStability",
    "ProviderAdapterRegistry",
    "ProviderDescriptor",
    "ProviderManager",
    "ProviderModelRef",
    "ProviderProfile",
    "ProviderRegistry",
    "ProviderType",
    "RegisteredProvider",
    "ResolvedModelSelection",
    "SelectionReason",
    "SelectionScope",
    "StreamChunk",
    "TokenUsage",
    "UnknownProviderError",
    "build_request_kwargs",
    "get_google_adc_token",
    "user_agent",
]

KAIROS_VERSION: Final = "0.1.0"


class _OmitTemperature:
    __slots__ = ()

    def __repr__(self) -> str:
        return "OMIT_TEMPERATURE"

    def __bool__(self) -> bool:
        return False


OMIT_TEMPERATURE: Final = _OmitTemperature()


class DiscoveryLayer(StrEnum):
    BUILTIN = "builtin"
    BUNDLED_PLUGIN = "bundled_plugin"
    USER_PLUGIN = "user_plugin"
    RUNTIME = "runtime"


_LAYER_ORDER: Final = tuple(DiscoveryLayer)


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    display_name: str
    base_url: str
    default_model: str
    env_api_key: str | None = None
    models_url: str | None = None
    requires_api_key: bool = True
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    default_temperature: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    request_timeout: float = 120.0
    layer: DiscoveryLayer = DiscoveryLayer.BUILTIN


def user_agent() -> str:
    return f"kairos/{KAIROS_VERSION}"


def build_request_kwargs(profile: ProviderProfile, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model": profile.default_model, **overrides}

    temperatura = overrides.get("temperature", profile.default_temperature)
    if temperatura is OMIT_TEMPERATURE:
        kwargs.pop("temperature", None)
    elif "temperature" not in overrides and profile.default_temperature is not None:
        kwargs["temperature"] = profile.default_temperature

    headers = {**profile.headers, "User-Agent": user_agent()}
    kwargs["headers"] = headers
    kwargs.setdefault("timeout", profile.request_timeout)
    return kwargs


class ProviderRegistry:
    def __init__(self) -> None:
        self._profiles: dict[str, ProviderProfile] = {}

    def register(self, profile: ProviderProfile) -> ProviderProfile:
        atual = self._profiles.get(profile.name)
        if atual is not None and _LAYER_ORDER.index(profile.layer) < _LAYER_ORDER.index(
            atual.layer
        ):
            logger.warning(
                "perfil %r da camada %s ignorado: já registrado por %s (mais alta)",
                profile.name,
                profile.layer,
                atual.layer,
            )
            return atual
        self._profiles[profile.name] = profile
        return profile

    def get(self, name: str) -> ProviderProfile | None:
        return self._profiles.get(name)

    def list_providers(self) -> list[str]:
        return sorted(self._profiles)

    @staticmethod
    def user_module_name(plugin: str) -> str:
        return f"_kairos_user_provider_{plugin}"
