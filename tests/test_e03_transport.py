import json
import socket
import threading
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from scripts.e03 import launcher
from tests.test_phase2_benchmark import sample


def test_startup_refusal_is_retried_before_using_the_bridge(monkeypatch):
    with socket.socket() as upstream:
        upstream.bind(("127.0.0.1", 0))
        forward = launcher.LoopbackForward("127.0.0.1", upstream.getsockname()[1])
        connect = socket.create_connection

        def start_after_real_refusal(*args, **kwargs):
            try:
                return connect(*args, **kwargs)
            except ConnectionRefusedError:
                upstream.listen()
                raise

        try:
            monkeypatch.setattr(socket, "create_connection", start_after_real_refusal)
            forward.wait_until_ready(timeout=2)
            result = forward.summary()
            assert result["startup_connection_refusals"] == 1
            assert result["failures"] == 0
            assert result["state"] == "observed"
        finally:
            forward.close()


def test_unavailable_upstream_has_explicit_startup_failure():
    with socket.socket() as upstream:
        upstream.bind(("127.0.0.1", 0))
        forward = launcher.LoopbackForward("127.0.0.1", upstream.getsockname()[1])
        try:
            with pytest.raises(RuntimeError, match="temporary_upstream_timeout"):
                forward.wait_until_ready(timeout=0.05)
            assert forward.summary()["startup_connection_refusals"] >= 1
            assert forward.summary()["state"] == "unavailable"
        finally:
            forward.close()


def test_runtime_connect_failure_is_logged_without_connection_content(caplog):
    recorded = threading.Event()

    class ObservedForward(launcher.LoopbackForward):
        def handle_error(self, request, client_address):
            try:
                super().handle_error(request, client_address)
            finally:
                recorded.set()

    with socket.socket() as upstream:
        upstream.bind(("127.0.0.1", 0))
        upstream.listen()
        forward = ObservedForward("127.0.0.1", upstream.getsockname()[1])
        try:
            forward.wait_until_ready(timeout=1)
            upstream.close()
            with socket.create_connection(forward.server_address, timeout=1) as client:
                client.sendall(b"synthetic-private-connection-content")
                assert recorded.wait(2)
            result = forward.summary()
            assert result["failures"] == 1
            assert result["state"] == "failed"
            assert result["errors"] == [
                {"operation": "connect", "error_kind": "ConnectionRefusedError", "count": 1}
            ]
            assert len(caplog.records) == 1
            assert caplog.records[0].error_kind == "ConnectionRefusedError"
            assert caplog.records[0].operation == "connect"
            assert "synthetic-private" not in caplog.text + json.dumps(result)
        finally:
            forward.close()


def test_unexpected_exception_never_exports_its_message_or_custom_class(caplog):
    class PrivateException(RuntimeError):
        pass

    forward = launcher.LoopbackForward("127.0.0.1", 1)
    try:
        try:
            raise PrivateException("synthetic-private-exception-content")
        except PrivateException:
            forward.handle_error(None, None)
        result = forward.summary()
        assert result["state"] == "failed"
        assert result["errors"] == [
            {"operation": "unknown", "error_kind": "unexpected", "count": 1}
        ]
        exported = json.dumps(result) + caplog.text + str(vars(caplog.records[0]))
        assert "synthetic-private" not in exported
        assert "PrivateException" not in exported
    finally:
        forward.close()


@pytest.mark.parametrize("failures,rejections", [(0, 0), (1, 0), (0, 1)])
def test_report_requires_successful_transport_even_when_all_measurements_pass(
    tmp_path, monkeypatch, failures, rejections
):
    run = launcher.TemporaryRun(
        launcher.LaunchConfig("sha256:" + "1" * 64, "sha256:" + "2" * 64, tmp_path / "run")
    )
    run.directory.mkdir()
    run.public.mkdir()
    run.container = "3" * 64
    transport = {
        "state": "failed" if failures or rejections else "observed",
        "failures": failures,
        "rejections": rejections,
        "startup_connection_refusals": 4,
        "errors": [],
    }
    run.forward = SimpleNamespace(failures=failures, rejected=rejections, summary=lambda: transport)
    run.kernel_attestation = {}
    run.stats = [
        {
            "at": 1,
            "containers": {
                "kairos": {
                    "running": True,
                    "oom_killed": False,
                    "cgroup": {"state": "observed", "memory_events": {"oom_kill": 0}},
                }
            },
        }
    ]
    rows = []
    receipts = {}
    for i in range(150):
        call_id = "e03-" + f"{i:032x}"
        row = asdict(
            sample(
                scenario=launcher.SCENARIOS[i // 30],
                approval_id=f"{i:032x}",
                call_id=call_id,
                persisted_call_id=call_id,
                persisted_message_id=i + 1,
            )
        )
        rows.append(row)
        receipts[(row["session_id"], call_id)] = {"id": i + 1, "timestamp": 1, "status": "denied"}
    run.intervals = {case: [{"start": 0, "end": 2, "passed": True}] for case in launcher.SCENARIOS}
    monkeypatch.setattr(run, "capture_resources", lambda: None)
    monkeypatch.setattr(run, "receipts", lambda _: receipts)
    monkeypatch.setattr(run, "sessions", lambda: [{"title": "fixture:pressure", "id": "pressure"}])
    monkeypatch.setattr(
        run,
        "messages",
        lambda _: [
            {"role": "user", "created_at": 0, "content": "e03-synthetic:painel-inicio"},
            {"role": "user", "created_at": 2, "content": "e03-synthetic:painel-fim"},
        ],
    )

    def command(*args):
        if args[0] == "git":
            return "a" * 40
        assert args[:2] == ("docker", "ps")
        return run.container or ""

    monkeypatch.setattr(launcher, "command", command)
    run.finalize_report({"schema_version": 1, "run_id": run.run_id, "client": {}, "samples": rows})
    report = json.loads((run.directory / "report.json").read_text())
    if failures or rejections:
        assert report["gate"] == "inconclusive"
    else:
        assert report["gate"] == "passed"
    assert report["transport"] == transport
    assert all(case["clean_denies"] == 30 for case in report["scenarios"].values())
    if not failures and not rejections:
        info = run.directory.stat()
        run.directory_identity = (info.st_dev, info.st_ino)
        run.container = None
        run.forward.close = lambda: transport.update(state="failed", failures=1)
        run.cleanup()
        final = json.loads((run.directory / "report.json").read_text())
        assert final["gate"] == "inconclusive"
        assert final["transport"]["failures"] == 1
        assert final["teardown_state"] == "complete"
