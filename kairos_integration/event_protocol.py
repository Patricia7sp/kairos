"""Versioned JSON representation shared by interaction transports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairos_integration.interaction_contract import InteractionEvent, InteractionEventKind

__all__ = ["INTERACTION_PROTOCOL_VERSION", "interaction_event_to_json"]

INTERACTION_PROTOCOL_VERSION = 1


def interaction_event_to_json(
    event: InteractionEvent,
    *,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Serialize one canonical event without credential metadata."""
    session_id = event.conversation_id or conversation_id
    payload: dict[str, Any] = {
        "type": event.kind.value,
        "protocol": INTERACTION_PROTOCOL_VERSION,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    payload.update(_event_fields(event))
    return payload


def _event_fields(event: InteractionEvent) -> dict[str, Any]:  # noqa: PLR0912
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
    elif event.kind is InteractionEventKind.TOOL_APPROVAL_REQUEST:
        if event.tool_call is None or event.tool_approval_id is None:
            raise ValueError("tool_approval_request exige tool_call e approval_id")
        tool_call = {
            "id": event.tool_call.id,
            "name": event.tool_call.name,
            "arguments": event.tool_call.arguments,
        }
        payload.update(approval_id=event.tool_approval_id, tool_call=tool_call)
    elif event.kind is InteractionEventKind.USAGE:
        payload["usage"] = (
            {
                "input_tokens": event.usage.input_tokens,
                "output_tokens": event.usage.output_tokens,
                "cache_read_tokens": event.usage.cache_read_tokens,
                "reasoning_tokens": event.usage.reasoning_tokens,
                "cache_write_tokens": event.usage.cache_write_tokens,
                "total_tokens": event.usage.total,
            }
            if event.usage is not None
            else None
        )
        payload["cost"] = {
            "estimated_usd": event.cost.estimated_usd,
            "actual_usd": event.cost.actual_usd,
            "status": event.cost.status,
            "source": event.cost.source,
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


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_value(item) for item in sorted(value, key=repr)]
    return value
