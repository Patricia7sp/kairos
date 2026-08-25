"""Composição e filtragem do catálogo híbrido de modelos."""

from __future__ import annotations

import unittest

from kairos_providers.catalog import CatalogSnapshot, ModelCatalog
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelStability,
    ProviderModelRef,
)


class ModelCatalogTests(unittest.TestCase):
    def model(
        self,
        model: str = "m",
        *,
        origin: CatalogOrigin = CatalogOrigin.CURATED,
        context: int = 100,
        stability: ModelStability = ModelStability.STABLE,
    ) -> CatalogModel:
        return CatalogModel(
            ref=ProviderModelRef("p", model),
            display_name=model,
            capabilities=ModelCapabilities(
                chat=True, tools=True, context_length=context
            ),
            stability=stability,
            origins=frozenset({origin}),
        )

    def test_dinamico_prevalece_e_preserva_origens(self):
        catalog = ModelCatalog()
        catalog.merge([self.model(context=100)], origin=CatalogOrigin.CURATED)
        catalog.merge(
            [self.model(origin=CatalogOrigin.DYNAMIC, context=200)],
            origin=CatalogOrigin.DYNAMIC,
        )

        found = catalog.find(ProviderModelRef("p", "m"))
        self.assertEqual(found.capabilities.context_length, 200)
        self.assertEqual(
            found.origins,
            frozenset({CatalogOrigin.CURATED, CatalogOrigin.DYNAMIC}),
        )

    def test_curado_prevalece_sobre_cache(self):
        catalog = ModelCatalog()
        catalog.merge(
            [self.model(origin=CatalogOrigin.CACHE, context=50)],
            origin=CatalogOrigin.CACHE,
        )
        catalog.merge([self.model(context=100)], origin=CatalogOrigin.CURATED)

        self.assertEqual(
            catalog.find(ProviderModelRef("p", "m")).capabilities.context_length,
            100,
        )

    def test_preview_e_deprecated_ficam_ocultos_por_padrao(self):
        catalog = ModelCatalog()
        catalog.merge(
            [
                self.model("stable"),
                self.model("preview", stability=ModelStability.PREVIEW),
                self.model("old", stability=ModelStability.DEPRECATED),
            ],
            origin=CatalogOrigin.CURATED,
        )

        self.assertEqual(
            [model.ref.model for model in catalog.list_models("p")], ["stable"]
        )
        self.assertEqual(
            [
                model.ref.model
                for model in catalog.list_models("p", include_preview=True)
            ],
            ["preview", "stable"],
        )

    def test_snapshot_expirado_nao_substitui_cache_utilizavel(self):
        catalog = ModelCatalog(clock=lambda: 1000.0)
        catalog.load_snapshot(
            CatalogSnapshot(
                models=(self.model(),), fetched_at=900.0, expires_at=1100.0
            )
        )
        catalog.load_snapshot(
            CatalogSnapshot(
                models=(self.model("expired"),),
                fetched_at=800.0,
                expires_at=999.0,
            )
        )

        self.assertEqual(catalog.find(ProviderModelRef("p", "m")).ref.model, "m")


if __name__ == "__main__":
    unittest.main()
