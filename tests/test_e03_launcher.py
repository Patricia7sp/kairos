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


def test_audit_workload_exercises_findings_from_inert_fixture(tmp_path):
    from kairos_security.checks.source_code import scan_source_tree
    from scripts.e03.workloads import audit_fixture

    details = audit_fixture(tmp_path)
    assert details["audits"] > 0
    assert details["findings"] > 0
    assert "SEC-SRC-004" in {finding.id for finding in scan_source_tree(tmp_path / "source")}


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


def test_incomplete_pressure_report_still_cleans_owned_resources(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts.e03 import launcher

    run = launcher.TemporaryRun(
        launcher.LaunchConfig("sha256:" + "1" * 64, "sha256:" + "2" * 64, tmp_path / "new")
    )
    run.directory.mkdir(mode=0o700)
    info = run.directory.stat()
    run.directory_identity = (info.st_dev, info.st_ino)
    run.public.mkdir()
    (run.directory / "credential").write_text("private-temporary-credential")
    run.active = "conversa_painel"
    run.intervals[run.active] = [{"start": 1, "end": run.deadline, "passed": True}]
    run.container, run.network = "3" * 64, "4" * 64
    remaining = {run.container, run.network}
    closed = []
    run.forward = SimpleNamespace(
        close=lambda: closed.append(True),
        summary=lambda: {"state": "observed", "failures": 0, "rejections": 0},
    )
    run.kernel_attestation = {}

    def daemon(*args):
        if args[0] == "git":
            return "a" * 40
        if args[:2] == ("docker", "ps"):
            return run.container if run.container in remaining else ""
        if args[:2] == ("docker", "inspect"):
            return json.dumps(
                [{"Id": run.container, "Config": {"Labels": {launcher.LABEL: run.run_id}}}]
            )
        if args[:3] == ("docker", "network", "inspect"):
            return json.dumps([{"Id": run.network, "Labels": {launcher.LABEL: run.run_id}}])
        if args[:3] in (("docker", "rm", "-f"), ("docker", "network", "rm")):
            remaining.remove(args[-1])
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(launcher, "command", daemon)
    monkeypatch.setattr(run, "capture_resources", lambda: None)
    monkeypatch.setattr(run, "sessions", lambda: [])
    run.finalize_report({"schema_version": 1, "run_id": run.run_id, "client": {}, "samples": []})
    assert run.intervals["conversa_painel"] == []
    run.cleanup()
    assert remaining == set()
    assert closed == [True]
    assert not (run.directory / "credential").exists()
    assert not run.public.exists()
    report = json.loads((run.directory / "report.json").read_text())
    assert report["gate"] == "inconclusive"
    assert report["teardown_state"] == "complete"
    assert report["teardown_errors"] == []


@pytest.mark.parametrize("field", ["id", "restarts", "image", "running", "oom_kill"])
def test_resource_summary_preserves_intermediate_oom_and_lifecycle_evidence(field):
    from copy import deepcopy

    from scripts.e03.resources import summarize

    first = {
        "at": 1,
        "containers": {
            "kairos": {
                "id": "1" * 64,
                "image": "sha256:" + "2" * 64,
                "running": True,
                "restarts": 0,
                "oom_killed": False,
                "cgroup": {"state": "observed", "memory_events": {"oom_kill": 0}},
            }
        },
    }
    middle, last = deepcopy(first), deepcopy(first)
    middle["at"], last["at"] = 2, 3
    value = middle["containers"]["kairos"]
    if field == "oom_kill":
        value["cgroup"]["memory_events"][field] = 1
    else:
        value[field] = {
            "id": "3" * 64,
            "restarts": 1,
            "image": "sha256:" + "4" * 64,
            "running": False,
        }[field]
    report = summarize([first, middle, last])
    assert report["state"] == "failed"
    assert report["oom"] == (["kairos"] if field == "oom_kill" else [])
    assert report["lifecycle_changes"] == ([] if field == "oom_kill" else ["kairos"])


def test_resource_snapshot_records_observed_container_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts.e03 import resources

    row = {
        "Id": "1" * 64,
        "Image": "sha256:" + "2" * 64,
        "RestartCount": 0,
        "State": {"Running": True, "OOMKilled": False, "Pid": 123},
    }
    monkeypatch.setattr(resources.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        resources.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout=json.dumps([row])),
    )
    monkeypatch.setattr(
        resources, "cgroup", lambda _: {"state": "observed", "memory_events": {"oom_kill": 0}}
    )
    assert resources.snapshot(["kairos"], tmp_path)["containers"]["kairos"]["id"] == "1" * 64


