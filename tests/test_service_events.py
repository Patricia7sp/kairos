"""The operational journal is durable, bounded, and excludes freeform data."""

import importlib
import json
import multiprocessing
import os
import stat
from pathlib import Path

import pytest


def module():
    return importlib.import_module("kairos_observability.service_events")


def journal(home):
    return home / "logs" / "service-events.jsonl"


def seed(home, data, *, rotated=False):
    directory = home / "logs"
    directory.mkdir(mode=0o700, exist_ok=True)
    target = journal(home).with_suffix(".jsonl.1") if rotated else journal(home)
    target.write_bytes(data)
    target.chmod(0o600)
    return target


def encoded(*, code="chat.completed", timestamp="2026-09-12T12:00:00.000000Z", counters=None):
    return (
        json.dumps({"timestamp": timestamp, "code": code, "counters": counters or {}}) + "\n"
    ).encode()


def test_record_is_durable_and_public_text_is_derived(tmp_path):
    events = module()
    assert events.record_service_event(tmp_path, "chat.completed", api_calls=2, input_tokens=10)
    raw = json.loads(journal(tmp_path).read_text())
    assert set(raw) == {"timestamp", "code", "counters"}
    assert raw["counters"] == {"api_calls": 2, "input_tokens": 10}
    result = events.read_service_events(tmp_path)
    assert result["state"] == "ready"
    assert result["source"] == "service-events"
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["service"] == "chat"
    assert event["level"] == "info"
    assert event["message"]
    assert event["timestamp"].endswith("Z")
    assert event["counters"] == {"api_calls": 2, "input_tokens": 10}
    reopened = importlib.reload(events).read_service_events(tmp_path)
    assert reopened == result
    assert stat.S_IMODE((tmp_path / "logs").stat().st_mode) == 0o700
    assert stat.S_IMODE(journal(tmp_path).stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("code", "service", "level"),
    [
        ("web.started", "web", "info"),
        ("web.stopped", "web", "info"),
        ("chat.completed", "chat", "info"),
        ("chat.failed", "chat", "error"),
        ("chat.cancelled", "chat", "warning"),
        ("search.completed", "search", "info"),
        ("search.failed", "search", "error"),
        ("cron.completed", "cron", "info"),
        ("cron.failed", "cron", "error"),
        ("cron.unknown", "cron", "warning"),
    ],
)
def test_catalog_accepts_only_defined_service_events(tmp_path, code, service, level):
    events = module()
    assert events.record_service_event(tmp_path, code)
    event = events.read_service_events(tmp_path)["events"][0]
    assert (event["service"], event["level"]) == (service, level)


def test_meta_round_trips_and_old_journals_read_as_empty(tmp_path):
    events = module()
    assert events.record_service_event(
        tmp_path,
        "chat.completed",
        api_calls=2,
        meta={
            "origin": "web",
            "call_type": "chat",
            "status": "completed",
            "duration_ms": 150,
            "conversation_id": "conv-001",
        },
    )
    raw = json.loads(journal(tmp_path).read_text())
    assert set(raw) == {"timestamp", "code", "counters", "meta"}
    assert raw["meta"] == {
        "origin": "web",
        "call_type": "chat",
        "status": "completed",
        "duration_ms": 150,
        "conversation_id": "conv-001",
    }
    assert events.read_service_events(tmp_path)["events"][0]["meta"] == raw["meta"]

    old = seed(tmp_path, encoded(code="web.started"), rotated=True)
    assert old.exists()
    result = events.read_service_events(tmp_path)
    assert result["events"][-1]["meta"] == {}


@pytest.mark.parametrize(
    "meta",
    [
        {"unknown_key": "private-sentinel"},
        {"origin": ""},
        {"origin": " "},
        {"origin": "x" * 25},
        {"call_type": "x" * 33},
        {"status": "x" * 25},
        {"model": "x" * 129},
        {"conversation_id": "x" * 129},
        {"tool": "x" * 65},
        {"error": "x" * 97},
        {"duration_ms": "150"},
        {"duration_ms": -1},
        {"duration_ms": 86_400_001},
        {"duration_ms": 150.5},
        {"origin": 7},
        {"conversation_id": None},
        "private-sentinel",
        5,
        {"origin": True},
    ],
    ids=[
        "unknown-key",
        "empty-origin",
        "blank-origin",
        "oversized-origin",
        "oversized-call-type",
        "oversized-status",
        "oversized-model",
        "oversized-conversation-id",
        "oversized-tool",
        "oversized-error",
        "string-duration",
        "negative-duration",
        "oversized-duration",
        "float-duration",
        "int-for-string",
        "none-value",
        "non-dict-string",
        "non-dict-int",
        "bool-for-string",
    ],
)
def test_invalid_meta_is_rejected_without_creating_files(tmp_path, meta):
    assert module().record_service_event(tmp_path, "chat.completed", meta=meta) is False
    assert list(tmp_path.iterdir()) == []


