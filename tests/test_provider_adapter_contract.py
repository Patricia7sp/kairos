"""Contratos públicos para adapters modernos de providers."""

from decimal import Decimal

from kairos_providers import (
    AdapterRequest,
    CanonicalMessage,
    CatalogModel,
    ContentPart,
    ModelPrice,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
)


def test_provider_error_normaliza_payload_sem_expor_credencial():
    """Remover a normalização permitiria que erros do upstream vazassem segredos."""
    sentinel = "sk-provider-sentinel-credential"
    upstream_payload = {
        "error": {"message": f"Invalid API key: {sentinel}"},
        "request_headers": {"Authorization": f"Bearer {sentinel}"},
    }

    error = ProviderError.from_upstream(
        ProviderErrorKind.AUTH, upstream_payload, retryable=False
    )

    assert error.kind is ProviderErrorKind.AUTH
    assert error.retryable is False
    assert sentinel not in str(error)
    assert sentinel not in repr(error)


def test_catalog_model_identifica_preco_gratuito():
    """Um preço integralmente zero precisa habilitar o filtro de modelos gratuitos."""
    model = CatalogModel(
        ref=ProviderModelRef("openrouter", "example/free"),
        display_name="Example Free",
        price=ModelPrice(
            prompt=Decimal("0"),
            completion=Decimal("0"),
            request=Decimal("0"),
        ),
    )

    assert model.is_free is True


def test_requisicao_e_evento_usam_payload_canonico_independente_do_provider():
    """Adapters recebem partes canônicas e emitem um evento que o gateway entende."""
    request = AdapterRequest(
        model=ProviderModelRef("openai", "gpt-5.6-terra"),
        messages=(
            CanonicalMessage(
                role="user",
                content=(ContentPart(kind="text", value="Olá"),),
            ),
        ),
    )
    event = ProviderEvent(kind="text_delta", text="Oi")

    assert request.messages[0].content[0].value == "Olá"
    assert event.kind == "text_delta"
    assert event.text == "Oi"
