"""Composição do catálogo, registry e cofre para adapters de providers."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kairos_providers.adapter_contract import ProviderAdapter, ProviderError
from kairos_providers.catalog import CatalogSnapshot, ModelCatalog
from kairos_providers.catalog_store import CatalogSnapshotStore
from kairos_providers.contracts import CatalogModel, CatalogOrigin, ProviderModelRef
from kairos_providers.provider_registry import ProviderAdapterRegistry
from kairos_security.credentials import CredentialRef, CredentialService


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

        now = self._clock()
        snapshot = CatalogSnapshot(models, now, now + self._ttl)
        self._snapshots.save(provider, snapshot)
        self.catalog.merge(models, origin=CatalogOrigin.DYNAMIC)
        return ProviderCatalogResult(models, CatalogOrigin.DYNAMIC)

    def create_adapter(self, ref: ProviderModelRef) -> ProviderAdapter:
        return self._create_adapter(ref.provider, model=ref.model)

    def _adapter_for_discovery(self, provider: str) -> ProviderAdapter:
        return self._create_adapter(provider)

    def _create_adapter(self, provider: str, *, model: str | None = None) -> ProviderAdapter:
        kwargs = self._credential_values(provider)
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