def test_corrupt_meta_record_is_explicit_and_never_exposes_raw_lines(tmp_path):
    seed(
        tmp_path,
        b'{"timestamp":"2026-09-12T12:00:00Z","code":"web.started","counters":{},'
        b'"meta":{"unknown_key":"private-sentinel"}}\n',
    )
    result = module().read_service_events(tmp_path)
    assert result["state"] == "error"
    assert result["events"] == []
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    ("code", "counters"),
    [
        ("private-exception-and-url", {}),
        ("web.started", {"message": "private-prompt"}),
        ("chat.completed", {"api_calls": True}),
        ("chat.completed", {"input_tokens": -1}),
        ("chat.completed", {"output_tokens": 1_000_000_001}),
        ("search.completed", {"results": "private-token"}),
        ("search.completed", {"results": 2.0}),
        ("chat.failed", {"exception": "private-stack-trace"}),
        ("web.started", {"api_calls": None}),
    ],
)
def test_invalid_record_is_rejected_without_creating_files(tmp_path, code, counters):
    assert module().record_service_event(tmp_path, code, **counters) is False
    assert list(tmp_path.iterdir()) == []


def test_approved_counter_extremes_are_persisted(tmp_path):
    events = module()
    assert events.record_service_event(
        tmp_path,
        "chat.completed",
        api_calls=0,
        input_tokens=1_000_000_000,
        output_tokens=1,
        results=5,
    )
    assert events.read_service_events(tmp_path)["events"][0]["counters"] == {
        "api_calls": 0,
        "input_tokens": 1_000_000_000,
        "output_tokens": 1,
        "results": 5,
    }


def test_missing_source_and_valid_empty_source_are_distinct_and_read_only(tmp_path):
    events = module()
    absent_home = tmp_path / "absent"
    assert events.read_service_events(absent_home)["state"] == "unavailable"
    assert not absent_home.exists()
    assert events.read_service_events(tmp_path)["state"] == "unavailable"
    assert list(tmp_path.iterdir()) == []
    seed(tmp_path, b"")
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "logs").iterdir()}
    assert events.read_service_events(tmp_path) == {
        "state": "ready",
        "events": [],
        "source": "service-events",
    }
    assert before == {p: p.stat().st_mtime_ns for p in (tmp_path / "logs").iterdir()}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["logs"]


def test_filters_and_limit_apply_to_newest_timestamps_across_rotation(tmp_path):
    events = module()
    seed(tmp_path, encoded(code="web.started", timestamp="2026-09-12T12:00:02Z"), rotated=True)
    seed(
        tmp_path,
        encoded(code="chat.failed", timestamp="2026-09-12T12:00:01Z")
        + encoded(code="chat.cancelled", timestamp="2026-09-12T12:00:03Z")
        + encoded(code="cron.failed", timestamp="2026-09-12T12:00:04Z"),
    )
    assert [e["code"] for e in events.read_service_events(tmp_path, limit=2)["events"]] == [
        "cron.failed",
        "chat.cancelled",
    ]
    assert [e["code"] for e in events.read_service_events(tmp_path, service="chat")["events"]] == [
        "chat.cancelled",
        "chat.failed",
    ]
    assert [e["code"] for e in events.read_service_events(tmp_path, level="error")["events"]] == [
        "cron.failed",
        "chat.failed",
    ]
    assert events.read_service_events(tmp_path, service="search")["events"] == []


