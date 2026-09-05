"""Fronteira JSON-lines pública do host local de runtime."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import RuntimeEvent
from .errors import RuntimeErrorInfo
from .redaction import sanitize_payload

__all__ = ["MAX_MESSAGE_BYTES", "json_value", "runtime_event_to_json"]

MAX_MESSAGE_BYTES = 1024 * 1024


def json_value(value: Any) -> Any:
    """Copy a typed runtime payload into strict, mutable JSON values."""
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise RuntimeErrorInfo("invalid_event", "payload de runtime inválido", False)
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise RuntimeErrorInfo("invalid_event", "payload de runtime inválido", False)
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [json_value(item) for item in sorted(value, key=repr)]
    raise RuntimeErrorInfo("invalid_event", "payload de runtime inválido", False)


def runtime_event_to_json(event: RuntimeEvent) -> dict[str, Any]:
    if not isinstance(event, RuntimeEvent):
        raise TypeError("event deve ser RuntimeEvent")
    return {
        "protocol_version": event.protocol_version,
        "event_id": event.event_id,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "sequence": event.sequence,
        "cursor": event.cursor,
        "kind": event.kind,
        "payload": sanitize_payload(event.kind, json_value(event.payload)),
    }


def encode_message(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            json_value(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeErrorInfo("invalid_event", "payload de runtime inválido", False) from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise RuntimeErrorInfo("invalid_event", "mensagem de runtime excede o limite", False)
    return encoded + b"\n"


async def read_message(reader) -> dict[str, Any]:
    try:
        raw = await reader.readline()
    except (ValueError, asyncio.LimitOverrunError) as exc:
        raise RuntimeErrorInfo(
            "invalid_event", "mensagem de runtime excede o limite", False
        ) from exc
    if not raw:
        raise RuntimeErrorInfo("transport", "conexão com runtime encerrada", True)
    if len(raw) > MAX_MESSAGE_BYTES + 1 or not raw.endswith(b"\n"):
        raise RuntimeErrorInfo("invalid_event", "mensagem de runtime excede o limite", False)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeErrorInfo("invalid_event", "mensagem de runtime inválida", False) from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RuntimeErrorInfo("invalid_event", "mensagem de runtime inválida", False)
    return value
