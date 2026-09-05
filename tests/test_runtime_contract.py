from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kairos_runtime.contracts import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeObservation,
    RuntimeSession,
)
from kairos_runtime.errors import RuntimeErrorInfo


def test_runtime_error_exposes_only_public_code_and_message() -> None:
    error = RuntimeErrorInfo("transport", "runtime indisponível", True)

    assert str(error) == "transport: runtime indisponível"
    assert error.code == "transport"
    assert error.message == "runtime indisponível"
    assert error.retryable is True


def test_capabilities_from_json_ignores_additive_fields() -> None:
    capabilities = RuntimeCapabilities.from_json(
        {
            "protocol_version": 1,
            "features": ["text", "resume"],
            "future_extension": {"enabled": True},
        }
    )

    assert capabilities == RuntimeCapabilities(1, frozenset({"text", "resume"}))
    assert isinstance(capabilities.features, frozenset)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"protocol_version": True, "features": []},
        {"protocol_version": 1},
        {"protocol_version": 1, "features": "text"},
        {"protocol_version": 1, "features": ["text", 1]},
    ],
)
def test_capabilities_from_json_requires_strict_fields(payload: object) -> None:
    with pytest.raises(RuntimeErrorInfo) as exc_info:
        RuntimeCapabilities.from_json(payload)

    assert exc_info.value.code == "invalid_event"


def test_session_from_json_is_frozen_and_ignores_additive_fields() -> None:
    session = RuntimeSession.from_json(
        {
            "session_id": "session-1",
            "runtime_kind": "codex",
            "cwd": "/tmp/project",
            "sandbox": "workspace_write",
            "external_thread_id": None,
            "future_extension": "ignored",
        }
    )

    assert session.runtime_kind == "codex"
    with pytest.raises(FrozenInstanceError):
        session.cwd = "/tmp/other"  # type: ignore[misc]


@pytest.mark.parametrize("missing", ["session_id", "runtime_kind", "cwd", "sandbox"])
def test_session_from_json_requires_every_non_optional_field(missing: str) -> None:
    payload = {
        "session_id": "session-1",
        "runtime_kind": "codex",
        "cwd": "/tmp/project",
        "sandbox": "read_only",
    }
    del payload[missing]

    with pytest.raises(RuntimeErrorInfo) as exc_info:
        RuntimeSession.from_json(payload)

    assert exc_info.value.code == "invalid_event"


def test_event_from_json_copies_and_recursively_freezes_payload() -> None:
    source = {"text": "olá", "nested": {"items": ["one"]}}
    event = RuntimeEvent.from_json(
        {
            "protocol_version": 1,
            "event_id": "event-1",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "sequence": 1,
            "cursor": "cursor-1",
            "kind": "delta",
            "payload": source,
            "future_extension": "ignored",
        }
    )

    source["nested"]["items"].append("two")  # type: ignore[index, union-attr]
    assert event.payload["nested"]["items"] == ("one",)  # type: ignore[index]
    with pytest.raises(TypeError):
        event.payload["text"] = "alterado"  # type: ignore[index]


@pytest.mark.parametrize("sequence", [0, -1, True, 1.5])
def test_event_requires_positive_integer_sequence(sequence: object) -> None:
    with pytest.raises(RuntimeErrorInfo) as exc_info:
        RuntimeEvent.from_json(
            {
                "protocol_version": 1,
                "event_id": "event-1",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "sequence": sequence,
                "cursor": "cursor-1",
                "kind": "delta",
                "payload": {},
            }
        )

    assert exc_info.value.code == "invalid_event"


def test_observation_from_json_freezes_item_collections() -> None:
    source = {"kind": "message", "content": ["hello"]}
    observation = RuntimeObservation.from_json(
        {
            "state": "active",
            "external_turn_id": "external-1",
            "items": [source],
            "pending_requests": [{"request_id": "request-1"}],
            "future_extension": 42,
        }
    )

    source["content"].append("changed")  # type: ignore[union-attr]
    assert observation.items[0]["content"] == ("hello",)
    with pytest.raises(TypeError):
        observation.pending_requests[0]["request_id"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    "payload",
    [
        {"state": "active", "items": [], "pending_requests": []},
        {
            "state": "finished",
            "external_turn_id": None,
            "items": [],
            "pending_requests": [],
        },
        {
            "state": "active",
            "external_turn_id": None,
            "items": {},
            "pending_requests": [],
        },
    ],
)
def test_observation_from_json_rejects_missing_or_invalid_fields(payload: object) -> None:
    with pytest.raises(RuntimeErrorInfo) as exc_info:
        RuntimeObservation.from_json(payload)

    assert exc_info.value.code == "invalid_event"
