# Runtime delivery implementation plan

**Spec:** `docs/superpowers/specs/2026-09-10-runtime-delivery.md`

**Goal:** deliver version selection, checkpoint review, approved Git integration,
tested draft PR publication and abrupt broker recovery acceptance.

**Architecture:** immutable operator-exported Git catalog; baseline and review
utilities shared by broker and operator; authenticated read-only review in Web;
explicit trusted-host Git commands. Preserve current Docker and policy boundaries.

## Global Constraints

- Work only in `/home/paty7sp/projetos/kairos/.worktrees/runtime-delivery`, branch `feat/runtime-delivery`; no production changes by implementers.
- No Git credentials or Docker socket in Web. No worker home/auth in review artifacts.
- Catalog layout is `<catalog>/<full-commit>/workspace` plus sibling `project.json`; never overwrite a published version or broaden exact-directory authorization to descendants.
- Review canonical JSON is at most 512 KiB; transport remains limited to 1 MiB. No truncated review or unreviewed file application.
- Apply requires exact review-ID approval and verified Git baseline, uses a new worktree/branch, runs explicit argv tests without a shell, and publishes only through a separate explicit command after success.
- Failed/interrupted turns are never automatically replayed. Existing sessions and mounts remain usable.
- Use meaningful TDD for behavioral changes. Focused tests per edit, complete relevant suites once per task; root runs full CI and Docker acceptance once after integration.
- Commit scoped changes; do not commit local dependency symlinks, scratch reports, auth, private production data, or incidental generated packaging metadata.

### Task 1: Immutable source catalog and bounded review artifacts

Create `kairos_runtime/projects.py`, `kairos_runtime/reviews.py` and focused tests.
Read existing `docker_backend/archives.py` and `experimental/snapshot.py` to reuse
bounds and validation rather than implementing permissive tar extraction.

Provide these stable Python interfaces (Path inputs, JSON-serializable metadata):

```python
export_project(repo: Path, revision: str, catalog: Path) -> dict
discover_projects(catalogs: tuple[str, ...]) -> list[dict]
project_metadata(workspace: Path) -> dict | None
build_review(session_id: str, baseline: bytes, checkpoint: bytes,
             *, base_commit: str | None = None) -> dict
validate_review(bundle: dict) -> dict
workspace_fingerprint(archive: bytes) -> str
verify_review_baseline(bundle: dict, baseline: bytes) -> None
```

`export_project` resolves a commit safely using Git argument separation, exports
tracked regular files (no symlinks/submodules) to staging, validates the archive,
normalizes modes as snapshot does, and atomically publishes an immutable version.
Reject tracked files excluded by snapshot policy (e.g. `.env`, `.git`) explicitly,
not silently; ordinary tracked `.superpowers` reports are permitted. Re-export
verifies existing contents/metadata, never repairs/overwrites them. Manifest keys:
`schema_version: 1`, `revision`, `name` (repo basename), `baseline_fingerprint`.
Discovery validates commit-name/manifest, fixed workspace location, absence of
links and fingerprint; outputs metadata plus `cwd`. Legacy roots have no metadata.
Avoid unbounded subprocess capture and no follow of unsafe output directories.

Review schema keys: `schema_version: 1`, `session_id`, `base_commit` (nullable),
`baseline_fingerprint`, `checkpoint_digest` (SHA-256 of raw validated checkpoint),
`changes`, `diff`, `review_id`. Each sorted change has `path`, `kind` (add/modify/delete),
`old_sha256`, `new_sha256`, `old_mode`, `new_mode`, `content_base64` (nullable for
deletion). Modes are normalized 420 or 493. Fingerprint is SHA-256 of canonical
JSON of sorted regular-file `{path, sha256, mode}` entries. Ignore empty dirs.
Review ID is SHA-256 of canonical JSON (`sort_keys=True,separators=(',', ':'),
ensure_ascii=True`) without `review_id`. Limit includes final ID. Validate strict
schema/types, duplicates, safe paths, base64/content hashes, nullable fields,
kind consistency, hashes and approval digest. Diff uses unified text for UTF-8,
explicit binary/mode notices otherwise. Build and validate share invariants.
`verify_review_baseline` checks the fingerprint and each old hash/mode against
the reconstructed baseline, regenerates the diff using approved new content and
rejects any disagreement between readable diff and payload.
Do not implement Git apply, CLI, registry or Web in this task.

