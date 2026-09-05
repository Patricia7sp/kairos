from __future__ import annotations

import asyncio
import functools
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kairos_runtime.codex_adapter import CodexAppServerAdapter
from kairos_runtime.contracts import RuntimeObservation, RuntimeSession
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.supervisor import CodexSupervisor

FIXTURE = Path(__file__).parent / "fixtures" / "codex_app_server.py"


def async_test(function: Callable[..., Any]) -> Callable[..., None]:
    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(function(*args, **kwargs))

    return run


def session(
    tmp_path: Path, sandbox: str = "workspace_write", thread_id: str | None = None
) -> RuntimeSession:
    return RuntimeSession(
        session_id="session-1",
        runtime_kind="codex",
        cwd=str(tmp_path),
        sandbox=sandbox,  # type: ignore[arg-type]
        external_thread_id=thread_id,
    )


async def adapter(codex_home: Path, mode: str = "adapter") -> CodexAppServerAdapter:
    supervisor = CodexSupervisor(
        codex_home=str(codex_home),
        executable=sys.executable,
        server_args=(str(FIXTURE), mode),
    )
    await supervisor.start()
    return CodexAppServerAdapter(supervisor)


@async_test
async def test_create_and_resume_enforce_and_revalidate_effective_policy(tmp_path: Path) -> None:
    runtime = await adapter(tmp_path)
    try:
        thread_id = await runtime.create_thread(session(tmp_path))
        assert thread_id == "thread-1"
        observation = await runtime.resume_thread(session(tmp_path, thread_id=thread_id))
        assert observation == RuntimeObservation("unknown", None, (), ())
    finally:
        await runtime.aclose()


@async_test
async def test_turn_observer_is_registered_before_start_and_error_is_nonterminal(
    tmp_path: Path,
) -> None:
    runtime = await adapter(tmp_path)
    active = session(tmp_path, thread_id="thread-1")
    try:
        external_turn_id = await runtime.start_turn(active, "local-turn-1", "hello")
        assert external_turn_id == "external-turn-1"

        events = []
        async for event in runtime.observe(active, "local-turn-1"):
            events.append(event)

        assert [event["kind"] for event in events] == [
            "text",
            "approval",
            "error",
            "tool",
            "text",
            "usage",
            "turn",
        ]
        assert events[0]["payload"]["delta"] == "early "
        assert events[1]["payload"]["rpc_request_id"] == 700
        assert events[1]["payload"]["item_id"] == "change-1"
        assert events[2]["payload"] == {"will_retry": True}
        assert "secret" not in repr(events[2])
        assert events[4]["payload"] == {
            "item": {"id": "message-1", "type": "agentMessage", "text": "early final"},
            "replace": True,
        }
        assert events[-1]["payload"]["state"] == "completed"
        assert all(event["generation"] == runtime.generation for event in events)
    finally:
        await runtime.aclose()


@async_test
async def test_restricted_approval_accept_fails_closed_and_sends_decline(tmp_path: Path) -> None:
    runtime = await adapter(tmp_path)
    active = session(tmp_path, thread_id="thread-1")
    try:
        await runtime.start_turn(active, "local-turn-1", "hello")
        approval = None
        async for event in runtime.observe(active, "local-turn-1"):
            if event["kind"] == "approval":
                approval = event
        assert approval is not None

        with pytest.raises(RuntimeErrorInfo) as exc_info:
            await runtime.respond_approval(approval["payload"]["request_id"], "accept")
        assert exc_info.value.code == "invalid_policy"
        assert "broad_access" in exc_info.value.message
    finally:
        await runtime.aclose()


@async_test
async def test_explicit_broad_approval_preserves_integer_rpc_request_id(tmp_path: Path) -> None:
    runtime = await adapter(tmp_path)
    active = session(tmp_path, sandbox="broad_access", thread_id="thread-1")
    try:
        await runtime.start_turn(active, "local-turn-1", "hello")
        request_id = ""
        async for event in runtime.observe(active, "local-turn-1"):
            if event["kind"] == "approval":
                request_id = event["payload"]["request_id"]
        await runtime.respond_approval(request_id, "accept")
    finally:
        await runtime.aclose()


@async_test
async def test_cancelled_is_only_used_after_our_interrupt(tmp_path: Path) -> None:
    runtime = await adapter(tmp_path)
    active = session(tmp_path, thread_id="interrupted-thread")
    try:
        before = await runtime.inspect_turn(active, "cancelled-turn")
        assert before.state == "interrupted"
        await runtime.cancel_turn(active, "cancelled-turn")
        after = await runtime.inspect_turn(active, "cancelled-turn")
        assert after.state == "cancelled"
    finally:
        await runtime.aclose()


@async_test
async def test_end_thread_unsubscribes_without_deleting(tmp_path: Path) -> None:
    runtime = await adapter(tmp_path)
    try:
        await runtime.end_thread(session(tmp_path, thread_id="thread-1"))
    finally:
        await runtime.aclose()


@async_test
async def test_unknown_server_request_is_explicit_error_without_consent(tmp_path: Path) -> None:
    runtime = await adapter(tmp_path, "unknown-request")
    active = session(tmp_path, thread_id="thread-1")
    try:
        await runtime.start_turn(active, "local-turn-1", "hello")
        events = [event async for event in runtime.observe(active, "local-turn-1")]
        assert any(
            event["kind"] == "approval_error" and event["payload"] == {"unsupported": True}
            for event in events
        )
        assert all(event["kind"] != "approval" for event in events)
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("sandbox", ["read_only", "workspace_write", "broad_access"])
@async_test
async def test_turn_sends_exact_sandbox_policy_without_model_provider_override(
    tmp_path: Path, sandbox: str
) -> None:
    runtime = await adapter(tmp_path)
    active = session(tmp_path, sandbox=sandbox, thread_id="thread-1")
    try:
        assert await runtime.start_turn(active, "local-turn-1", "hello") == "external-turn-1"
    finally:
        await runtime.aclose()


@async_test
async def test_approval_from_previous_process_generation_is_stale(tmp_path: Path) -> None:
    async def no_sleep(delay: float) -> None:
        del delay

    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        server_args=(str(FIXTURE), "adapter"),
        sleep=no_sleep,
        generation_factory=iter(("generation-a", "generation-b")).__next__,
    )
    await supervisor.start()
    runtime = CodexAppServerAdapter(supervisor)
    active = session(tmp_path, sandbox="broad_access", thread_id="thread-1")
    try:
        await runtime.start_turn(active, "local-turn-1", "hello")
        request_id = ""
        async for event in runtime.observe(active, "local-turn-1"):
            if event["kind"] == "approval":
                request_id = event["payload"]["request_id"]
        await supervisor.restart()

        with pytest.raises(RuntimeErrorInfo) as exc_info:
            await runtime.respond_approval(request_id, "decline")
        assert exc_info.value.code == "approval_stale"
    finally:
        await runtime.aclose()