def test_resource_summary_retains_docker_oom_when_cgroup_is_unavailable():
    from scripts.e03.resources import summarize

    report = summarize(
        [
            {
                "at": 1,
                "containers": {
                    "kairos": {
                        "oom_killed": True,
                        "running": False,
                        "cgroup": {"state": "unavailable"},
                    }
                },
            }
        ]
    )
    assert report["state"] == "failed"
    assert report["oom"] == ["kairos"]
    assert report["gaps"] == ["kairos"]


@pytest.mark.parametrize("divergence", ["missing_listener", "changed_production", "funnel"])
def test_publication_requires_observed_listener_and_preserved_tailnet_config(
    tmp_path, monkeypatch, divergence
):
    from copy import deepcopy

    from scripts.e03 import launcher

    run = launcher.TemporaryRun(
        launcher.LaunchConfig("sha256:" + "1" * 64, "sha256:" + "2" * 64, tmp_path / "new")
    )
    run.public.mkdir(parents=True)
    run.local_origin = "http://127.0.0.1:12345"
    run.origin = run.local_origin
    host = "example.tail123.ts.net"
    before = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            f"{host}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8090"}}},
        },
    }
    current = deepcopy(before)

    def tailscale(*args):
        if args == ("tailscale", "status", "--json"):
            return json.dumps({"Self": {"DNSName": host + "."}})
        if args in (
            ("tailscale", "serve", "status", "--json"),
            ("tailscale", "funnel", "status", "--json"),
        ):
            return json.dumps(current)
        if args == ("tailscale", "serve", "--bg", "--https=8443", run.local_origin):
            if divergence != "missing_listener":
                current["TCP"]["8443"] = {"HTTPS": True}
                current["Web"][f"{host}:8443"] = {"Handlers": {"/": {"Proxy": run.local_origin}}}
            if divergence == "changed_production":
                current["Web"][f"{host}:443"]["Handlers"]["/"]["Proxy"] = run.local_origin
            if divergence == "funnel":
                current["AllowFunnel"] = {f"{host}:8443": True}
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(launcher, "command", tailscale)
    with pytest.raises(ValueError, match=r"temporary_.*mismatch"):
        run.publish()
    assert not (run.public / "manifest.json").exists()


@pytest.mark.parametrize(
    "removal", ["restored", "listener_remains", "production_changed", "listener_replaced"]
)
def test_publication_cleanup_attests_restoration_before_reporting_success(
    tmp_path, monkeypatch, removal
):
    from copy import deepcopy

    from scripts.e03 import launcher

    run = launcher.TemporaryRun(
        launcher.LaunchConfig("sha256:" + "1" * 64, "sha256:" + "2" * 64, tmp_path / "new")
    )
    run.directory.mkdir(mode=0o700)
    info = run.directory.stat()
    run.directory_identity = (info.st_dev, info.st_ino)
    run.public.mkdir()
    (run.directory / "credential").write_text("private-temporary-credential")
    run.local_origin = "http://127.0.0.1:12345"
    run.origin = run.local_origin
    run.final_report = {"gate": "inconclusive"}
    host = "example.tail123.ts.net"
    before = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            f"{host}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8090"}}},
        },
    }
    current = deepcopy(before)

    def daemon(*args):
        if args == ("tailscale", "status", "--json"):
            return json.dumps({"Self": {"DNSName": host + "."}})
        if args in (
            ("tailscale", "serve", "status", "--json"),
            ("tailscale", "funnel", "status", "--json"),
        ):
            return json.dumps(current)
        if args == ("tailscale", "serve", "--bg", "--https=8443", run.local_origin):
            current["TCP"]["8443"] = {"HTTPS": True}
            current["Web"][f"{host}:8443"] = {"Handlers": {"/": {"Proxy": run.local_origin}}}
            return ""
        if args == ("tailscale", "serve", "--https=8443", "off"):
            if removal != "listener_remains":
                current["TCP"].pop("8443")
                current["Web"].pop(f"{host}:8443")
            if removal == "production_changed":
                current["Web"].pop(f"{host}:443")
            return ""
        if args[:2] == ("docker", "ps"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(launcher, "command", daemon)
    run.publish()
    manifest = json.loads((run.public / "manifest.json").read_text())
    assert manifest["origin"] == f"https://{host}:8443"
    if removal == "listener_replaced":
        current["Web"][f"{host}:8443"]["Handlers"]["/other"] = {"Proxy": "http://127.0.0.1:43210"}
    if removal == "restored":
        run.cleanup()
        assert current == before
    else:
        with pytest.raises(RuntimeError, match="listener_cleanup_failed"):
            run.cleanup()
    report = json.loads((run.directory / "report.json").read_text())
    assert report["teardown_state"] == ("complete" if removal == "restored" else "failed")
    assert not (run.directory / "credential").exists()
    if removal == "listener_replaced":
        assert current["Web"][f"{host}:8443"]["Handlers"]["/other"] == {
            "Proxy": "http://127.0.0.1:43210"
        }