TDD: real temporary Git repos with two revisions/idempotence/tampered target;
reject symlink/submodule/traversal and `.git` paths; compare add/modify/delete,
binary/mode-only changes, altered ID/content/schema and over-limit packages.
Run new test files and existing archive/snapshot tests, ruff on touched files.
Document public interface examples and limitations in module docstrings. Commit.

### Task 2: Persist baseline and expose authenticated version/review UI

Use Task 1 interfaces. Add optional `agent_runtime.project_catalogs` validated list
to host config; discover versions on startup, append exact workspaces to authorized
directories, expose `project_versions` in runtime.status. Keep existing roots and
old sessions. Add readonly compose catalog mount configured with
`KAIROS_RUNTIME_PROJECT_CATALOG`, target `/projects/versions`, and conservative existing project fallback;
document matching config and idle restart. Healthcheck must retain current behavior;
do not hash entire catalogs on every healthcheck configuration read. Perform
discovery at broker startup separately from cheap configuration validation.

Add registry baseline table rather than changing SessionRecord positional fields:
session_id primary key, workspace_digest, base_commit nullable. Capture initial
source snapshot once before worker creation, record immutable baseline and pass
that exact archive into SessionWorker. On restore retain baseline. Baseline metadata
comes from trusted sibling project manifest verified against captured snapshot.
Expose `DockerSessionRuntime.changes(session)` returning Task 1 review; legacy
baseline absence explicit error, never guess from current source. Protect access
with service authorization, per-session lock/no active task and host mutation fence.
Add `session.changes` IPC method/client and authenticated HTTP
`GET /api/runtime/sessions/{session_id}/changes`; unavailable local backend is a
controlled error. Avoid blocking the asyncio loop with snapshot/hash work.

Web runtime selector labels discovered versions using project name and shortcommit.
Completed idle sessions offer `Revisar alterações`; on click show revision,
review ID, file summary and diff via textContent, plus `Baixar pacote` button for download of full JSON.
Do not introduce apply/execute/publish in Web. New turn/session change invalidates
stale preview. API error/loading/large-artifact states are visible. Implement in
existing vanilla runtime UI/API; preserve React Web and reconnection tests.

TDD covers immutable baseline across checkpoint/restart, legacy session error,
catalog config invalidity/versions/exact roots, authenticated HTTP success and
denial, active-turn conflict/authorization, UI safe text and download/staleness.
Run relevant runtime registry/host/service/API tests, Web suite and tsc, compose
and health tests, ruff. Update example configuration and operator docs. Commit.

### Task 3: Trusted operator export, review, apply and publish commands

Add focused `kairos_cli/runtime_delivery.py` using Task 1 interfaces and integrate
parser/catalog/runtime dispatch. Flat subcommands:

```
kairos runtime project-export --repo PATH --revision REF --catalog PATH [--json]
kairos runtime changes --session ID --output FILE [--json]
kairos runtime apply --repo PATH --bundle FILE --approve REVIEW_ID --branch NAME --worktree PATH --test 'ARGV STRING' [--test ...] --receipt FILE [--json]
kairos runtime publish --receipt FILE --title TITLE --base main [--json]
```

All outputs written without overwriting existing user files. JSON output never
contains auth. `changes` uses IPC and validates complete bundle before saving0600.
Apply checks bundle size before parsing, rejects duplicate JSON keys, validates exact approval, requires
non-null base commit, reconstructs source baseline using Git into disposable
export and uses `verify_review_baseline` to check fingerprint, old hashes/modes
and readable diff before creating new branch/worktree. Validate new
branch name via Git; no force, no checkout of original repo. Reject worktree path
inside source/export/.git and ensure safe parents/no symlinks. Apply only reviewed
files, respecting removals/modes and preventing `.git` components, symlinks and
path escape. Handle file↔directory transitions correctly. Verify resulting tree
matches approved changes before and after tests. Mandatory nonempty tests parsed
with shlex.split, subprocess argv with cwd isolated worktree, no shell; on failure
keep worktree for inspection and stop before commit. Allow ignored generated test
outputs, but reject tracked or nonignored unexpected changes. Commit staged exact
reviewed files with hooks disabled and signing disabled; receipt records schema,
review_id, baseline fingerprint/base commit, worktree/repo/branch, commit/tree and
successful test argv/results. Receipt protected0600 and immutable create.

