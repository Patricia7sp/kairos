from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import get_type_hints

import pytest

from kairos_integration import InteractionEnvelope
from kairos_integration.router import InteractionRouter
from kairos_providers import ProviderModelRef
from kairos_runtime import RuntimeErrorInfo, RuntimeEvent
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository


def async_test(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class FakeModelService:
    def __init__(self) -> None:
        self.envelopes = []
        self.closed = False
        self.approval_decisions = []

    async def stream(self, envelope):
        self.envelopes.append(envelope)
        yield "model-event"

    def decide_tool_approval(self, *, approval_id, session_id, decision):
        self.approval_decisions.append((approval_id, session_id, decision))

    async def aclose(self):
        self.closed = True


class FakeRuntimeClient:
    def __init__(self, events=()) -> None:
        self.events = events
        self.submitted = []
        self.closed = False

    async def submit(self, session_id, content, idempotency_key):
        self.submitted.append((session_id, content, idempotency_key))
        return "accepted-turn"

    async def subscribe(self, session_id, cursor=None):
        del session_id, cursor
        for event in self.events:
            yield event

    async def aclose(self):
        self.closed = True


class AcceptanceOrderingRuntimeClient(FakeRuntimeClient):
    def __init__(self, accepted: list[str], events=()) -> None:
        super().__init__(events)
        self.accepted = accepted
        self.accepted_before_subscribe: tuple[str, ...] | None = None

    async def subscribe(self, session_id, cursor=None):
        self.accepted_before_subscribe = tuple(self.accepted)
        async for event in super().subscribe(session_id, cursor):
            yield event


def runtime_event(*, turn_id: str, sequence: int, kind: str) -> RuntimeEvent:
    return RuntimeEvent(
        protocol_version=1,
        event_id=f"event-{sequence}",
        session_id="runtime-session",
        turn_id=turn_id,
        sequence=sequence,
        cursor=f"v1:runtime-session:{sequence}",
        kind=kind,
        payload={"state": "completed"} if kind == "turn_end" else {},
    )


def make_router(tmp_path: Path, *, events=()):
    db = connect(tmp_path / "state.db")
    initialize_schema(db)
    db.close()
    model = FakeModelService()
    runtime = FakeRuntimeClient(events)
    return InteractionRouter(tmp_path, model, runtime), model, runtime


def test_builder_exposes_router_lifecycle_without_starting_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kairos_integration import composition

    model = FakeModelService()
    monkeypatch.setattr(composition, "build_interaction_service", lambda _home: model)

    router = composition.build_interaction_router(tmp_path)

    assert get_type_hints(composition.build_interaction_router)["return"] is InteractionRouter
    assert isinstance(router, InteractionRouter)
    assert not (tmp_path / "run" / "runtime.sock").exists()
    asyncio.run(router.aclose())


@async_test
async def test_missing_and_legacy_sessions_keep_model_creation_path(tmp_path: Path) -> None:
    router, model, runtime = make_router(tmp_path)
    envelope = InteractionEnvelope("new-session", "test", "olá")

    assert [item async for item in router.stream(envelope)] == ["model-event"]

    with connect(tmp_path / "state.db") as db:
        SessionRepository(db).create("legacy", "test")
    legacy = InteractionEnvelope("legacy", "test", "de novo")
    assert [item async for item in router.stream(legacy)] == ["model-event"]
    assert model.envelopes == [envelope, legacy]
    assert runtime.submitted == []
    await router.aclose()


@async_test
async def test_runtime_routes_persisted_identity_and_finishes_only_accepted_turn(
    tmp_path: Path,
) -> None:
    events = (
        runtime_event(turn_id="older-turn", sequence=1, kind="turn_end"),
        runtime_event(turn_id="accepted-turn", sequence=2, kind="text"),
        runtime_event(turn_id="accepted-turn", sequence=3, kind="turn_end"),
        runtime_event(turn_id="later-turn", sequence=4, kind="text"),
    )
    router, model, runtime = make_router(tmp_path, events=events)
    project = tmp_path / "project"
    project.mkdir()
    from runtime_support import runtime_session

    from kairos_state.repositories.runtime import RuntimeRepository

    with connect(tmp_path / "state.db") as db:
        RuntimeRepository(db).create_session(
            runtime_session(project, "runtime-session"),
            "test",
            allowed_directories=(str(project),),
        )
    envelope = InteractionEnvelope(
        "runtime-session", "test", "execute", idempotency_key="durable-key"
    )

    received = [item async for item in router.stream(envelope)]

    assert [item.event_id for item in received] == ["event-2", "event-3"]
    assert runtime.submitted == [("runtime-session", "execute", "durable-key")]
    assert model.envelopes == []
    await router.aclose()
    assert model.closed and runtime.closed


@async_test
async def test_runtime_acceptance_callback_precedes_subscription(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.db")
    initialize_schema(db)
    db.close()
    project = tmp_path / "project"
    project.mkdir()
    from runtime_support import runtime_session

    from kairos_state.repositories.runtime import RuntimeRepository

    with connect(tmp_path / "state.db") as db:
        RuntimeRepository(db).create_session(
            runtime_session(project, "runtime-session"),
            "test",
            allowed_directories=(str(project),),
        )

    accepted: list[str] = []
    runtime = AcceptanceOrderingRuntimeClient(
        accepted,
        events=(runtime_event(turn_id="accepted-turn", sequence=1, kind="turn_end"),),
    )
    router = InteractionRouter(tmp_path, FakeModelService(), runtime)
    envelope = InteractionEnvelope(
        "runtime-session", "test", "execute", idempotency_key="durable-key"
    )

    received = [
        event async for event in router.stream(envelope, on_runtime_accepted=accepted.append)
    ]

    assert accepted == ["accepted-turn"]
    assert runtime.accepted_before_subscribe == ("accepted-turn",)
    assert [event.event_id for event in received] == ["event-1"]
    await router.aclose()


@pytest.mark.parametrize(
    "envelope",
    [
        InteractionEnvelope("runtime-session", "test", "x"),
        InteractionEnvelope(
            "runtime-session",
            "test",
            "x",
            override=ProviderModelRef("openai", "gpt-4o"),
            idempotency_key="key",
        ),
        InteractionEnvelope(
            "runtime-session",
            "test",
            "x",
            parameters={"cwd": "/tmp/other"},
            idempotency_key="key",
        ),
        InteractionEnvelope("runtime-session", "test", "x", tools=True, idempotency_key="key"),
        InteractionEnvelope(
            "runtime-session",
            "test",
            "x",
            web_search=True,
            idempotency_key="key",
        ),
    ],
)
@async_test
async def test_runtime_requires_key_and_rejects_provider_or_identity_override(
    tmp_path: Path, envelope: InteractionEnvelope
) -> None:
    router, _model, runtime = make_router(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    from runtime_support import runtime_session

    from kairos_state.repositories.runtime import RuntimeRepository

    with connect(tmp_path / "state.db") as db:
        RuntimeRepository(db).create_session(
            runtime_session(project, "runtime-session"),
            "test",
            allowed_directories=(str(project),),
        )

    with pytest.raises(RuntimeErrorInfo) as raised:
        await anext(router.stream(envelope))

    assert raised.value.code == "invalid_event"
    assert runtime.submitted == []
    await router.aclose()


def test_router_forwards_tool_approval_decisions_to_the_model_service(tmp_path: Path) -> None:
    router, model, _runtime = make_router(tmp_path)
    router.decide_tool_approval(approval_id="a1", session_id="s1", decision="deny")
    assert model.approval_decisions == [("a1", "s1", "deny")]
    asyncio.run(router.aclose())


def test_router_reports_unavailable_when_model_service_cannot_decide(tmp_path: Path) -> None:
    class DummyModel:
        async def aclose(self):
            return None

    router = InteractionRouter(tmp_path, DummyModel(), FakeRuntimeClient())
    with pytest.raises(RuntimeErrorInfo) as raised:
        router.decide_tool_approval(approval_id="a1", session_id="s1", decision="allow")
    assert raised.value.code == "unavailable"
    asyncio.run(router.aclose())
