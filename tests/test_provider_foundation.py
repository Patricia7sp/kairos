"""Contratos da fundação de providers e modelos."""

from __future__ import annotations

import dataclasses
import unittest

from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelStability,
    ProviderDescriptor,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
)
from kairos_providers.provider_registry import (
    ProviderAdapterRegistry,
    UnknownProviderError,
)


class ProviderContractTests(unittest.TestCase):
    def test_provider_model_ref_exige_provider(self):
        with self.assertRaisesRegex(ValueError, "provider"):
            ProviderModelRef(provider="", model="gpt-5.6-terra")

    def test_provider_model_ref_exige_modelo(self):
        with self.assertRaisesRegex(ValueError, "model"):
            ProviderModelRef(provider="openai", model="")

    def test_modelo_estavel_de_chat_e_selecionavel(self):
        model = CatalogModel(
            ref=ProviderModelRef("openai", "gpt-5.6-terra"),
            display_name="GPT 5.6 Terra",
            capabilities=ModelCapabilities(chat=True, tools=True),
            stability=ModelStability.STABLE,
            origins=frozenset({CatalogOrigin.CURATED}),
        )

        self.assertTrue(model.is_selectable(include_preview=False))

    def test_preview_so_e_selecionavel_quando_solicitado(self):
        stable = CatalogModel(
            ref=ProviderModelRef("openai", "gpt-5.6-terra"),
            display_name="GPT 5.6 Terra",
            capabilities=ModelCapabilities(chat=True, tools=True),
            stability=ModelStability.STABLE,
            origins=frozenset({CatalogOrigin.CURATED}),
        )
        preview = dataclasses.replace(stable, stability=ModelStability.PREVIEW)

        self.assertFalse(preview.is_selectable(include_preview=False))
        self.assertTrue(preview.is_selectable(include_preview=True))

    def test_modelo_sem_chat_nao_e_selecionavel(self):
        model = CatalogModel(
            ref=ProviderModelRef("example", "embedding-only"),
            display_name="Embedding",
            capabilities=ModelCapabilities(chat=False),
            stability=ModelStability.STABLE,
            origins=frozenset({CatalogOrigin.DYNAMIC}),
        )

        self.assertFalse(model.is_selectable(include_preview=True))

    def test_resolved_selection_carrega_razao(self):
        resolved = ResolvedModelSelection(
            ref=ProviderModelRef("openai", "gpt-5.6-terra"),
            reason=SelectionReason.CONVERSATION_OVERRIDE,
        )

        self.assertEqual(resolved.reason.value, "conversation_override")


class ProviderAdapterRegistryTests(unittest.TestCase):
    def test_registry_descreve_e_cria_adapter_sem_armazenar_credencial(self):
        registry = ProviderAdapterRegistry()
        descriptor = ProviderDescriptor(id="fake", display_name="Fake")
        registry.register(descriptor, lambda **kwargs: {"model": kwargs["model"]})

        self.assertEqual(registry.describe("fake"), descriptor)
        self.assertEqual(registry.create("fake", model="m"), {"model": "m"})

    def test_provider_desconhecido_falha_sem_fallback(self):
        with self.assertRaisesRegex(UnknownProviderError, "missing"):
            ProviderAdapterRegistry().create("missing")

    def test_listagem_e_deterministica(self):
        registry = ProviderAdapterRegistry()
        registry.register(ProviderDescriptor(id="z", display_name="Z"), lambda **_: object())
        registry.register(ProviderDescriptor(id="a", display_name="A"), lambda **_: object())

        self.assertEqual([provider.id for provider in registry.list_descriptors()], ["a", "z"])


if __name__ == "__main__":
    unittest.main()