Publish revalidates receipt shape, commit/tree/branch/clean state and successful
tests; uses push without force to configured origin, then `gh pr create --draft`
with structured title and a temporary --body-file containing change/test evidence.
No shell interpolation, secrets or caller-controlled option injection. If branch
already pushed after prior partial failure, permit safe retry for samecommit and
existing matching draft PR. Return PR URL. Git/gh failures sanitized yet actionable.
No live publication from implementer tests: use local bare remote and fake gh.

TDD temporary real Git: apply add/edit/delete/binary/modes and correctcommit;
wrong approval/base, malicious paths, dirty/colliding worktree/branch, failed tests,
extra modifications, unchanged-baseline file/directory collisions (Task1 minor
review gap), altered receipt and publish HEAD drift rejected; positive
push/draft behavior with stub gh and exact argv/body evidence. Existing CLI help
and parser tests remain valid. Add `docs/runtime-delivery.md` operator runbook with concrete commands and
review boundaries, limits and failure recovery. Run relevant CLI/core tests, ruff.
Commit.

### Task 4: Abrupt broker recovery and full delivery acceptance

Create opt-in integration test plus minimal fixture helper using real Docker
SessionWorker with existing OfflineToolModel protocol, private temporary state
and a real subprocess broker service. Complete one turn/checkpoint; start a
second long tool writing an uncommitted marker, prove it is running, SIGKILL only
the disposable broker subprocess, restart same state. Assert exactly one durable
interrupted terminal for old turn, no automatic model call/replay, old worker
removed, confirmed checkpoint unchanged and uncommitted marker absent. An explicit
new submit must resume the same external thread and complete with restored file.
Bound waits and always clean disposable process/workers. Existing graceful-restart
test remains. If failure reveals product defect, report root cause to controller
before minimal fix and add regression in same task. Run opt-in test with Docker,
focused non-Docker recovery tests and ruff. Document actual failure guarantees.
Commit; controller then handles final review/CI, image builds, backup/restore,
production deployment and real version→task→review→apply→tests→draft PR acceptance,
recording evidence in a dated acceptance document without private artifacts.


### Task 5: Retain the production worker image dependency

Repeated removal of unused worker images was observed during this delivery; root
restored the validated worker and started temporary `kairos-worker-image-retention`.
Add permanent Compose service `runtime-worker-image` in profile `agent-runtime`,
container_name `kairos-runtime-worker-image`, with this behavior:

```yaml
    image: "${KAIROS_RUNTIME_WORKER_IMAGE:-kairos:external-sandbox}"
    pull_policy: never
    platform: linux/amd64
    restart: unless-stopped
    user: "10000:10000"
    entrypoint: ["/bin/sleep"]
    command: ["infinity"]
    network_mode: none
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 8
    mem_limit: 16m
    cpus: 0.05
```

No ports, volumes, Docker socket, secrets, model calls, worker session, or extra
build/pull behavior. Broker depends_on this service with condition service_started,
retaining existing healthy-app dependency. Operator builds/loads worker first and
sets `KAIROS_RUNTIME_WORKER_IMAGE` to the same immutable digest as config docker_image.
Do not change or disable global cleanup jobs. Document purpose and limitation
against forced administrator removal, private docker image save/load recovery,
and matching config/environment IDs in docs/runtime-delivery.md and production
runbook. Add meaningful Compose tests for dependency/image/security/profile and
preserve Web isolation; run Compose/health tests and lint/diff checks. Commit.
Controller deploys permanent holder before removing its own temporary holder.
