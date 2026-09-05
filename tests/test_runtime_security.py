from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from runtime_support import capabilities, open_runtime_db, runtime_session

from kairos_runtime import RuntimeEvent
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.host import _public_error
from kairos_runtime.redaction import public_error, sanitize_payload
from kairos_runtime.supervisor import CodexSupervisor, _runtime_environment
from kairos_runtime.wire import runtime_event_to_json
from kairos_state.repositories.runtime import RuntimeRepository

SENTINEL = "sk-runtime-security-sentinel"


def test_public_errors_and_unknown_payloads_never_echo_technical_details():
    assert public_error("transport") == {
        "code": "transport",
        "message": "falha de transporte do runtime",
        "retryable": True,
    }
    payload = sanitize_payload(
        "future_event",
        {"error": SENTINEL, "stderr": SENTINEL, "response": {"unknown": SENTINEL}},
    )
    assert payload == {}
    assert SENTINEL not in json.dumps(payload)
    safe = _public_error(RuntimeErrorInfo("transport", SENTINEL, True))
    assert safe.message == "falha de transporte do runtime"
    assert SENTINEL not in str(safe)


def test_runtime_event_wire_applies_payload_allowlist_without_mutilating_transcript():
    event = RuntimeEvent(
        1,
        "event-1",
        "session-1",
        "turn-1",
        1,
        "v1:session-1:1",
        "tool",
        {
            "threadId": "thread-1",
            "turnId": "external-turn-1",
            "item": {
                "id": "tool-1",
                "type": "commandExecution",
                "command": "printf safe",
                "aggregatedOutput": "safe tool output",
                "apiKey": SENTINEL,
                "unknown": SENTINEL,
            },
            "response": SENTINEL,
        },
    )
    serialized = runtime_event_to_json(event)
    encoded = json.dumps(serialized)
    assert serialized["payload"]["item"]["aggregatedOutput"] == "safe tool output"
    assert SENTINEL not in encoded


def test_repository_sanitizes_event_and_approval_before_database_write(tmp_path: Path):
    database = tmp_path / "state.db"
    connection = open_runtime_db(database)
    repository = RuntimeRepository(connection)
    session = runtime_session(tmp_path, "s1")
    repository.create_session(
        session,
        "test",
        allowed_directories=(str(tmp_path),),
    )
    repository.bind_thread("s1", "thread-1", capabilities("text", "approvals"))
    turn_id = repository.admit("s1", "one", "safe user content")
    repository.transition(turn_id, "queued", "starting")
    repository.dispatch(turn_id, "generation-1")
    repository.confirm_dispatch(turn_id, "external-1")
    repository.append(
        turn_id,
        "event-unsafe",
        "tool",
        {
            "item": {
                "id": "tool-1",
                "type": "commandExecution",
                "aggregatedOutput": "safe output",
                "authorization": SENTINEL,
            },
            "unknown": SENTINEL,
        },
    )
    repository.save_approval(
        turn_id,
        "generation-1",
        "request-1",
        "tool-1",
        {
            "request_id": "request-1",
            "rpc_request_id": 7,
            "item_id": "tool-1",
            "request_kind": "item/commandExecution/requestApproval",
            "details": {
                "threadId": "thread-1",
                "turnId": "external-1",
                "itemId": "tool-1",
                "command": "printf safe",
                "accessToken": SENTINEL,
            },
        },
    )
    connection.close()

    database_dump = database.read_bytes()
    assert SENTINEL.encode() not in database_dump
    assert b"safe output" in database_dump
    assert b"printf safe" in database_dump


def test_runtime_environment_removes_known_developer_credentials(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SENTINEL)
    monkeypatch.setenv("PATH", "/safe/bin")
    environment = _runtime_environment(str(tmp_path))
    assert environment["CODEX_HOME"] == str(tmp_path)
    assert environment["PATH"] == "/safe/bin"
    assert SENTINEL not in repr(environment)


def test_stderr_diagnostic_logs_only_category_and_bounded_byte_count(caplog):
    caplog.set_level(logging.WARNING)

    async def drain() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data((SENTINEL + "\n").encode())
        reader.feed_eof()
        supervisor = object.__new__(CodexSupervisor)
        supervisor._logger = logging.getLogger("test.runtime.stderr")
        await supervisor._drain_stderr(reader)

    asyncio.run(drain())
    captured_logs = caplog.text
    assert SENTINEL not in captured_logs
    assert "codex_stderr category=diagnostic bytes=" in captured_logs
