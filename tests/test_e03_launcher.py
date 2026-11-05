import json
import os
import subprocess
import sys

import pytest


def test_temporary_server_refuses_without_flag_before_listening(tmp_path):
    environment = {key: value for key, value in os.environ.items() if key != "KAIROS_E03_TEMPORARY"}
    environment["KAIROS_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.e03.serve", "--port", "0"],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["error_kind"] == "temporary_profile_required"
    assert not (tmp_path / "state.db").exists()


def test_temporary_server_rejects_production_home(tmp_path):
    environment = {**os.environ, "KAIROS_E03_TEMPORARY": "1", "KAIROS_HOME": "/opt/data"}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.e03.serve", "--wait-profile-seconds", "0"],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["error_kind"] == "temporary_home_invalid"


def test_temporary_server_refuses_nonlocal_public_bind(tmp_path):
    environment = {**os.environ, "KAIROS_E03_TEMPORARY": "1", "KAIROS_HOME": str(tmp_path)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.e03.serve",
            "--host",
            "8.8.8.8",
            "--wait-profile-seconds",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["error_kind"] == "temporary_arguments_invalid"


def test_launch_requires_immutable_images_private_new_directory_and_no_inherited_secret(
    tmp_path, monkeypatch
):
    from scripts.e03.launcher import LaunchConfig

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = LaunchConfig("sha256:" + "1" * 64, "sha256:" + "2" * 64, tmp_path / "new")
    config.validate()
    with pytest.raises(ValueError):
        LaunchConfig("kairos:local", config.worker_image, tmp_path / "new").validate()
    with pytest.raises(ValueError):
        LaunchConfig(config.image, config.worker_image, tmp_path).validate()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-credential-never-use")
    with pytest.raises(ValueError):
        config.validate()
    assert not (tmp_path / "new").exists()


def test_cleanup_refuses_changed_identity_even_with_matching_name():
    from scripts.e03.launcher import require_owned

    row = {"Id": "a" * 64, "Config": {"Labels": {"io.kairos.e03.run_id": "b" * 32}}}
    require_owned(row, "a" * 64, "b" * 32)
    with pytest.raises(ValueError):
        require_owned(row, "c" * 64, "b" * 32)
    with pytest.raises(ValueError):
        require_owned(row, "a" * 64, "d" * 32)


def test_report_refuses_unknown_fields_and_receipt_not_in_successful_workload():
    from scripts.e03.launcher import validate_report

    raw = {
        "schema_version": 1,
        "run_id": "a" * 32,
        "client": {},
        "samples": [],
        "token": "must-not-export",
    }
    with pytest.raises(ValueError):
        validate_report(raw, "a" * 32, {}, {})
    raw.pop("token")
    report = validate_report(raw, "a" * 32, {}, {})
    assert report["gate"] == "inconclusive"
    assert report["gate_scope"] == "measurements_only"
    assert report["workload_state"] == "incomplete"


def test_workload_policy_preserves_kernel_contract_and_adds_run_ownership():
    from kairos_runtime.experimental.worker_policy import WorkerPolicy
    from scripts.e03.workloads import owned_worker_policy

    original = WorkerPolicy("sha256:" + "1" * 64, "kairos-worker-" + "2" * 32, True)
    policy = owned_worker_policy(original, "3" * 32)
    args = policy.create_args()
    assert args[-2:] == original.create_args()[-2:]
    assert (
        args[: args.index("--label")]
        == original.create_args()[: original.create_args().index("--label")]
    )
    assert "io.kairos.e03.run_id=" + "3" * 32 in args
    assert policy.tmpfs == original.tmpfs
    assert args.count("--network") == 1 and args[args.index("--network") + 1] == "none"


def test_report_requires_host_receipt_and_actual_exposure_for_each_clean_sample():
    from dataclasses import asdict

    from scripts.e03.launcher import validate_report
    from tests.test_phase2_benchmark import sample

    row = asdict(sample())
    raw = {"schema_version": 1, "run_id": "a" * 32, "client": {}, "samples": [row]}
    receipt = {
        (row["session_id"], row["call_id"]): {
            "id": row["persisted_message_id"],
            "status": "denied",
            "timestamp": 100,
        }
    }
    report = validate_report(
        raw, "a" * 32, receipt, {"ocioso": [{"start": 101, "end": 200, "passed": True}]}
    )
    assert report["workload_gaps"][0]["reason"] == "successful_workload_not_observed"
    assert report["gate"] == "inconclusive"


def test_resource_summary_detects_oom_counter_delta_even_without_docker_oom_flag():
    from scripts.e03.resources import summarize

    first = {
        "at": 1,
        "containers": {
            "kairos": {
                "running": True,
                "restarts": 0,
                "oom_killed": False,
                "cgroup": {"state": "observed", "memory_events": {"oom_kill": 0}},
            }
        },
    }
    last = {
        "at": 2,
        "containers": {
            "kairos": {
                "running": True,
                "restarts": 0,
                "oom_killed": False,
                "cgroup": {"state": "observed", "memory_events": {"oom_kill": 1}},
            }
        },
    }
    assert summarize([first, last])["state"] == "failed"
    assert summarize([first, last])["oom"] == ["kairos"]


def test_resource_summary_rejects_restart_even_when_oom_is_false():
    from copy import deepcopy

    from scripts.e03.resources import summarize

    first = {
        "at": 1,
        "containers": {
            "kairos": {
                "running": True,
                "restarts": 0,
                "image": "sha256:" + "1" * 64,
                "oom_killed": False,
                "cgroup": {"state": "observed", "memory_events": {"oom_kill": 0}},
            }
        },
    }
    last = deepcopy(first)
    last["at"] = 2
    last["containers"]["kairos"]["restarts"] = 1
    assert summarize([first, last])["state"] == "failed"


def test_fixed_workload_error_returns_explicit_failure(tmp_path, monkeypatch, capsys):
    from scripts.e03 import workloads

    monkeypatch.setattr(workloads.signal, "signal", lambda *_: None)

    def fail(*_):
        raise ValueError("private fixture detail must not reach report")

    monkeypatch.setattr(workloads, "cycle", fail)
    assert (
        workloads.main(
            [
                "--scenario",
                "auditoria",
                "--worker-image",
                "sha256:" + "1" * 64,
                "--run-id",
                "2" * 32,
                "--stop-file",
                str(tmp_path / "stop"),
            ]
        )
        == 1
    )
    row = json.loads(capsys.readouterr().out)
    assert row["passed"] is False and row["error_kind"] == "ValueError"
    assert "private" not in json.dumps(row)


def test_workload_deadline_terminates_real_owned_process_group(tmp_path, monkeypatch):
    import time

    from scripts.e03.launcher import LaunchConfig, TemporaryRun

    run = TemporaryRun(LaunchConfig("sha256:" + "1" * 64, "sha256:" + "2" * 64, tmp_path / "new"))
    run.directory.mkdir(mode=0o700)
    run.workload_log = (run.directory / "log").open("w")
    run.workload_stderr = (run.directory / "stderr").open("w")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    run.workload = process
    monkeypatch.setattr(run, "capture_resources", lambda: None)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="workload_shutdown_timeout"):
            run.finish_workload(timeout=0.05)
        assert process.poll() is not None
        assert time.monotonic() - started < 10
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_launcher_reads_public_message_created_at_for_workload_alignment(tmp_path, monkeypatch):
    from scripts.e03.launcher import LaunchConfig, TemporaryRun

    run = TemporaryRun(LaunchConfig("sha256:" + "1" * 64, "sha256:" + "2" * 64, tmp_path / "new"))
    sid, call = "e03-synthetic-" + "3" * 32, "e03-" + "4" * 32
    monkeypatch.setattr(
        run,
        "messages",
        lambda _: [
            {
                "id": 7,
                "role": "tool",
                "tool_call_id": call,
                "is_error": True,
                "created_at": 123.5,
                "content": '{"status":"denied"}',
            }
        ],
    )
    proof = run.receipts({"samples": [{"session_id": sid}]})
    assert proof[(sid, call)]["timestamp"] == 123.5


def test_resource_barrier_refuses_snapshot_during_worker_lifecycle(tmp_path):
    from scripts.e03.resources import lifecycle_barrier

    with (
        lifecycle_barrier(tmp_path / "resources.lock"),
        pytest.raises(TimeoutError),
        lifecycle_barrier(tmp_path / "resources.lock", timeout=0.03),
    ):
        pytest.fail("snapshot entrou durante alteração do worker")


def test_server_mounts_refuse_other_sources_even_if_readonly(tmp_path):
    from scripts.e03.launcher import validate_mounts

    root = tmp_path / "profile"
    mounts = [
        {"Type": "bind", "Source": str(root / source), "Destination": destination, "RW": False}
        for source, destination in [
            ("code", "/opt/kairos/scripts/e03"),
            ("code/ui", "/opt/kairos/kairos_web/ui/e03"),
            ("public", "/opt/kairos/kairos_web/ui/e03-run"),
        ]
    ]
    validate_mounts(mounts, root)
    mounts[0]["Source"] = "/opt/production-data"
    with pytest.raises(ValueError):
        validate_mounts(mounts, root)
