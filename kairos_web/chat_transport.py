"""Tradução entre o protocolo WebSocket v1 e eventos de interação."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairos_integration import InteractionEnvelope, InteractionEvent, InteractionEventKind
from kairos_providers import ProviderModelRef

PROTOCOL_VERSION = 1


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
    )


def interaction_event_to_json(
    event: InteractionEvent,
    *,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Serializa todo evento canônico como JSON aditivo do protocolo v1."""
    session_id = event.conversation_id or conversation_id
    payload: dict[str, Any] = {
        "type": event.kind.value,
        "protocol": PROTOCOL_VERSION,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    payload.update(_event_fields(event))
    return payload


def _event_fields(event: InteractionEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if event.kind is InteractionEventKind.TURN_START:
        if event.snapshot is None:
            raise ValueError("turn_start exige snapshot")
        payload.update(
            provider=event.snapshot.ref.provider,
            model=event.snapshot.ref.model,
            selection_reason=event.snapshot.reason.value,
            parameters=_json_value(event.snapshot.parameters),
        )
    elif event.kind is InteractionEventKind.DELTA:
        payload["text"] = event.text
    elif event.kind is InteractionEventKind.REASONING_DELTA:
        payload.update(text=event.reasoning, reasoning=event.reasoning)
    elif event.kind is InteractionEventKind.TOOL_CALL:
        if event.tool_call is None:
            raise ValueError("tool_call exige payload")
        tool_call = {
            "id": event.tool_call.id,
            "name": event.tool_call.name,
            "arguments": event.tool_call.arguments,
        }
        payload.update(tool_call=tool_call, tool_calls=[tool_call])
    elif event.kind is InteractionEventKind.TOOL_RESULT:
        if event.tool_result is None:
            raise ValueError("tool_result exige payload")
        payload["tool_result"] = {
            "tool_call_id": event.tool_result.tool_call_id,
            "content": event.tool_result.content,
            "is_error": event.tool_result.is_error,
        }
    elif event.kind is InteractionEventKind.USAGE:
        if event.usage is None:
            raise ValueError("usage exige payload")
        payload["usage"] = {
            "input_tokens": event.usage.input_tokens,
            "output_tokens": event.usage.output_tokens,
            "cache_read_tokens": event.usage.cache_read_tokens,
            "reasoning_tokens": event.usage.reasoning_tokens,
            "total_tokens": event.usage.total,
        }
    elif event.kind is InteractionEventKind.TURN_ERROR:
        payload.update(
            error=event.error,
            error_kind=event.error_kind,
            retryable=event.retryable,
        )
    elif event.kind is InteractionEventKind.TURN_END:
        payload["finish_reason"] = event.finish_reason

    return payload


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


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_value(item) for item in sorted(value, key=repr)]
    return value
