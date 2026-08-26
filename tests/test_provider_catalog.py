"""Composição e filtragem do catálogo híbrido de modelos."""

from __future__ import annotations

import unittest

from kairos_providers.catalog import CatalogSnapshot, ModelCatalog
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelKind,
    ModelStability,
    ProviderModelRef,
)
from kairos_providers.curated_catalog import curated_models


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
            capabilities=ModelCapabilities(chat=True, tools=True, context_length=context),
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

    def test_dinamico_completa_capacidades_ausentes_com_curadoria(self):
        catalog = ModelCatalog()
        curated = CatalogModel(
            ref=ProviderModelRef("p", "m"),
            display_name="Curated",
            capabilities=ModelCapabilities(
                chat=True,
                tools=True,
                vision=False,
                streaming=True,
                context_length=100,
                max_output_tokens=20,
            ),
            origins=frozenset({CatalogOrigin.CURATED}),
        )
        dynamic = CatalogModel(
            ref=ProviderModelRef("p", "m"),
            display_name="Dynamic",
            capabilities=ModelCapabilities(
                chat=True,
                context_length=200,
            ),
            origins=frozenset({CatalogOrigin.DYNAMIC}),
        )
        catalog.merge([curated], origin=CatalogOrigin.CURATED)

        catalog.merge([dynamic], origin=CatalogOrigin.DYNAMIC)

        capabilities = catalog.find(ProviderModelRef("p", "m")).capabilities
        self.assertEqual(capabilities.context_length, 200)
        self.assertTrue(capabilities.tools)
        self.assertFalse(capabilities.vision)
        self.assertTrue(capabilities.streaming)
        self.assertEqual(capabilities.max_output_tokens, 20)

    def test_dinamico_nao_apaga_metadados_omitidos_pela_descoberta(self):
        """Uma resposta parcial da API não pode esconder parâmetros e modalidades ainda válidos."""
        catalog = ModelCatalog()
        curated = CatalogModel(
            ref=ProviderModelRef("openrouter", "example/chat"),
            display_name="Example",
            capabilities=ModelCapabilities(chat=True),
            supported_parameters=frozenset({"temperature", "tools"}),
            input_modalities=frozenset({"text", "image"}),
            output_modalities=frozenset({"text"}),
            expiration_date="2026-12-31",
        )
        dynamic = CatalogModel(
            ref=curated.ref,
            display_name="Example dynamic",
            capabilities=ModelCapabilities(chat=True),
            origins=frozenset({CatalogOrigin.DYNAMIC}),
        )
        catalog.merge([curated], origin=CatalogOrigin.CURATED)
        catalog.merge([dynamic], origin=CatalogOrigin.DYNAMIC)

        found = catalog.find(curated.ref)
        self.assertEqual(found.supported_parameters, curated.supported_parameters)
        self.assertEqual(found.input_modalities, curated.input_modalities)
        self.assertEqual(found.output_modalities, curated.output_modalities)
        self.assertEqual(found.expiration_date, curated.expiration_date)

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

        self.assertEqual([model.ref.model for model in catalog.list_models("p")], ["stable"])
        self.assertEqual(
            [model.ref.model for model in catalog.list_models("p", include_preview=True)],
            ["preview", "stable"],
        )

    def test_snapshot_expirado_nao_substitui_cache_utilizavel(self):
        catalog = ModelCatalog(clock=lambda: 1000.0)
        catalog.load_snapshot(
            CatalogSnapshot(models=(self.model(),), fetched_at=900.0, expires_at=1100.0)
        )
        catalog.load_snapshot(
            CatalogSnapshot(
                models=(self.model("expired"),),
                fetched_at=800.0,
                expires_at=999.0,
            )
        )

        self.assertEqual(catalog.find(ProviderModelRef("p", "m")).ref.model, "m")

    def test_snapshots_de_providers_distintos_tem_ordem_independente(self):
        catalog = ModelCatalog(clock=lambda: 1000.0)
        openai = CatalogModel(
            ref=ProviderModelRef("openai", "gpt"),
            display_name="GPT",
            capabilities=ModelCapabilities(chat=True),
        )
        anthropic = CatalogModel(
            ref=ProviderModelRef("anthropic", "claude"),
            display_name="Claude",
            capabilities=ModelCapabilities(chat=True),
        )

        catalog.load_snapshot(CatalogSnapshot((openai,), fetched_at=900.0, expires_at=1100.0))
        catalog.load_snapshot(CatalogSnapshot((anthropic,), fetched_at=800.0, expires_at=1100.0))

        self.assertEqual(catalog.find(openai.ref).display_name, "GPT")
        self.assertEqual(catalog.find(anthropic.ref).display_name, "Claude")


class CuratedCatalogTests(unittest.TestCase):
    def test_inclui_recomendacoes_agentic_da_especificacao(self):
        refs = {model.ref for model in curated_models()}

        self.assertIn(ProviderModelRef("openai", "gpt-5.6-terra"), refs)
        self.assertIn(ProviderModelRef("anthropic", "claude-sonnet-5"), refs)
        self.assertIn(ProviderModelRef("gemini", "gemini-3.7-flash"), refs)
        self.assertIn(ProviderModelRef("deepseek", "deepseek-v4-pro"), refs)

    def test_curadoria_principal_contem_somente_chat(self):
        self.assertTrue(all(model.capabilities.chat for model in curated_models()))

    def test_antigravity_e_remote_agent_preview(self):
        antigravity = next(model for model in curated_models() if model.ref.model == "antigravity")

        self.assertEqual(antigravity.ref.kind, ModelKind.REMOTE_AGENT)
        self.assertEqual(antigravity.stability, ModelStability.PREVIEW)


if __name__ == "__main__":
    unittest.main()
