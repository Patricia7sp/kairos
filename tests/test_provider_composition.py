"""Composição local do gateway moderno e ponte temporária do manager."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from kairos_providers.composition import build_provider_gateway
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelPrice,
    ProviderModelRef,
)
from kairos_providers.manager import ProviderManager


def test_composition_registra_todos_os_providers(tmp_path):
    """A omissão de qualquer factory torna o provider impossível de selecionar."""
    gateway = build_provider_gateway(tmp_path)

    ids = [descriptor.id for descriptor in gateway.registry.list_descriptors()]

    assert ids == [
        "anthropic",
        "custom",
        "deepseek",
        "gemini",
        "groq",
        "ollama",
        "openai",
        "openrouter",
    ]


def test_manager_converte_catalogo_moderno_somente_na_borda_legada(tmp_path):
    """A conversão protege clientes legados de conhecerem CatalogModel."""
    gateway = build_provider_gateway(tmp_path)
    gateway.catalog.merge(
        [
            CatalogModel(
                ref=ProviderModelRef("openai", "gpt-edge"),
                display_name="GPT Edge",
                capabilities=ModelCapabilities(
                    chat=True,
                    tools=True,
                    vision=False,
                    streaming=True,
                    context_length=32_000,
                    max_output_tokens=4_000,
                ),
                origins=frozenset({CatalogOrigin.CURATED}),
                price=ModelPrice(prompt=Decimal("1.25"), completion=Decimal("2.50")),
            )
        ],
        origin=CatalogOrigin.CURATED,
    )

    models = ProviderManager(gateway=gateway).list_all_models()
    edge = next(model for model in models if model.id == "gpt-edge")

    assert edge.provider == "openai"
    assert edge.context_length == 32_000
    assert edge.max_output_tokens == 4_000
    assert edge.supports_tools is True
    assert edge.cost_input_per_million == 1.25
    assert edge.cost_output_per_million == 2.5


def test_manager_legado_nao_cria_cofre_ao_instanciar_adapter(tmp_path, monkeypatch):
    """Criar adapter legado não deve concorrer pela inicialização do cofre."""
    passphrase_file = tmp_path / "vault-passphrase"
    passphrase_file.write_text("senha-mestra-de-teste\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase_file))

    adapter = ProviderManager().get_provider("openai")

    assert adapter.name == "openai"
    assert not (Path(tmp_path) / "credentials.vault").exists()
