"""Deterministic snapshot reconciliation and journal-based text projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .contracts import RuntimeEvent, RuntimeObservation


def json_value(value):
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def reconciliation(
    thread_id: str, observation: RuntimeObservation, reason: str
) -> tuple[str, dict]:
    snapshot = {
        "external_thread_id": thread_id,
        "external_turn_id": observation.external_turn_id,
        "state": observation.state,
        "items": json_value(observation.items),
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    event_id = "snapshot:" + hashlib.sha256(encoded.encode()).hexdigest()
    return event_id, {"snapshot": snapshot, "reason": reason}


class TranscriptProjection:
    """Each snapshot replaces its item; identical deltas remain distinct journal events."""

    def __init__(self):
        self.items: dict[str, str] = {}
        self.usage = None
        self._seen: set[str] = set()

    @property
    def content(self) -> str:
        return "".join(self.items.values())

    def apply(self, event: RuntimeEvent) -> None:
        if event.event_id in self._seen:
            return
        self._seen.add(event.event_id)
        payload = event.payload
        if event.kind == "text":
            item = payload.get("item")
            if isinstance(item, Mapping):
                if isinstance(item.get("id"), str) and isinstance(item.get("text"), str):
                    self.items[item["id"]] = item["text"]
            elif isinstance(payload.get("itemId"), str) and isinstance(payload.get("delta"), str):
                key = payload["itemId"]
                self.items[key] = self.items.get(key, "") + payload["delta"]
        elif event.kind == "reconciled":
            for item in payload["snapshot"]["items"]:
                if (
                    item.get("type") == "agentMessage"
                    and isinstance(item.get("id"), str)
                    and isinstance(item.get("text"), str)
                ):
                    self.items[item["id"]] = item["text"]
        elif event.kind == "usage":
            self.usage = json_value(payload)
