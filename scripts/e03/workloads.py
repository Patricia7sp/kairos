"""Cargas offline fixas no host confiável; nunca importado pelo servidor temporário."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import signal
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from kairos_runtime.experimental.worker_policy import WorkerPolicy

LABEL = "io.kairos.e03.run_id"


def owned_worker_policy(original, run_id):
    if re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
        raise ValueError("identidade inválida")

    class OwnedPolicy(WorkerPolicy):
        def create_args(self):
            args = super().create_args()
            args[-2:-2] = ["--label", f"{LABEL}={run_id}"]
            return args

        def validate(self, inspected):
            super().validate(inspected)
            if inspected.get("Config", {}).get("Labels", {}).get(LABEL) != run_id:
                raise ValueError("identidade de worker divergente")

    return OwnedPolicy(original.image_id, original.name, original.writable, original.sandbox)


@contextmanager
def offline_fixture(image, run_id):
    from kairos_runtime.docker_backend import worker
    from kairos_runtime.experimental import docker_worker

    source = Path(__file__).resolve().parents[2] / "tests/test_docker_sessions_integration.py"
    spec = importlib.util.spec_from_file_location("e03_offline_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous_policy = docker_worker.WorkerPolicy
    previous_worker = worker.SessionWorker

    class PinnedWorker(previous_worker):
        def __init__(self, *args, **kwargs):
            if kwargs.get("image") not in {"kairos:external-sandbox", image}:
                raise ValueError("imagem fora do ensaio")
            kwargs["image"] = image
            super().__init__(*args, **kwargs)

        async def _prepare(self, archive):
            from .resources import lifecycle_barrier

            with lifecycle_barrier(Path(os.environ["KAIROS_E03_RESOURCE_LOCK"])):
                return await super()._prepare(archive)

        async def _cleanup(self):
            from .resources import lifecycle_barrier

            with lifecycle_barrier(Path(os.environ["KAIROS_E03_RESOURCE_LOCK"])):
                return await super()._cleanup()

    def policy_factory(*args, **kwargs):
        return owned_worker_policy(previous_policy(*args, **kwargs), run_id)

    try:
        docker_worker.WorkerPolicy = policy_factory
        worker.SessionWorker = module.SessionWorker = PinnedWorker
        yield module
    finally:
        worker.SessionWorker = previous_worker
        docker_worker.WorkerPolicy = previous_policy


def audit_fixture(root, seconds=0):
    from kairos_security.auditor import run_full_audit

    source = root / "source"
    source.mkdir(exist_ok=True)
    payload = (
        Path(__file__).resolve().parents[2] / "tests/fixtures/e03/audit-source.txt"
    ).read_text()
    for index in range(128):
        (source / f"module_{index}.py").write_text(payload)
    home = root / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    deadline = time.monotonic() + seconds
    audits = 0
    findings = 0
    while audits == 0 or time.monotonic() < deadline:
        report = run_full_audit(home, source_root=source)
        if not report.findings:
            raise RuntimeError("auditoria não exercitou findings")
        audits += 1
        findings += len(report.findings)
    return {
        "findings": findings,
        "fixture_files": 128,
        "audits": audits,
        "batch_minimum_seconds": seconds,
    }


def cycle(scenario, image, run_id, root):
    if scenario == "auditoria":
        return audit_fixture(root, seconds=20)
    with offline_fixture(image, run_id) as module:
        if scenario == "execucao_isolada":
            module.test_real_codex_turn_uses_bridge_and_executes_local_tool(root)
            return {"contract": "real_codex_bridge_tool_checkpoint_no_host_mutation"}
        if scenario == "recuperacao":
            module.test_real_tool_turn_checkpoint_and_broker_restart_preserve_same_thread(root)
            return {"contract": "checkpoint_close_reopen_restore_same_thread_real_tool"}
    raise ValueError("cenário sem carga offline")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=("auditoria", "execucao_isolada", "recuperacao"), required=True
    )
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    args = parser.parse_args(argv)
    if (
        re.fullmatch(r"sha256:[a-f0-9]{64}", args.worker_image) is None
        or re.fullmatch(r"[a-f0-9]{32}", args.run_id) is None
    ):
        return 2
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    while not stopping and not args.stop_file.exists():
        started = time.time()
        try:
            with tempfile.TemporaryDirectory(prefix="e03-offline-") as directory:
                details = cycle(args.scenario, args.worker_image, args.run_id, Path(directory))
            print(
                json.dumps(
                    {
                        "scenario": args.scenario,
                        "start": started,
                        "end": time.time(),
                        "passed": True,
                        "details": details,
                    }
                ),
                flush=True,
            )
        except (OSError, ValueError, RuntimeError, AssertionError, TimeoutError) as exc:
            print(
                json.dumps(
                    {
                        "scenario": args.scenario,
                        "start": started,
                        "end": time.time(),
                        "passed": False,
                        "error_kind": type(exc).__name__,
                    }
                ),
                flush=True,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
