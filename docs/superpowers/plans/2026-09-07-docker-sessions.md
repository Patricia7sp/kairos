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

- [ ] Write rejection/streaming/cancellation tests before implementation.
- [ ] Run focused tests and observe RED, then implement bounded stdio HTTP bridge.
- [ ] Validate fixed endpoint/header isolation and zero admission outside a turn.
- [ ] Independent review of relay and regression results.

## Task 2: Session registry and checkpoint archives

Files: `kairos_runtime/docker_backend/registry.py`, `archives.py`,
`tests/test_docker_registry.py`.
Interfaces: `SessionRegistry(root)` owns manifest/opaque blobs; validates identity,
stores worker name before launch, commits workspace/home/thread atomically and
lists workers for recovery; `validate_archive(bytes)` rejects unsafe/oversized tar.

- [ ] Write tests for traversal/links/corruption, identity rebinding, atomic checkpoints.
- [ ] Observe RED, implement registry and archive boundary, run focused suite.
- [ ] Review security properties and provide concrete signatures to integration task.

## Task 3: Docker session backend and ChatGPT host integration

Files: `kairos_runtime/docker_backend/{runtime,adapter,auth}.py`,
`kairos_runtime/host.py`, experimental worker transport helpers, Dockerfile,
tests for real offline threaded Codex + RuntimeClient.

- [ ] Add worker server lifecycle/export/import and bounded relay process helpers.
- [ ] Implement per-session adapter preserving events and strict policy validation.
- [ ] Implement broker-owned ChatGPT auth headers using dedicated Codex refresh.
- [ ] Wire opt-in backend into host; preserve local behavior and account surfaces.
- [ ] Validate real threaded tool execution, checkpoint/restore, cancellation and routing.
- [ ] Run full relevant regressions; address independent review findings.

## Task 4: Operational acceptance

- [ ] Document opt-in broker configuration, exports, restart limits and dedicated login.
- [ ] Record actual tests and limitations; commit clean branch.
- [ ] Present concrete next user action only if interactive ChatGPT login remains necessary.
