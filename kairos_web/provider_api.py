"""Serialização segura das APIs públicas de providers e modelos."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from kairos_providers.contracts import CatalogModel
from kairos_providers.gateway import ProviderGateway


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def serialize_model(model: CatalogModel) -> dict[str, Any]:
    capabilities = model.capabilities
    return {
        "id": model.ref.model,
        "name": model.display_name,
        "provider": model.ref.provider,
        "kind": model.ref.kind.value,
        "stability": model.stability.value,
        "is_free": model.is_free,
        "pricing": {
            "prompt": _decimal(model.price.prompt),
            "completion": _decimal(model.price.completion),
            "request": _decimal(model.price.request),
        },
        "capabilities": {
            "chat": capabilities.chat,
            "tools": capabilities.tools,
            "vision": capabilities.vision,
            "streaming": capabilities.streaming,
            "context_length": capabilities.context_length,
            "max_output_tokens": capabilities.max_output_tokens,
        },
        "origins": sorted(origin.value for origin in model.origins),
        "supported_parameters": sorted(model.supported_parameters),
        "input_modalities": sorted(model.input_modalities),
        "output_modalities": sorted(model.output_modalities),
        "expiration_date": model.expiration_date,
    }


def list_models_payload(
    gateway: ProviderGateway,
    *,
    provider: str | None,
    free_only: bool,
    include_preview: bool,
    default_provider: str,
    default_model: str,
) -> dict[str, Any]:
    models = gateway.catalog.list_models(provider, include_preview=include_preview)
    if free_only:
        models = [model for model in models if model.is_free]
    return {
        "default_provider": default_provider,
        "default_model": default_model,
        "models": [serialize_model(model) for model in models],
    }


def list_providers_payload(gateway: ProviderGateway) -> dict[str, Any]:
    providers = []
    for descriptor in gateway.registry.list_descriptors():
        credential_state = gateway.credential_state(descriptor.id)
        configured = credential_state in {"configured", "not_required"}
        providers.append(
            {
                "id": descriptor.id,
                "provider": descriptor.id,
                "name": descriptor.display_name,
                "auth_methods": list(descriptor.auth_methods),
                "requires_credential": bool(descriptor.auth_methods),
                "configured": configured,
                "credential_state": credential_state,
            }
        )
    return {"providers": providers}
