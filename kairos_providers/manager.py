"""Facade somente-leitura para consumidores antigos de catálogo.

Novos consumidores usam ProviderGateway. Esta classe não resolve credenciais,
não seleciona provider e não inicia chamadas externas.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from kairos_providers.base import ModelDescriptor
from kairos_providers.composition import build_provider_gateway
from kairos_providers.contracts import CatalogModel
from kairos_providers.gateway import ProviderGateway

__all__ = ["ProviderManager"]


class ProviderManager:
    def __init__(
        self,
        *_args: object,
        gateway: ProviderGateway | None = None,
        home: Path | None = None,
        **_kwargs: object,
    ) -> None:
        self._gateway = gateway
        self._gateway_is_injected = gateway is not None
        self._home = home or Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))

    def list_all_models(self) -> list[ModelDescriptor]:
        return [_legacy_descriptor(model) for model in self._catalog_gateway.catalog.list_models()]

    async def test_all_connections(self):
        """Compatibilidade temporária; runtime Web não consome mais esta sonda."""
        if self._gateway_is_injected:
            return await self._catalog_gateway.test_all_connections()
        gateway = build_provider_gateway(self._home)
        try:
            return await gateway.test_all_connections()
        finally:
            await gateway.aclose()

    async def aclose(self) -> None:
        if not self._gateway_is_injected and self._gateway is not None:
            await self._gateway.aclose()
            self._gateway = None

    async def __aenter__(self) -> ProviderManager:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    @property
    def _catalog_gateway(self) -> ProviderGateway:
        if self._gateway is None:
            self._gateway = build_provider_gateway(self._home)
        return self._gateway


def _legacy_descriptor(model: CatalogModel) -> ModelDescriptor:
    capabilities = model.capabilities
    return ModelDescriptor(
        id=model.ref.model,
        name=model.display_name,
        provider=model.ref.provider,
        context_length=capabilities.context_length or 128_000,
        max_output_tokens=capabilities.max_output_tokens or 8_192,
        supports_tools=capabilities.tools is True,
        supports_vision=capabilities.vision is True,
        supports_streaming=capabilities.streaming is True,
        cost_input_per_million=_legacy_price(model.price.prompt),
        cost_output_per_million=_legacy_price(model.price.completion),
    )


def _legacy_price(value: Decimal | None) -> float:
    return float(value) if value is not None else 0.0
