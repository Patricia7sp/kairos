"""Composição do catálogo, registry e cofre para adapters de providers."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from kairos_providers.adapter_contract import ProviderAdapter, ProviderError
from kairos_providers.base import ConnectionStatus
from kairos_providers.catalog import CatalogSnapshot, ModelCatalog
from kairos_providers.catalog_store import CatalogSnapshotStore
from kairos_providers.contracts import CatalogModel, CatalogOrigin, ProviderModelRef
from kairos_providers.provider_registry import ProviderAdapterRegistry
from kairos_security.credentials import CredentialRef, CredentialService, VaultError


@dataclass(frozen=True)
class ProviderCatalogResult:
    models: tuple[CatalogModel, ...]
    source: CatalogOrigin


class ProviderGateway:
    """Único consumidor de factories, resolvendo segredos apenas ao criar adapters."""

    def __init__(
        self,
        registry: ProviderAdapterRegistry,
        catalog: ModelCatalog,
        credentials: CredentialService,
        snapshot_store: CatalogSnapshotStore,
        *,
        clock: Callable[[], float] = time.time,
        snapshot_ttl: float = 3600.0,
    ) -> None:
        self.registry = registry
        self.catalog = catalog
        self._credentials = credentials
        self._snapshots = snapshot_store
        self._clock = clock
        self._ttl = snapshot_ttl

    async def refresh(self, provider: str) -> ProviderCatalogResult:
        adapter = self._adapter_for_discovery(provider)
        try:
            models = tuple(await adapter.discover_models())
        except ProviderError:
            return self._load_cached(provider)

        if any(model.ref.provider != provider for model in models):
            raise ValueError("descoberta retornou modelo de outro provider")

        now = self._clock()
        snapshot = CatalogSnapshot(models, now, now + self._ttl)
        self._snapshots.save(provider, snapshot)
        self.catalog.merge(models, origin=CatalogOrigin.DYNAMIC)
        return ProviderCatalogResult(models, CatalogOrigin.DYNAMIC)

    def create_adapter(self, ref: ProviderModelRef) -> ProviderAdapter:
        return self._create_adapter(ref.provider, model=ref.model)

    async def test_connection(self, provider: str) -> ConnectionStatus:
        """Testa somente o provider pedido, sem escolher um substituto."""
        return await self._test_connection(provider)

    async def _test_connection(
        self, provider: str, *, credential_values: Mapping[str, Any] | None = None
    ) -> ConnectionStatus:
        descriptor = self.registry.describe(provider)
        if credential_values is None:
            try:
                credential_values = self._credential_values(provider)
            except VaultError:
                credential_values = {}
        has_credentials = bool(credential_values)
        if descriptor.auth_methods and not has_credentials:
            return ConnectionStatus(
                False,
                provider,
                "credencial inválida ou ausente",
                0,
                auth_method=descriptor.auth_methods[0],
                state="unavailable",
            )
        return await self._adapter_for_discovery(
            provider, credential_values=credential_values
        ).test_connection()

    async def test_all_connections(
        self,
        *,
        credential_resolver: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> dict[str, ConnectionStatus]:
        """Expõe o estado de cada provider registrado para a ponte legada."""
        return {
            descriptor.id: await self._test_connection(
                descriptor.id,
                credential_values=(
                    dict(credential_resolver(descriptor.id))
                    if credential_resolver is not None
                    else None
                ),
            )
            for descriptor in self.registry.list_descriptors()
        }

    async def aclose(self) -> None:
        """Fecha recursos próprios; subclasses compostas fecham seus clientes."""

    async def __aenter__(self) -> ProviderGateway:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    def _adapter_for_discovery(
        self, provider: str, *, credential_values: Mapping[str, Any] | None = None
    ) -> ProviderAdapter:
        return self._create_adapter(provider, credential_values=credential_values)

    def _create_adapter(
        self,
        provider: str,
        *,
        model: str | None = None,
        credential_values: Mapping[str, Any] | None = None,
    ) -> ProviderAdapter:
        if credential_values is not None:
            kwargs = dict(credential_values)
        elif self.registry.describe(provider).auth_methods:
            kwargs = self._credential_values(provider)
        else:
            kwargs = {}
        if model is not None:
            kwargs["model"] = model
        return self.registry.create(provider, **kwargs)

    def _credential_values(self, provider: str) -> dict[str, Any]:
        credentials = self._credentials.list(provider)
        if not credentials:
            return {}
        ref = credentials[0].ref
        if not isinstance(ref, CredentialRef):
            raise TypeError("referência de credencial inválida")
        return self._credentials.get(ref).reveal()

    def _load_cached(self, provider: str) -> ProviderCatalogResult:
        snapshot = self._snapshots.load(provider)
        if snapshot is None or not snapshot.is_valid(self._clock()):
            return ProviderCatalogResult((), CatalogOrigin.CACHE)
        self.catalog.load_snapshot(snapshot)
        return ProviderCatalogResult(snapshot.models, CatalogOrigin.CACHE)
