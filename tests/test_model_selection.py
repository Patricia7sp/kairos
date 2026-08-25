"""Precedência e falhas da seleção de provider/modelo."""

from __future__ import annotations

import unittest

from kairos_providers.catalog import ModelCatalog
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelStability,
    ProviderModelRef,
    SelectionReason,
)
from kairos_providers.selection import (
    ModelSelectionContext,
    ModelSelectionResolver,
    ModelSelectionUnavailableError,
)


def catalog_with(*refs: ProviderModelRef) -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.merge(
        [
            CatalogModel(
                ref=ref,
                display_name=ref.model,
                capabilities=ModelCapabilities(chat=True),
                origins=frozenset({CatalogOrigin.CURATED}),
            )
            for ref in refs
        ],
        origin=CatalogOrigin.CURATED,
    )
    return catalog


class ModelSelectionResolverTests(unittest.TestCase):
    def setUp(self):
        self.refs = {
            name: ProviderModelRef("p", name)
            for name in ("message", "conversation", "activity", "profile", "global")
        }
        self.resolver = ModelSelectionResolver(catalog_with(*self.refs.values()))

    def test_mensagem_vence_todas_as_demais_camadas(self):
        result = self.resolver.resolve(
            ModelSelectionContext(
                message=self.refs["message"],
                conversation=self.refs["conversation"],
                activity=self.refs["activity"],
                profile=self.refs["profile"],
                global_default=self.refs["global"],
            )
        )

        self.assertEqual(result.ref, self.refs["message"])
        self.assertEqual(result.reason, SelectionReason.MESSAGE_OVERRIDE)

    def test_cada_camada_e_usada_quando_as_superiores_estao_ausentes(self):
        cases = (
            (
                ModelSelectionContext(conversation=self.refs["conversation"]),
                self.refs["conversation"],
                SelectionReason.CONVERSATION_OVERRIDE,
            ),
            (
                ModelSelectionContext(activity=self.refs["activity"]),
                self.refs["activity"],
                SelectionReason.ACTIVITY_RULE,
            ),
            (
                ModelSelectionContext(profile=self.refs["profile"]),
                self.refs["profile"],
                SelectionReason.PROFILE_DEFAULT,
            ),
            (
                ModelSelectionContext(global_default=self.refs["global"]),
                self.refs["global"],
                SelectionReason.GLOBAL_DEFAULT,
            ),
        )
        for context, expected_ref, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = self.resolver.resolve(context)
                self.assertEqual(result.ref, expected_ref)
                self.assertEqual(result.reason, expected_reason)

    def test_modelo_ausente_falha_sem_trocar_provider(self):
        missing = ProviderModelRef("paid", "missing")

        with self.assertRaisesRegex(ModelSelectionUnavailableError, "paid/missing"):
            self.resolver.resolve(
                ModelSelectionContext(message=missing, global_default=self.refs["global"])
            )

    def test_contexto_sem_nenhuma_selecao_falha_explicitamente(self):
        with self.assertRaisesRegex(ModelSelectionUnavailableError, "nenhuma seleção configurada"):
            self.resolver.resolve(ModelSelectionContext())

    def test_modelo_depreciado_e_indisponivel(self):
        ref = ProviderModelRef("p", "deprecated")
        catalog = catalog_with(ref)
        catalog.merge(
            [
                CatalogModel(
                    ref=ref,
                    display_name="Deprecated",
                    capabilities=ModelCapabilities(chat=True),
                    stability=ModelStability.DEPRECATED,
                )
            ],
            origin=CatalogOrigin.DYNAMIC,
        )

        with self.assertRaisesRegex(ModelSelectionUnavailableError, "p/deprecated"):
            ModelSelectionResolver(catalog).resolve(ModelSelectionContext(message=ref))

    def test_modelo_sem_chat_e_indisponivel(self):
        ref = ProviderModelRef("p", "embedding")
        catalog = ModelCatalog()
        catalog.merge(
            [
                CatalogModel(
                    ref=ref,
                    display_name="Embedding",
                    capabilities=ModelCapabilities(chat=False),
                )
            ],
            origin=CatalogOrigin.DYNAMIC,
        )

        with self.assertRaisesRegex(ModelSelectionUnavailableError, "p/embedding"):
            ModelSelectionResolver(catalog).resolve(ModelSelectionContext(message=ref))

    def test_preview_exige_habilitacao_explicita(self):
        ref = ProviderModelRef("p", "preview")
        catalog = ModelCatalog()
        catalog.merge(
            [
                CatalogModel(
                    ref=ref,
                    display_name="Preview",
                    capabilities=ModelCapabilities(chat=True),
                    stability=ModelStability.PREVIEW,
                )
            ],
            origin=CatalogOrigin.DYNAMIC,
        )

        with self.assertRaisesRegex(ModelSelectionUnavailableError, "p/preview"):
            ModelSelectionResolver(catalog).resolve(ModelSelectionContext(message=ref))

        resolved = ModelSelectionResolver(catalog, include_preview=True).resolve(
            ModelSelectionContext(message=ref)
        )
        self.assertEqual(resolved.ref, ref)


if __name__ == "__main__":
    unittest.main()
