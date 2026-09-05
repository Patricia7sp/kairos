"""Tradução entre o protocolo WebSocket v1 e eventos de interação."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairos_integration import (
    INTERACTION_PROTOCOL_VERSION,
    InteractionEnvelope,
    interaction_event_to_json,
)
from kairos_providers import ProviderModelRef

__all__ = ["PROTOCOL_VERSION", "interaction_envelope_from_json", "interaction_event_to_json"]

PROTOCOL_VERSION = INTERACTION_PROTOCOL_VERSION


def interaction_envelope_from_json(message: Mapping[str, Any]) -> InteractionEnvelope:
    """Normaliza uma mensagem WebSocket v1 sem selecionar provider ou modelo."""
    protocol = message.get("protocol", PROTOCOL_VERSION)
    if type(protocol) is not int or protocol != PROTOCOL_VERSION:
        raise ValueError(f"protocolo WebSocket não suportado: {protocol!r}")

    parameters = message.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise TypeError("parameters deve ser um mapping")

    return InteractionEnvelope(
        conversation_id=message.get("session_id") or "web-default",
        source="web",
        content=_required_text(message.get("content"), "content"),
        profile=_optional_text(message.get("profile"), "profile"),
        activity=_optional_text(message.get("activity"), "activity"),
        override=_model_override(message.get("provider"), message.get("model")),
        parameters=parameters,
        idempotency_key=_optional_text(message.get("idempotency_key"), "idempotency_key"),
    )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} é obrigatório")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} deve ser uma string")
    return value.strip() or None


def _model_override(provider: object, model: object) -> ProviderModelRef | None:
    if provider is None and model is None:
        return None
    if provider is None and isinstance(model, str) and "/" in model:
        provider, model = model.split("/", 1)
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider é obrigatório quando model é informado")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model é obrigatório quando provider é informado")
    return ProviderModelRef(provider.strip(), model.strip())
