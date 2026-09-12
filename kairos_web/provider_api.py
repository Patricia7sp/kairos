"""Serialização segura das APIs públicas de providers e modelos."""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kairos_providers.contracts import CatalogModel
from kairos_providers.gateway import ProviderGateway

PUBLIC_SELECTION_PARAMETERS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "seed",
        "parallel_tool_calls",
        "include_reasoning",
        "reasoning_effort",
    }
)


def public_parameters(value: object) -> dict[str, Any]:
    """Expose known nonsecret preferences, never arbitrary nested config."""
    if not isinstance(value, Mapping):
        return {}
    result = {key: item for key, item in value.items() if _public_parameter(key, item)}
    routing = value.get("routing")
    if isinstance(routing, Mapping):
        safe = {
            key: item
            for key, item in routing.items()
            if (key == "data_collection" and item in ("allow", "deny"))
            or (key in {"require_parameters", "allow_fallbacks"} and type(item) is bool)
        }
        if safe:
            result["routing"] = safe
    return result


def _public_parameter(key: str, value: object) -> bool:
    if key not in PUBLIC_SELECTION_PARAMETERS:
        return False
    if key in {"parallel_tool_calls", "include_reasoning"}:
        return type(value) is bool
    if key in {"seed", "max_tokens"}:
        return type(value) is int and abs(value) <= 2**63 - 1
    if key == "reasoning_effort":
        return isinstance(value, str) and value in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }
    if key == "stop":
        return isinstance(value, str) or (
            isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)
        )
    return type(value) in {int, float} and abs(value) <= 1e100 and math.isfinite(value)


def public_profiles(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, Mapping):
        return []
    return [
        {
            "name": name,
            "provider": profile.get("provider", config.get("provider")),
            "model": profile.get("model", config.get("model")),
            "parameters": public_parameters(profile.get("parameters")),
        }
        for name, profile in profiles.items()
        if isinstance(name, str)
        and isinstance(profile, Mapping)
        and isinstance(profile.get("provider", config.get("provider")), str)
        and isinstance(profile.get("model", config.get("model")), str)
    ]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def serialize_model(model: CatalogModel) -> dict[str, Any]:
    capabilities = model.capabilities
    reasoning_parameters = {"reasoning", "include_reasoning", "reasoning_effort"}
    reasoning = True if reasoning_parameters.intersection(model.supported_parameters) else None
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
            "reasoning": reasoning,
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
