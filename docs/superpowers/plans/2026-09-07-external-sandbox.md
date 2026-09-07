# External Sandbox Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task by task; request an independent security review before completion.

**Goal:** Validate an offline, disposable Docker boundary for real Codex commands.

**Architecture:** Trusted host launcher imports a bounded project snapshot into
a fresh restricted worker, attests Docker/kernel policy, then exposes Codex RPC.
Production integration remains disabled while this prototype establishes feasibility.

**Tech Stack:** Python 3.11, existing CodexRpc, Docker CLI, Codex 0.153.4.

**Spec:** docs/superpowers/specs/2026-09-07-external-sandbox-design.md

## Global Constraints

- No production state, credentials, Docker socket or host bind mounts in worker.
- Network none; nonroot execution; no-new-privileges; all capabilities dropped.
- Keep production adapter, supervisor and Compose unchanged.
- Snapshot maximum 64 MiB / 10,000 entries; reject links and special files.
- Explicit integration opt-in; no live model calls.

## Task 1: Snapshot and worker policy

Files: `kairos_runtime/experimental/snapshot.py`, `worker_policy.py`,
`tests/test_external_sandbox.py`, `docker/external-sandbox/Dockerfile`.
Interfaces: `snapshot_project(Path) -> bytes`; immutable `WorkerPolicy` creates
Docker argv and validates inspect against exact image, identity and resources.

- [x] Test project copy and rejection of escaping links before implementation:
  ```python
  (project / "escape").symlink_to(outside)
  with pytest.raises(ValueError):
      snapshot_project(project)
  ```
- [x] Run `uv run pytest -q tests/test_external_sandbox.py` and observe failure.
- [x] Implement bounded tar from opened descriptors; fixed normalized metadata.
- [x] Add inspect mutation tests, each dangerous field must fail closed:
  ```python
  inspected["HostConfig"]["Privileged"] = True
  with pytest.raises(ValueError):
      policy.validate(inspected)
  ```
- [x] Implement fixed resource/security command and standalone worker image.
- [x] Run focused tests and commit tested unit.

## Task 2: Owned worker lifecycle and real RPC

Files: `kairos_runtime/experimental/docker_worker.py`,
`tests/test_external_sandbox.py`, `tests/test_external_sandbox_integration.py`.
Interface: async context manager `DockerWorker(project, image, writable=False)`;
`execute(command) -> dict` is fixed to /workspace and externalSandbox restricted.

- [x] Test cancellation/failure invokes removal using name allocated before create.
- [x] Observe RED, implement lifecycle with bounded Docker calls and persistent cleanup.
- [x] Build image: `docker build -f docker/external-sandbox/Dockerfile --target worker -t kairos:external-sandbox .`.
- [x] Add and run opt-in integration assertions:
  ```python
  result = await worker.execute(["python", "-c", "print(40 + 2)"])
  assert result["stdout"].strip() == "42"
  ```
  Verify write success only in RW, rootfs/host/network denials, second worker
  cannot see first workspace and Docker inspect cannot find worker after close.
- [x] Verify cancellation also kills a background process in the worker namespace.
- [x] Commit lifecycle and meaningful regression coverage.

## Task 3: Development environment and acceptance

Files: `.devcontainer/devcontainer.json`, `.devcontainer/Dockerfile`,
`docker/external-sandbox/README.md`, acceptance document beside this plan.

- [x] Create development image with Python/Node tooling and isolated repository volume.
- [x] Validate JSON, image build and Python baseline inside container.
- [x] Run runtime regression tests, lint/format and real worker isolation suite.
- [x] Request independent review; fix material findings and rerun affected tests.
- [x] Record actual evidence and explicit pending production integration; commit.
