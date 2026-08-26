"""Ponte temporária entre os consumidores legados e o ProviderGateway."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from kairos_providers.base import BaseLLMProvider, ConnectionStatus, ModelDescriptor
from kairos_providers.composition import (
    build_legacy_provider,
    build_provider_gateway,
    get_google_adc_token,
    resolve_legacy_api_key,
)
from kairos_providers.contracts import CatalogModel
from kairos_providers.gateway import ProviderGateway

__all__ = ["ProviderManager", "get_google_adc_token"]


class ProviderManager:
    """Mantém a API legada, delegando catálogo e conexões ao gateway moderno."""

    def __init__(
        self,
        auth_store: dict[str, Any] | None = None,
        secret_resolver: Callable[[str, str], Mapping[str, str] | Any] | None = None,
        *,
        gateway: ProviderGateway | None = None,
        home: Path | None = None,
    ) -> None:
        self.auth_store = auth_store or {}
        self.secret_resolver = secret_resolver
        self._gateway = gateway
        self._gateway_is_injected = gateway is not None
        self._home = home or Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))

    def get_api_key(self, provider: str) -> str | None:
        return resolve_legacy_api_key(provider, self.auth_store, self.secret_resolver)

    def get_provider(self, provider_name: str, **kwargs: Any) -> BaseLLMProvider:
        """Retorna o adapter antigo até Chat e CLI migrarem para o gateway."""
        return build_legacy_provider(provider_name, api_key=self.get_api_key(provider_name), **kwargs)

    def list_all_models(self) -> list[ModelDescriptor]:
        """Converte ``CatalogModel`` na fronteira exigida por clientes legados."""
        return [_legacy_descriptor(model) for model in self._gateway_for_legacy_calls.catalog.list_models()]

    async def test_all_connections(self) -> dict[str, ConnectionStatus]:
        if self._gateway_is_injected:
            return await self._gateway_for_legacy_calls.test_all_connections(
                credential_resolver=self._legacy_credential_values
            )

        gateway = build_provider_gateway(self._home)
        try:
            return await gateway.test_all_connections(
                credential_resolver=self._legacy_credential_values
            )
        finally:
            await gateway.aclose()

    async def aclose(self) -> None:
        """Fecha o gateway criado pelo manager, preservando gateways injetados."""
        if not self._gateway_is_injected and self._gateway is not None:
            await self._gateway.aclose()
            self._gateway = None

    async def __aenter__(self) -> ProviderManager:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    @property
    def _gateway_for_legacy_calls(self) -> ProviderGateway:
        if self._gateway is None:
            self._gateway = build_provider_gateway(self._home)
        return self._gateway

    def _legacy_credential_values(self, provider: str) -> dict[str, str]:
        api_key = self.get_api_key(provider)
        return {"api_key": api_key} if api_key else {}


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
