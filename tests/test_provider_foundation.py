"""Contratos da fundação de providers e modelos."""

from __future__ import annotations

import dataclasses
import unittest

from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelStability,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
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


if __name__ == "__main__":
    unittest.main()
