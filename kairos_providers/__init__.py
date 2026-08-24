"""Perfis de provedor — declarativos, imutáveis em runtime.

`_reversa_sdd/providers-gateway/` §1 (Tarefa 11).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "OMIT_TEMPERATURE",
    "DiscoveryLayer",
    "ProviderProfile",
    "ProviderRegistry",
    "build_request_kwargs",
    "user_agent",
]

KAIROS_VERSION: Final = "0.1.0"


class _OmitTemperature:
    """Sentinela para `default_temperature`.

    `None` **não serve**: `temperature=None` é valor legítimo para alguns
    provedores, e um `if temperature is None` não distinguiria "não definido"
    de "explicitamente nulo". Modelos de raciocínio (`o1`, `o3`) **rejeitam** o
    parâmetro, então a chave precisa sumir do corpo — não ir com valor.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "OMIT_TEMPERATURE"

    def __bool__(self) -> bool:
        return False


OMIT_TEMPERATURE: Final = _OmitTemperature()


class DiscoveryLayer(StrEnum):
    """Precedência de descoberta — **last-writer-wins**, nesta ordem."""

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
    """`User-Agent` obrigatório em toda requisição.

    Não é telemetria: WAFs de provedor bloqueiam com **HTTP 403** o
    User-Agent padrão do `urllib`/`requests`. Sem isto o provedor parece fora
    do ar, e o erro não diz nada sobre o motivo real.
    """
    return f"kairos/{KAIROS_VERSION}"


def build_request_kwargs(profile: ProviderProfile, **overrides: Any) -> dict[str, Any]:
    """Monta os kwargs da requisição, **removendo** o que deve ser omitido."""
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
    """Registro com precedência por camada.

    **Last-writer-wins dentro da mesma camada; camada mais alta sempre vence.**
    Sem a ordem por camada, a precedência dependeria da ordem de importação —
    que muda com o sistema de arquivos e é impossível de depurar.
    """

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
        """Prefixo dinâmico de namespace para módulo de usuário.

        Dois perfis com um plugin de mesmo nome colidiriam na tabela de
        módulos do interpretador, e o segundo silenciosamente reusaria o
        código do primeiro.
        """
        return f"_kairos_user_provider_{plugin}"
