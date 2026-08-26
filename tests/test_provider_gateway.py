"""Persistência de snapshots e composição segura do gateway de providers."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from kairos_providers.adapter_contract import ProviderError, ProviderErrorKind
from kairos_providers.catalog import CatalogSnapshot, ModelCatalog
from kairos_providers.catalog_store import CatalogSnapshotStore
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelPrice,
    ProviderDescriptor,
    ProviderModelRef,
)
from kairos_providers.gateway import ProviderGateway
from kairos_providers.provider_registry import ProviderAdapterRegistry
from kairos_security.credentials import CredentialRef, CredentialSecret


def snapshot(provider: str, model: str) -> CatalogSnapshot:
    return CatalogSnapshot(
        models=(
            CatalogModel(
                ref=ProviderModelRef(provider, model),
                display_name=model,
                capabilities=ModelCapabilities(chat=True),
                price=ModelPrice(prompt=Decimal("1.25"), completion=Decimal("2.50")),
            ),
        ),
        fetched_at=100.0,
        expires_at=200.0,
    )


class FakeCredentials:
    def __init__(self, secret: str) -> None:
        self._ref = CredentialRef("openai", "primary")
        self._secret = CredentialSecret({"api_key": secret})

    def list(self, provider: str):
        return [type("Metadata", (), {"ref": self._ref})()] if provider == "openai" else []

    def get(self, ref: CredentialRef) -> CredentialSecret:
        assert ref == self._ref
        return self._secret


class FailingDiscoveryAdapter:
    descriptor = ProviderDescriptor("openai", "OpenAI")

    async def discover_models(self):
        raise ProviderError(ProviderErrorKind.NETWORK, retryable=True)


class DiscoveringAdapter:
    descriptor = ProviderDescriptor("openai", "OpenAI")

    async def discover_models(self):
        return snapshot("openai", "gpt-new").models


class MismatchedDiscoveryAdapter:
    descriptor = ProviderDescriptor("openai", "OpenAI")

    async def discover_models(self):
        return snapshot("anthropic", "claude-wrong-provider").models


def gateway_with_adapter(adapter, store: CatalogSnapshotStore) -> ProviderGateway:
    registry = ProviderAdapterRegistry()
    registry.register(adapter.descriptor, lambda **_kwargs: adapter)
    return ProviderGateway(
        registry,
        ModelCatalog(clock=lambda: 150.0),
        FakeCredentials("secret-value"),
        store,
        clock=lambda: 150.0,
    )


class CatalogSnapshotStoreTests(unittest.TestCase):
    def test_salva_carrega_snapshot_com_preco_decimal_e_modo_restrito(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model-catalog.json"
            store = CatalogSnapshotStore(path)

            store.save("openai", snapshot("openai", "gpt-ok"))

            loaded = store.load("openai")
            assert loaded is not None
            self.assertEqual(loaded.models[0].price.prompt, Decimal("1.25"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn('"1.25"', path.read_text(encoding="utf-8"))

    def test_snapshot_preserva_metadados_publicos_openrouter(self):
        """Reabrir o cache não pode apagar capacidades específicas do modelo descoberto."""
        model = CatalogModel(
            ref=ProviderModelRef("openrouter", "acme/chat:free"),
            display_name="Acme Free",
            capabilities=ModelCapabilities(chat=True, tools=True, context_length=64_000),
            price=ModelPrice(prompt=Decimal("0"), completion=Decimal("0"), request=Decimal("0")),
            supported_parameters=frozenset({"temperature", "tools"}),
            input_modalities=frozenset({"text", "image"}),
            output_modalities=frozenset({"text"}),
            expiration_date="2026-12-31",
        )
        snapshot_with_metadata = CatalogSnapshot((model,), fetched_at=100.0, expires_at=200.0)
        with TemporaryDirectory() as tmpdir:
            store = CatalogSnapshotStore(Path(tmpdir) / "model-catalog.json")
            store.save("openrouter", snapshot_with_metadata)
            loaded = store.load("openrouter")

        assert loaded is not None
        self.assertEqual(loaded.models, snapshot_with_metadata.models)

    def test_reescrita_remove_campos_inesperados_do_snapshot(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model-catalog.json"
            store = CatalogSnapshotStore(path)
            store.save("openai", snapshot("openai", "gpt-ok"))
            document = json.loads(path.read_text(encoding="utf-8"))
            marker = "sentinel-unexpected-field"
            document["snapshots"]["openai"]["unexpected"] = marker
            path.write_text(json.dumps(document), encoding="utf-8")

            store.save("anthropic", snapshot("anthropic", "claude-ok"))

            self.assertNotIn(marker, path.read_text(encoding="utf-8"))

    def test_ignora_snapshot_que_nao_corresponde_ao_provider_solicitado(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model-catalog.json"
            store = CatalogSnapshotStore(path)
            store.save("openai", snapshot("openai", "gpt-ok"))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["snapshots"]["openai"]["models"][0]["ref"]["provider"] = "anthropic"
            path.write_text(json.dumps(document), encoding="utf-8")

            self.assertIsNone(store.load("openai"))

    def test_rejeita_snapshot_com_modelo_de_outro_provider_antes_de_gravar(self):
        with TemporaryDirectory() as tmpdir:
            store = CatalogSnapshotStore(Path(tmpdir) / "model-catalog.json")

            with self.assertRaisesRegex(ValueError, "provider"):
                store.save("openai", snapshot("anthropic", "claude-wrong-provider"))

            self.assertIsNone(store.load("openai"))


class ProviderGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_falha_mantem_snapshot_cacheado(self):
        with TemporaryDirectory() as tmpdir:
            store = CatalogSnapshotStore(Path(tmpdir) / "model-catalog.json")
            store.save("openai", snapshot("openai", "gpt-ok"))
            gateway = gateway_with_adapter(FailingDiscoveryAdapter(), store)

            result = await gateway.refresh("openai")

            self.assertIs(result.source, CatalogOrigin.CACHE)
            self.assertEqual([model.ref.model for model in result.models], ["gpt-ok"])

    async def test_refresh_descoberto_persiste_mescla_e_retorna_origem_dinamica(self):
        with TemporaryDirectory() as tmpdir:
            store = CatalogSnapshotStore(Path(tmpdir) / "model-catalog.json")
            gateway = gateway_with_adapter(DiscoveringAdapter(), store)

            result = await gateway.refresh("openai")

            self.assertIs(result.source, CatalogOrigin.DYNAMIC)
            self.assertEqual(
                gateway.catalog.find(ProviderModelRef("openai", "gpt-new")).ref,
                result.models[0].ref,
            )
            self.assertEqual(store.load("openai").models, result.models)  # type: ignore[union-attr]

    async def test_refresh_rejeita_modelo_de_outro_provider_sem_persistir_ou_mesclar(self):
        with TemporaryDirectory() as tmpdir:
            store = CatalogSnapshotStore(Path(tmpdir) / "model-catalog.json")
            gateway = gateway_with_adapter(MismatchedDiscoveryAdapter(), store)

            with self.assertRaisesRegex(ValueError, "provider"):
                await gateway.refresh("openai")

            self.assertIsNone(store.load("openai"))
            with self.assertRaisesRegex(LookupError, "claude-wrong-provider"):
                gateway.catalog.find(ProviderModelRef("anthropic", "claude-wrong-provider"))

    def test_create_adapter_resolve_segredo_sem_guardar_no_registry(self):
        created: list[dict[str, str]] = []
        registry = ProviderAdapterRegistry()
        descriptor = ProviderDescriptor("openai", "OpenAI", ("api_key",))

        def factory(**kwargs):
            created.append(kwargs)
            return DiscoveringAdapter()

        registry.register(descriptor, factory)
        gateway = ProviderGateway(
            registry,
            ModelCatalog(),
            FakeCredentials("secret-value"),
            CatalogSnapshotStore(Path("/tmp") / "unused-model-catalog.json"),
        )

        adapter = gateway.create_adapter(ProviderModelRef("openai", "gpt-ok"))

        self.assertEqual(adapter.descriptor.id, "openai")
        self.assertEqual(created, [{"api_key": "secret-value", "model": "gpt-ok"}])
        self.assertNotIn("secret-value", repr(gateway.registry))


if __name__ == "__main__":
    unittest.main()
