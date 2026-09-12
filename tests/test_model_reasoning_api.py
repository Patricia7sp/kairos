"""Metadados explícitos de raciocínio no payload público de modelos."""

from kairos_providers.contracts import CatalogModel, ProviderModelRef
from kairos_web.provider_api import serialize_model


def test_serialize_model_expoe_reasoning_quando_parametro_e_explicitamente_suportado():
    model = CatalogModel(
        ref=ProviderModelRef("openrouter", "vendor/reasoning"),
        display_name="Reasoning",
        supported_parameters=frozenset({"temperature", "reasoning"}),
    )

    assert serialize_model(model)["capabilities"]["reasoning"] is True


def test_serialize_model_mantem_reasoning_desconhecido_sem_metadado_explicito():
    model = CatalogModel(
        ref=ProviderModelRef("openrouter", "vendor/unknown"),
        display_name="Unknown",
    )

    assert serialize_model(model)["capabilities"]["reasoning"] is None


def test_serialize_model_reconhece_variantes_explicitas_do_parametro_reasoning():
    for parameter in ("include_reasoning", "reasoning_effort"):
        model = CatalogModel(
            ref=ProviderModelRef("openrouter", f"vendor/{parameter}"),
            display_name=parameter,
            supported_parameters=frozenset({parameter}),
        )

        assert serialize_model(model)["capabilities"]["reasoning"] is True
