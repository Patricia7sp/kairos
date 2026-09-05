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
        version_args=(str(FIXTURE), "version"),
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
async def test_attach_registers_before_resume_and_keeps_exact_recovered_turn(tmp_path):
    runtime = await adapter(tmp_path, "attach")
    active = session(tmp_path, thread_id="thread-1")
    try:
        snapshot = await runtime.attach_turn(active, "recovered-local", "external-turn-1")
        assert snapshot.external_turn_id == "external-turn-1"
        events = [event async for event in runtime.observe(active, "recovered-local")]
        assert [event["kind"] for event in events] == ["text", "snapshot", "turn"]
        assert events[0]["payload"]["delta"] == "during resume"
        assert all(event["generation"] == runtime.generation for event in events)
    finally:
        await runtime.aclose()


@async_test
async def test_terminal_attachment_snapshot_finishes_observer_without_notification(tmp_path):
    runtime = await adapter(tmp_path, "attach-terminal-only")
    active = session(tmp_path, thread_id="thread-1")
    try:
        snapshot = await runtime.attach_turn(active, "recovered-local", "external-turn-1")
        assert snapshot.state == "completed"
        async with asyncio.timeout(0.3):
            events = [event async for event in runtime.observe(active, "recovered-local")]
        assert [event["kind"] for event in events] == ["snapshot"]
        assert events[0]["payload"]["items"][0]["text"] == "completed while attaching"
        assert events[0]["generation"] == runtime.generation
    finally:
        await runtime.aclose()


@async_test
async def test_snapshot_barrier_precedes_delta_in_same_response_batch(tmp_path):
    from kairos_runtime.contracts import RuntimeEvent
    from kairos_runtime.recovery import TranscriptProjection

    runtime = await adapter(tmp_path, "attach-barrier")
    active = session(tmp_path, thread_id="thread-1")
    projection = TranscriptProjection()
    try:
        await runtime.attach_turn(active, "recovered-local", "external-turn-1")
        events = [event async for event in runtime.observe(active, "recovered-local")]
        assert [event["kind"] for event in events] == ["text", "snapshot", "text", "turn"]
        for index, event in enumerate(events, start=1):
            kind = "reconciled" if event["kind"] == "snapshot" else event["kind"]
            payload = {"snapshot": event["payload"]} if kind == "reconciled" else event["payload"]
            projection.apply(
                RuntimeEvent(1, str(index), "s1", "t1", index, f"cursor-{index}", kind, payload)
            )
        assert projection.content == "during resume after response"
    finally:
        await runtime.aclose()


@pytest.mark.parametrize(
    "mode,code", [("missing-thread", "thread_missing"), ("invalid-resume", "transport")]
)
@async_test
async def test_missing_rollout_remote_rejection_is_specific_and_transport_stays_alive(
    tmp_path, mode, code
):
    runtime = await adapter(tmp_path, mode)
    try:
        with pytest.raises(RuntimeErrorInfo) as caught:
            await runtime.resume_thread(session(tmp_path, thread_id="thread-missing"))
        assert caught.value.code == code
        assert "sensitive" not in str(caught.value)
        assert await runtime.create_thread(session(tmp_path)) == "thread-1"
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
                break
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


@async_test
async def test_unknown_scoped_request_is_rejected_before_turn_filter(tmp_path: Path) -> None:
    runtime = await adapter(tmp_path, "unknown-request-other-turn")
    active = session(tmp_path, thread_id="thread-1")
    try:
        await runtime.start_turn(active, "local-turn-1", "hello")
        events = [event async for event in runtime.observe(active, "local-turn-1")]
        assert any(event["kind"] == "approval_error" for event in events)
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
        version_args=(str(FIXTURE), "version"),
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


@async_test
async def test_queued_old_approval_keeps_origin_and_never_replies_to_replacement(
    tmp_path: Path,
) -> None:
    async def no_sleep(delay: float) -> None:
        del delay

    supervisor = CodexSupervisor(
        codex_home=str(tmp_path),
        executable=sys.executable,
        version_args=(str(FIXTURE), "version"),
        server_args=(str(FIXTURE), "adapter"),
        sleep=no_sleep,
        generation_factory=iter(("generation-a", "generation-b")).__next__,
    )
    await supervisor.start()
    runtime = CodexAppServerAdapter(supervisor)
    active = session(tmp_path, sandbox="broad_access", thread_id="thread-1")
    try:
        await runtime.start_turn(active, "local-turn-1", "hello")
        await supervisor.restart()

        approval = None
        async for event in runtime.observe(active, "local-turn-1"):
            if event["kind"] == "approval":
                approval = event
                break
        assert approval is not None
        assert approval["generation"] == "generation-a"
        assert approval["payload"]["request_id"].startswith("generation-a:")

        snapshot = await runtime.inspect_turn(active, "external-turn-1")
        assert snapshot.pending_requests[0]["generation"] == "generation-a"
        with pytest.raises(RuntimeErrorInfo) as exc_info:
            await runtime.respond_approval(approval["payload"]["request_id"], "accept")
        assert exc_info.value.code == "approval_stale"
        assert await supervisor.rpc.call("check/replies", {}) == {"replyCount": 0}
    finally:
        await runtime.aclose()
