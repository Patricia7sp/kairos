"""Contrato v1 agnóstico de transporte para runtimes de agente."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self

from .errors import RuntimeErrorInfo

__all__ = [
    "AgentRuntimeProtocol",
    "Decision",
    "RuntimeCapabilities",
    "RuntimeEvent",
    "RuntimeObservation",
    "RuntimeSession",
    "Sandbox",
]


Sandbox = Literal["read_only", "workspace_write", "broad_access"]
Decision = Literal["accept", "decline"]

_SANDBOXES = frozenset({"read_only", "workspace_write", "broad_access"})
_OBSERVATION_STATES = frozenset(
    {"active", "completed", "failed", "interrupted", "cancelled", "missing", "unknown"}
)


def _invalid() -> RuntimeErrorInfo:
    return RuntimeErrorInfo("invalid_event", "contrato de runtime inválido", False)


def _as_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _invalid()
    return value


def _required(data: Mapping[str, Any], field: str) -> Any:
    try:
        return data[field]
    except KeyError as exc:
        raise _invalid() from exc


def _required_text(data: Mapping[str, Any], field: str) -> str:
    value = _required(data, field)
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    return value


def _optional_text(data: Mapping[str, Any], field: str) -> str | None:
    value = data.get(field)
    if value is not None and not isinstance(value, str):
        raise _invalid()
    return value


def _required_optional_text(data: Mapping[str, Any], field: str) -> str | None:
    value = _required(data, field)
    if value is not None and not isinstance(value, str):
        raise _invalid()
    return value


def _required_int(data: Mapping[str, Any], field: str, *, positive: bool = False) -> int:
    value = _required(data, field)
    if type(value) is not int or (positive and value <= 0):
        raise _invalid()
    return value


def _freeze_json(value: Any) -> Any:
    """Copia e congela recursivamente um valor aceito por uma fronteira JSON."""

    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid()
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _invalid()
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    raise _invalid()


def _mapping_tuple(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _invalid()
    frozen: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _invalid()
        frozen.append(_freeze_json(item))
    return tuple(frozen)


@dataclass(frozen=True)
class RuntimeSession:
    session_id: str
    runtime_kind: str
    cwd: str
    sandbox: Sandbox
    external_thread_id: str | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.session_id, self.runtime_kind, self.cwd)
        ):
            raise _invalid()
        if self.sandbox not in _SANDBOXES:
            raise _invalid()
        if self.external_thread_id is not None and (
            not isinstance(self.external_thread_id, str) or not self.external_thread_id.strip()
        ):
            raise _invalid()

    @classmethod
    def from_json(cls, value: object) -> Self:
        """Parse a session mapping, requiring v1 fields and ignoring additions."""

        data = _as_mapping(value)
        sandbox = _required_text(data, "sandbox")
        if sandbox not in _SANDBOXES:
            raise _invalid()
        return cls(
            session_id=_required_text(data, "session_id"),
            runtime_kind=_required_text(data, "runtime_kind"),
            cwd=_required_text(data, "cwd"),
            sandbox=sandbox,
            external_thread_id=_optional_text(data, "external_thread_id"),
        )


@dataclass(frozen=True)
class RuntimeCapabilities:
    protocol_version: int
    features: frozenset[str]

    def __post_init__(self) -> None:
        if type(self.protocol_version) is not int or self.protocol_version <= 0:
            raise _invalid()
        if not isinstance(self.features, (set, frozenset)) or any(
            not isinstance(feature, str) or not feature for feature in self.features
        ):
            raise _invalid()
        object.__setattr__(self, "features", frozenset(self.features))

    @classmethod
    def from_json(cls, value: object) -> Self:
        """Parse capabilities, requiring both v1 fields and ignoring additions."""

        data = _as_mapping(value)
        features = _required(data, "features")
        if not isinstance(features, Sequence) or isinstance(features, (str, bytes, bytearray)):
            raise _invalid()
        if any(not isinstance(feature, str) or not feature for feature in features):
            raise _invalid()
        return cls(
            protocol_version=_required_int(data, "protocol_version", positive=True),
            features=frozenset(features),
        )


@dataclass(frozen=True)
class RuntimeEvent:
    protocol_version: int
    event_id: str
    session_id: str
    turn_id: str
    sequence: int
    cursor: str
    kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.protocol_version) is not int or self.protocol_version != 1:
            raise _invalid()
        if type(self.sequence) is not int or self.sequence <= 0:
            raise _invalid()
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.event_id, self.session_id, self.turn_id, self.cursor, self.kind)
        ):
            raise _invalid()
        if not isinstance(self.payload, Mapping):
            raise _invalid()
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @classmethod
    def from_json(cls, value: object) -> Self:
        """Parse one committed runtime event and ignore additive envelope fields."""

        data = _as_mapping(value)
        payload = _required(data, "payload")
        if not isinstance(payload, Mapping):
            raise _invalid()
        return cls(
            protocol_version=_required_int(data, "protocol_version", positive=True),
            event_id=_required_text(data, "event_id"),
            session_id=_required_text(data, "session_id"),
            turn_id=_required_text(data, "turn_id"),
            sequence=_required_int(data, "sequence", positive=True),
            cursor=_required_text(data, "cursor"),
            kind=_required_text(data, "kind"),
            payload=payload,
        )


@dataclass(frozen=True)
class RuntimeObservation:
    state: str
    external_turn_id: str | None
    items: tuple[Mapping[str, Any], ...]
    pending_requests: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if self.state not in _OBSERVATION_STATES:
            raise _invalid()
        if self.external_turn_id is not None and (
            not isinstance(self.external_turn_id, str) or not self.external_turn_id.strip()
        ):
            raise _invalid()
        object.__setattr__(self, "items", _mapping_tuple(self.items))
        object.__setattr__(self, "pending_requests", _mapping_tuple(self.pending_requests))

    @classmethod
    def from_json(cls, value: object) -> Self:
        """Parse a runtime snapshot and ignore additive observation fields."""

        data = _as_mapping(value)
        return cls(
            state=_required_text(data, "state"),
            external_turn_id=_required_optional_text(data, "external_turn_id"),
            items=_mapping_tuple(_required(data, "items")),
            pending_requests=_mapping_tuple(_required(data, "pending_requests")),
        )


class AgentRuntimeProtocol(Protocol):
    @property
    def generation(self) -> str: ...

    async def capabilities(self) -> RuntimeCapabilities: ...

    async def create_thread(self, session: RuntimeSession) -> str: ...

    async def resume_thread(self, session: RuntimeSession) -> RuntimeObservation: ...

    async def start_turn(self, session: RuntimeSession, turn_id: str, content: str) -> str: ...

    async def attach_turn(
        self, session: RuntimeSession, turn_id: str, external_turn_id: str
    ) -> RuntimeObservation: ...

    def observe(
        self, session: RuntimeSession, turn_id: str
    ) -> AsyncIterator[Mapping[str, Any]]: ...

    async def cancel_turn(self, session: RuntimeSession, external_turn_id: str) -> None: ...

    async def respond_approval(self, request_id: str, decision: Decision) -> None: ...

    async def inspect_turn(
        self, session: RuntimeSession, external_turn_id: str | None
    ) -> RuntimeObservation: ...

    async def reconcile(
        self, session: RuntimeSession, cursor: str | None
    ) -> RuntimeObservation: ...

    async def end_thread(self, session: RuntimeSession) -> None: ...

    async def aclose(self) -> None: ...
