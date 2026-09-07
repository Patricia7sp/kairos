# Docker Sessions Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development, with independent task reviews and a final security review.

**Goal:** Integrate per-session Docker workers with durable Kairos sessions and broker-owned ChatGPT authentication.

**Architecture:** Existing Unix host owns a Docker session backend and an auth-only
Codex supervisor. Offline workers reach a bounded model transport over stdio and
checkpoint isolated workspace/home before terminal completion.

**Tech Stack:** Python 3.11, Docker CLI, Codex 0.153.4, existing RPC/service/client,
httpx streaming, SQLite registry and content-addressed archive files.

**Spec:** docs/superpowers/specs/2026-09-07-docker-sessions-design.md

## Global Constraints

- No real ChatGPT credentials inside worker; dedicated broker login only.
- Network none, standard Docker protections, no host/socket binds in worker.
- No automatic replay of abandoned turns or writes to original project.
- Default backend unchanged; no production activation before additional acceptance.
- Test model requests with simulated upstream; interactive login is a final user step.

## Task 1: Restricted model relay

Files: `kairos_runtime/docker_backend/model_relay.py`,
`docker/external-sandbox/model_bridge.py`, `tests/test_docker_model_relay.py`.
Interface: `ModelRelay(reader, writer, transport, model)` with start/close and
turn-scoped admission; transport takes validated request and yields byte chunks.

- [x] Rejection, streaming and cancellation tests implemented and passing.
- [x] Bounded stdio HTTP bridge implemented and validated with focused tests.
- [x] Fixed endpoint/header isolation and zero admission outside a turn validated.
- [x] Independent review of relay and regression results completed; findings addressed.

## Task 2: Session registry and checkpoint archives

Files: `kairos_runtime/docker_backend/registry.py`, `archives.py`,
`tests/test_docker_registry.py`.
Interfaces: `SessionRegistry(root)` owns manifest/opaque blobs; validates identity,
stores worker name before launch, commits workspace/home/thread atomically and
lists workers for recovery; `validate_archive(bytes)` rejects unsafe/oversized tar.

- [x] Tests cover traversal/links/corruption, identity rebinding and atomic checkpoints.
- [x] Registry and archive boundary implemented; focused suite passing.
- [x] Security properties reviewed and concrete interfaces integrated.

## Task 3: Docker session backend and ChatGPT host integration

Files: `kairos_runtime/docker_backend/{runtime,worker,auth}.py`,
`kairos_runtime/host.py`, experimental worker transport helpers, Dockerfile,
tests for real offline threaded Codex + RuntimeClient.

- [x] Worker lifecycle/export/import and bounded relay process helpers implemented.
- [x] Per-session adapter preserves events and validates policy strictly.
- [x] Broker-owned ChatGPT headers and dedicated Codex refresh implemented.
- [x] Opt-in backend wired into host; local behavior and account surfaces preserved.
- [x] Real Docker tool execution and checkpoint/restore validated; cancellation and routing covered by regression tests.
- [x] Relevant regressions passed; independent review findings addressed.

## Task 4: Operational acceptance

- [x] Opt-in configuration, exports, restart limits and dedicated login documented.
- [x] Tests and limitations recorded in the local acceptance document.
- [x] Interactive ChatGPT login completed; real RuntimeClient turn returned `KAIROS_OK`.

Acceptance: [local evidence and integration limits](../specs/2026-09-07-docker-sessions-acceptance.md).
Remote CI, merge and production deployment remain outside this local acceptance.
