"""Exercise served reducers with the journal emitted by the real service."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from test_codex_adapter import async_test
from test_runtime_approvals import pending_approval
from test_runtime_service import setup_service

from kairos_runtime import RuntimeErrorInfo, runtime_event_to_json


def project_journal(events, session_id):
    module = Path("kairos_web/ui/js/views/runtime.js").resolve().as_uri()
    script = f"""
        import {{ initialRuntimeState, reduceRuntime }} from {json.dumps(module)};
        let input = '';
        for await (const chunk of process.stdin) input += chunk;
        const {{events, session}} = JSON.parse(input);
        const states = [];
        let state = initialRuntimeState(session);
        for (const event of events) {{ state = reduceRuntime(state, event); states.push(state); }}
        console.log(JSON.stringify(states));
    """
    result = subprocess.run(
        ["node", "--experimental-default-type=module", "--input-type=module", "-e", script],
        input=json.dumps(
            {"session": session_id, "events": [runtime_event_to_json(e) for e in events]}
        ),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


@async_test
async def test_rejected_accept_remains_declineable_in_real_journal_replay(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        approval = await pending_approval(service, runtime)
        with pytest.raises(RuntimeErrorInfo, match="invalid_policy"):
            await service.decide("s1", approval, "accept")
        journal = await store.events_after("s1", None)
        rejected = project_journal(journal, "s1")[-1]
        assert rejected["approvals"][0]["id"] == approval
        assert rejected["approvals"][0]["rejection"]
        await service.decide("s1", approval, "decline")
        assert project_journal(await store.events_after("s1", None), "s1")[-1]["approvals"] == []
        assert runtime.replies == [("process-1:string:7", "decline")]
    finally:
        await asyncio.wait_for(service.aclose(), 2)


@async_test
async def test_project_queue_replay_targets_real_turn_for_cancel_without_external_send(tmp_path):
    service, store, runtime = await setup_service(tmp_path)
    try:
        await pending_approval(service, runtime)
        await service.create(str(tmp_path), "workspace_write", session_id="s2")
        queued = await service.submit("s2", "keep this instruction", "second")
        state = project_journal(await store.events_after("s2", None), "s2")[-1]
        assert state["cancellableTurnId"] == queued
        assert state["activeTurnId"] is None
        await service.cancel("s2", state["cancellableTurnId"])
        state = project_journal(await store.events_after("s2", None), "s2")[-1]
        assert state["cancellableTurnId"] is None
        assert state["queue"] == []
        assert len(runtime.starts) == 1
        assert (await store.get_turn(queued))["send_state"] == "not_sent"
    finally:
        await asyncio.wait_for(service.aclose(), 2)


@async_test
async def test_human_chat_consumes_real_service_replacement_and_reconciliation(tmp_path, capsys):
    from kairos_cli.chat import _run_turn

    service, _store, runtime = await setup_service(tmp_path)

    class Router:
        async def stream(self, envelope, *, on_runtime_accepted):
            turn = await service.submit("s1", envelope.content, "cli")
            on_runtime_accepted(turn)
            async for event in service.subscribe("s1"):
                yield event
                if event.kind == "turn_end":
                    return

    try:
        await runtime.events.put({"kind": "text", "payload": {"itemId": "answer", "delta": "ha"}})
        await runtime.events.put({"kind": "text", "payload": {"itemId": "answer", "delta": "ha"}})
        await runtime.events.put(
            {
                "kind": "text",
                "payload": {
                    "item": {"id": "answer", "type": "agentMessage", "text": "haha final"},
                    "replace": True,
                },
            }
        )
        await runtime.events.put(
            {
                "kind": "tool",
                "payload": {
                    "item": {
                        "id": "tool",
                        "type": "commandExecution",
                        "command": "pwd",
                        "status": "completed",
                    },
                },
            }
        )
        await runtime.events.put(
            {
                "kind": "snapshot",
                "payload": {
                    "state": "completed",
                    "items": [
                        {"id": "answer", "type": "agentMessage", "text": "snapshot final"},
                    ],
                },
            }
        )
        assert (
            await _run_turn(
                Router(),
                session_id="s1",
                content="hello",
                override=None,
                as_json=False,
                runtime_session=True,
            )
            == 0
        )
        output = capsys.readouterr().out
        assert "haha" in output and "haha final" in output
        assert "snapshot final" in output
        assert "pwd" in output and "completed" in output and "queued" in output
    finally:
        await asyncio.wait_for(service.aclose(), 2)