@pytest.mark.parametrize(
    "options",
    [
        {"limit": 0},
        {"limit": 201},
        {"limit": True},
        {"limit": 1.5},
        {"limit": "5"},
        {"service": "../../private"},
        {"level": "debug"},
        {"service": []},
        {"level": False},
    ],
)
def test_invalid_read_filters_return_safe_error_without_io(tmp_path, options):
    result = module().read_service_events(tmp_path, **options)
    assert result["state"] == "error"
    assert result["events"] == []
    assert result["error"]
    assert "private" not in json.dumps(result)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "data",
    [
        b"private-token-and-url\n",
        b"{}\n",
        b"[]\n",
        b"\xff\n",
        b"\n",
        encoded()[:-1],
        encoded(code="private-upstream-error"),
        encoded(timestamp="private-path"),
        encoded(timestamp="2026-09-12T12:00:00"),
        encoded(counters={"results": True}),
        encoded(counters={"results": -1}),
        encoded(counters={"prompt": "private-sentinel"}),
        b'{"timestamp":"2026-09-12T12:00:00Z","code":"web.started","counters":{},"message":"private-sentinel"}\n',
        b'{"timestamp":"2026-09-12T12:00:00Z","code":"web.started","code":"web.stopped","counters":{}}\n',
        b" " * 4096 + b"\n",
        b" " * (512 * 1024 + 1),
    ],
    ids=[
        "garbage",
        "empty-object",
        "array",
        "bad-utf8",
        "blank-line",
        "partial-line",
        "unknown-code",
        "bad-time",
        "naive-time",
        "bool-counter",
        "negative-counter",
        "secret-counter",
        "extra-field",
        "duplicate-field",
        "oversized-line",
        "oversized-file",
    ],
)
def test_corruption_is_explicit_and_never_exposes_raw_lines(tmp_path, data):
    seed(tmp_path, data)
    result = module().read_service_events(tmp_path)
    assert result["state"] == "error"
    assert result["events"] == []
    assert result["error"]
    assert "private" not in json.dumps(result)


def test_rotated_corruption_is_not_hidden_by_current_limit(tmp_path):
    seed(tmp_path, b"private-token\n", rotated=True)
    seed(tmp_path, encoded())
    assert module().read_service_events(tmp_path, limit=1)["state"] == "error"


@pytest.mark.parametrize("target", ["directory", "current", "rotated"])
def test_symlinks_are_rejected_without_reading_or_modifying_target(tmp_path, target):
    events = module()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_file = outside / "secret"
    secret_file.write_text("private-sentinel")
    home = tmp_path / "home"
    home.mkdir()
    if target == "directory":
        (home / "logs").symlink_to(outside, target_is_directory=True)
    else:
        seed(home, b"")
        path = journal(home) if target == "current" else journal(home).with_suffix(".jsonl.1")
        path.unlink(missing_ok=True)
        path.symlink_to(secret_file)
    result = events.read_service_events(home)
    assert result["state"] == "error"
    assert "private" not in json.dumps(result)
    assert events.record_service_event(home, "web.started") is False
    assert secret_file.read_text() == "private-sentinel"
    assert list(outside.iterdir()) == [secret_file]


@pytest.mark.parametrize("unsafe", ["permissions", "hardlink", "fifo", "directory"])
def test_unsafe_journal_files_are_rejected(tmp_path, unsafe):
    events = module()
    path = seed(tmp_path, encoded())
    if unsafe == "permissions":
        path.chmod(0o644)
    elif unsafe == "hardlink":
        os.link(path, tmp_path / "shared")
    else:
        path.unlink()
        if unsafe == "fifo":
            os.mkfifo(path, 0o600)
        else:
            path.mkdir()
    assert events.read_service_events(tmp_path)["state"] == "error"
    assert events.record_service_event(tmp_path, "web.started") is False


def test_rotation_limits_two_files_and_keeps_recent_events(tmp_path):
    events = module()
    # Real 512 KiB boundary, including final newline; no private limit monkeypatch.
    line = encoded()
    data = line * ((512 * 1024) // len(line))
    seed(tmp_path, data)
    assert events.record_service_event(tmp_path, "web.started", api_calls=1_000_000_000)
    assert journal(tmp_path).with_suffix(".jsonl.1").exists()
    assert events.read_service_events(tmp_path, limit=1)["events"][0]["code"] == "web.started"
    seed(tmp_path, data)
    assert events.record_service_event(tmp_path, "web.stopped", api_calls=1_000_000_000)
    paths = sorted((tmp_path / "logs").iterdir())
    assert [p.name for p in paths] == ["service-events.jsonl", "service-events.jsonl.1"]
    assert all(p.stat().st_size <= 512 * 1024 for p in paths)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in paths)
    result = events.read_service_events(tmp_path, limit=200)
    assert len(result["events"]) == 200
    assert result["events"][0]["code"] == "web.stopped"


def process_writer(home, offset):
    events = module()
    for index in range(80):
        if not events.record_service_event(Path(home), "chat.completed", api_calls=offset + index):
            raise RuntimeError("journal write failed")


def test_two_processes_append_without_lost_or_interleaved_records(tmp_path):
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=process_writer, args=(str(tmp_path), offset)) for offset in (0, 1000)
    ]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
    result = module().read_service_events(tmp_path, limit=200)
    assert result["state"] == "ready"
    assert len(result["events"]) == 160
    assert {e["counters"]["api_calls"] for e in result["events"]} == set(range(80)) | set(
        range(1000, 1080)
    )


