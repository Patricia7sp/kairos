from __future__ import annotations

import asyncio
import functools
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kairos_runtime.codex_rpc import CodexRpc
from kairos_runtime.errors import RuntimeErrorInfo

FIXTURE = Path(__file__).parent / "fixtures" / "codex_app_server.py"


def async_test(function: Callable[..., Any]) -> Callable[..., None]:
    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(function(*args, **kwargs))

    return run


async def rpc_process(mode: str = "rpc") -> tuple[asyncio.subprocess.Process, CodexRpc]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(FIXTURE),
        mode,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    rpc = CodexRpc(process.stdout, process.stdin, generation="generation-1")
    await rpc.start()
    return process, rpc


async def close_process(process: asyncio.subprocess.Process, rpc: CodexRpc) -> None:
    await rpc.aclose()
    if process.returncode is None:
        process.terminate()
    await process.wait()


@async_test
async def test_call_correlates_out_of_order_responses_and_preserves_notification() -> None:
    process, rpc = await rpc_process()
    subscription = rpc.subscribe("thread-1")
    try:
        slow = asyncio.create_task(rpc.call("slow", {}))
        fast = asyncio.create_task(rpc.call("fast", {}))

        assert await fast == {"value": "fast"}
        message = await subscription.get()
        assert message == {
            "method": "test/note",
            "params": {"threadId": "thread-1", "value": 3},
        }
        assert await slow == {"value": "slow"}
    finally:
        rpc.unsubscribe(subscription)
        await close_process(process, rpc)


@async_test
async def test_server_request_keeps_string_rpc_id_distinct_from_item_id() -> None:
    process, rpc = await rpc_process()
    subscription = rpc.subscribe("thread-1")
    try:
        assert await rpc.call("approval-test", {}) == {"ok": True}
        request = await subscription.get()
        assert request["id"] == "rpc-request-9"
        assert request["params"]["itemId"] == "item-9"
        assert request["params"]["approvalId"] == "approval-callback-9"
        await rpc.reply(request["id"], {"decision": "decline"})
    finally:
        rpc.unsubscribe(subscription)
        await close_process(process, rpc)


@pytest.mark.parametrize("mode", ["eof", "invalid-json", "oversized"])
@async_test
async def test_bad_or_closed_stdout_fails_pending_calls_with_safe_transport_error(
    mode: str,
) -> None:
    process, rpc = await rpc_process(mode)
    try:
        with pytest.raises(RuntimeErrorInfo) as exc_info:
            await rpc.call("trigger", {})
        assert exc_info.value.code == "transport"
        assert "broken" not in exc_info.value.message
        assert "x" * 100 not in exc_info.value.message
    finally:
        await close_process(process, rpc)


@async_test
async def test_process_exit_fails_subsequent_calls() -> None:
    process, rpc = await rpc_process("eof")
    try:
        with pytest.raises(RuntimeErrorInfo):
            await rpc.call("trigger", {})
        await process.wait()
        with pytest.raises(RuntimeErrorInfo) as exc_info:
            await rpc.call("after-exit", {})
        assert exc_info.value.code == "transport"
    finally:
        await close_process(process, rpc)


@async_test
async def test_unscoped_server_request_is_rejected_instead_of_waiting_forever() -> None:
    process, rpc = await rpc_process()
    try:
        result = await asyncio.wait_for(rpc.call("unscoped-request-test", {}), timeout=0.2)
        assert result == {"rejected": True}
    finally:
        await close_process(process, rpc)


@async_test
async def test_late_response_for_cancelled_call_does_not_poison_other_calls() -> None:
    process, rpc = await rpc_process()
    try:
        slow = asyncio.create_task(rpc.call("slow", {}))
        await asyncio.sleep(0)
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow

        assert await rpc.call("fast", {}) == {"value": "fast"}
        await asyncio.sleep(0)
        assert await rpc.call("check/replies", {}) == {"replyCount": 0}
    finally:
        await close_process(process, rpc)


@async_test
async def test_timed_out_interrupt_late_response_is_drained_without_resend() -> None:
    process, rpc = await rpc_process("late-interrupt")
    subscription = rpc.subscribe("thread-1")
    try:
        interrupt = asyncio.create_task(
            rpc.call("turn/interrupt", {"threadId": "thread-1", "turnId": "external-1"})
        )
        assert (await subscription.get())["method"] == "test/interruptPending"
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(interrupt, 0.01)
        assert await rpc.call("release-interrupt", {}) == {"interruptCount": 1}
        assert await rpc.call("check/replies", {}) == {"replyCount": 0}
    finally:
        rpc.unsubscribe(subscription)
        await close_process(process, rpc)
