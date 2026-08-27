"""Composição do catálogo, registry e cofre para adapters de providers."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from kairos_providers.adapter_contract import ProviderAdapter, ProviderError
from kairos_providers.base import ConnectionStatus
from kairos_providers.catalog import CatalogSnapshot, ModelCatalog, UnknownModelError
from kairos_providers.catalog_store import CatalogSnapshotStore
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelPrice,
    ProviderModelRef,
)
from kairos_providers.provider_registry import ProviderAdapterRegistry
from kairos_security.credentials import CredentialRef, CredentialService, VaultError


@dataclass(frozen=True)
class ProviderCatalogResult:
    models: tuple[CatalogModel, ...]
    source: CatalogOrigin


@dataclass(frozen=True)
class ProviderBillingMetadata:
    """Non-secret billing identity for one provider route."""

    provider: str
    base_url: str
    mode: str


class PreparedProviderAdapter:
    """Opaque, redacted adapter factory frozen for one interaction turn."""

    __slots__ = (
        "_adapter_kwargs",
        "_registry",
        "billing",
        "cost_source",
        "credential_id",
        "price",
        "pricing_version",
        "ref",
    )

    def __init__(
        self,
        *,
        registry: ProviderAdapterRegistry,
        ref: ProviderModelRef,
        adapter_kwargs: Mapping[str, Any],
        credential_id: str | None,
        billing: ProviderBillingMetadata,
        price: ModelPrice,
        cost_source: str | None,
        pricing_version: str | None = None,
    ) -> None:
        self._registry = registry
        self._adapter_kwargs = MappingProxyType(dict(adapter_kwargs))
        self.ref = ref
        self.credential_id = credential_id
        self.billing = billing
        self.price = price
        self.cost_source = cost_source
        self.pricing_version = pricing_version

    def create_adapter(self) -> ProviderAdapter:
        return self._registry.create(self.ref.provider, **dict(self._adapter_kwargs))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(ref={self.ref!r}, credential_id={self.credential_id!r}, "
            f"billing={self.billing!r}, adapter_config=<redacted>)"
        )


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
        billing_routes: Mapping[str, ProviderBillingMetadata] | None = None,
    ) -> None:
        self.registry = registry
        self.catalog = catalog
        self._credentials = credentials
        self._snapshots = snapshot_store
        self._clock = clock
        self._ttl = snapshot_ttl
        self._billing_routes = dict(billing_routes or {})

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

    def prepare(self, ref: ProviderModelRef) -> PreparedProviderAdapter:
        """Freeze credential, billing route, and price before the first attempt."""
        descriptor = self.registry.describe(ref.provider)
        credential_id: str | None = None
        auth_method = descriptor.auth_methods[0] if descriptor.auth_methods else "local"
        if descriptor.auth_methods:
            credentials = self._credentials.list(ref.provider)
            if credentials:
                metadata = credentials[0]
                credential_ref = metadata.ref
                if not isinstance(credential_ref, CredentialRef):
                    raise TypeError("referência de credencial inválida")
                credential_id = credential_ref.credential_id
                candidate_method = getattr(metadata, "auth_method", None)
                if isinstance(candidate_method, str) and candidate_method:
                    auth_method = candidate_method
                kwargs = self._credentials.get(credential_ref).reveal()
            else:
                kwargs = {}
        else:
            kwargs = {}
        kwargs["model"] = ref.model

        try:
            catalog_model = self.catalog.find(ref)
        except UnknownModelError:
            price = ModelPrice()
            cost_source = None
        else:
            price = catalog_model.price
            cost_source = (
                "catalog"
                if any(
                    value is not None for value in (price.prompt, price.completion, price.request)
                )
                else None
            )

        billing = self._billing_routes.get(
            ref.provider,
            ProviderBillingMetadata(ref.provider, "", auth_method),
        )
        if billing.mode == "credential":
            billing = ProviderBillingMetadata(billing.provider, billing.base_url, auth_method)
        return PreparedProviderAdapter(
            registry=self.registry,
            ref=ref,
            adapter_kwargs=kwargs,
            credential_id=credential_id,
            billing=billing,
            price=price,
            cost_source=cost_source,
        )

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