def test_write_failures_never_raise_or_include_exception_data(tmp_path, monkeypatch):
    events = module()

    def fail(*args, **kwargs):
        raise OSError("private-path-and-auth")

    monkeypatch.setattr(events.os, "write", fail)
    assert events.record_service_event(tmp_path, "web.started") is False


def test_lock_contention_is_bounded_and_does_not_change_journal(tmp_path):
    import fcntl
    import time

    events = module()
    path = seed(tmp_path, encoded())
    original = path.read_bytes()
    descriptor = os.open(tmp_path / "logs", os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        assert events.record_service_event(tmp_path, "web.started") is False
        written_elapsed = time.monotonic() - started
        started = time.monotonic()
        result = events.read_service_events(tmp_path)
        read_elapsed = time.monotonic() - started
        assert result["state"] == "error"
        assert result["error"]
        assert 0.20 <= written_elapsed < 1.0
        assert 0.20 <= read_elapsed < 1.0
        assert path.read_bytes() == original
    finally:
        os.close(descriptor)


def test_rotated_only_source_can_be_read_without_recreating_current(tmp_path):
    seed(tmp_path, encoded(), rotated=True)
    result = module().read_service_events(tmp_path)
    assert result["state"] == "ready"
    assert len(result["events"]) == 1
    assert not journal(tmp_path).exists()


def test_equal_timestamps_keep_most_recent_append_first(tmp_path):
    seed(tmp_path, encoded(code="web.started"), rotated=True)
    seed(tmp_path, encoded(code="chat.completed") + encoded(code="web.stopped"))
    assert [e["code"] for e in module().read_service_events(tmp_path)["events"]] == [
        "web.stopped",
        "chat.completed",
        "web.started",
    ]


def test_partial_writes_are_completed_without_corrupting_record(tmp_path, monkeypatch):
    events = module()
    write = events.os.write
    monkeypatch.setattr(events.os, "write", lambda fd, data: write(fd, data[:7]))
    assert events.record_service_event(tmp_path, "web.started") is True
    result = events.read_service_events(tmp_path)
    assert result["state"] == "ready"
    assert result["events"][0]["code"] == "web.started"


@pytest.mark.parametrize("rotated", [False, True])
def test_oversized_existing_file_prevents_write_and_is_not_truncated(tmp_path, rotated):
    path = seed(tmp_path, b" " * (512 * 1024 + 1), rotated=rotated)
    assert module().record_service_event(tmp_path, "web.started") is False
    assert path.stat().st_size == 512 * 1024 + 1


def test_async_writer_keeps_event_loop_responsive_under_real_flock(tmp_path):
    import asyncio
    import fcntl

    events = module()
    seed(tmp_path, b"")
    descriptor = os.open(tmp_path / "logs", os.O_RDONLY | os.O_DIRECTORY)

    async def scenario():
        task = asyncio.create_task(events.record_service_event_async(tmp_path, "web.started"))
        await asyncio.sleep(0.04)
        assert not task.done(), "lock acquisition must run outside the event loop"
        assert await task is False

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(scenario())
    finally:
        os.close(descriptor)


def test_async_writer_finishes_inflight_write_before_repeated_cancellation_returns(
    tmp_path, monkeypatch
):
    import asyncio
    import fcntl
    import threading

    events = module()
    seed(tmp_path, b"")
    entered, completed = threading.Event(), threading.Event()
    real_record = events.record_service_event

    def observed_record(*args, **kwargs):
        entered.set()
        try:
            return real_record(*args, **kwargs)
        finally:
            completed.set()

    monkeypatch.setattr(events, "record_service_event", observed_record)
    descriptor = os.open(tmp_path / "logs", os.O_RDONLY | os.O_DIRECTORY)

    async def scenario():
        task = asyncio.create_task(events.record_service_event_async(tmp_path, "web.started"))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert not completed.is_set()
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()
        assert events.read_service_events(tmp_path)["events"][0]["code"] == "web.started"

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(scenario())
    finally:
        os.close(descriptor)


def test_async_writer_preserves_awaited_order_and_failure_result(tmp_path):
    import asyncio

    events = module()

    async def scenario():
        assert await events.record_service_event_async(tmp_path, "web.started") is True
        assert await events.record_service_event_async(tmp_path, "web.stopped") is True
        assert await events.record_service_event_async(tmp_path, "invalid-secret-code") is False
        assert [e["code"] for e in events.read_service_events(tmp_path)["events"]] == [
            "web.stopped",
            "web.started",
        ]

    asyncio.run(scenario())
