# Task 2 report — transactional migration and runtime journal

## Outcome

Implemented schema version 2, a transaction-safe v1→v2 migration, the synchronous
`RuntimeRepository`, and the asynchronous `RuntimeStore`. Existing model sessions remain
`execution_kind='model'`; runtime creation writes the canonical `sessions` row and runtime
identity atomically.

The runtime journal now provides per-session sequences/cursors, idempotent admission and
event append, cursor validation, composite session/turn referential integrity, immutable
bound identities, and atomic user-message/turn persistence.

## RED / GREEN evidence

Initial RED:

```text
$ uv run pytest -q tests/test_runtime_storage.py
E   ModuleNotFoundError: No module named 'kairos_runtime.store'
1 error in 0.16s
```

Additional security-constraint RED after adding the protocol/identity mutation cases:

```text
$ uv run pytest -q tests/test_runtime_storage.py -k 'required_constraints or freezes_identity'
FAILED ... test_runtime_tables_are_canonical_and_have_required_constraints
FAILED ... test_bind_negotiates_capabilities_and_freezes_identity
2 failed, 13 deselected in 0.25s
```

The failures demonstrated that protocol 2 was still accepted by raw SQL and that filesystem
identity/consent columns were not yet immutable after bind. The v2 DDL was then tightened.

Focused GREEN:

```text
$ uv run pytest -q tests/test_runtime_storage.py tests/test_schema.py tests/test_state.py
95 passed in 0.99s
```

Task 1 regression GREEN:

```text
$ uv run pytest -q tests/test_runtime_contract.py tests/test_runtime_policy.py
39 passed in 0.08s
```

Full-project GREEN:

```text
$ uv run pytest -q
1177 passed, 19 skipped, 5551 subtests passed in 19.23s
```

Lint and diff checks:

```text
$ uv run ruff check .
All checks passed!

$ git diff --check
(no output; exit 0)
```

## Public API signatures

`RuntimeRepository`:

```python
RuntimeRepository(conn: sqlite3.Connection)

create_session(
    session: RuntimeSession,
    source: str,
    parent_session_id: str | None = None,
    *,
    allowed_directories: tuple[str, ...],
    broad_enabled: bool = False,
    consent: bool = False,
) -> str
bind_thread(
    session_id: str,
    thread_id: str,
    capabilities: RuntimeCapabilities,
) -> None
get_session(session_id: str) -> RuntimeSession
admit(session_id: str, key: str, content: str) -> str
append(
    turn_id: str,
    event_id: str,
    kind: str,
    payload: Mapping[str, Any],
) -> RuntimeEvent
events_after(
    session_id: str,
    cursor: str | None,
) -> tuple[RuntimeEvent, ...]
```

`RuntimeStore` has the same methods as async methods and this constructor:

```python
RuntimeStore(db_path: str | os.PathLike[str])
```

It is exported from `kairos_runtime`. Each async operation opens, initializes, uses, and
closes its SQLite connection wholly inside its worker thread. Cancellation is propagated to
the caller only after the shielded in-flight worker completes and closes its connection in
`finally`; repeated cancellation requests cannot interrupt this drain.

## Schema and migration decisions

- `SCHEMA_VERSION` is 2; `migrate(target=1)` still produces the genuine old schema.
- `migrate()` obtains `BEGIN IMMEDIATE` before reading `schema_version`, so simultaneous
  startup serializes before either process decides to apply `ALTER TABLE`.
- Migration-owned DDL uses `execute_schema()`, which accumulates statements with
  `sqlite3.complete_statement`; it does not use `executescript` or split naively on
  semicolons, so trigger bodies remain intact and rollback remains effective.
- All six runtime tables are canonical/auditable fingerprint tables and are not repairable
  derived objects.
- Turn and send states use the exact enumerations from the brief. Sandbox values and protocol
  version are SQL checked; persisted runtime protocol is null before negotiation or exactly
  1 after negotiation.
- Bound identity is protected by SQL triggers, including execution kind, runtime kind,
  external thread, requested/canonical cwd, sandbox, consent timestamp, device, and inode.
  Runtime identity rows cannot be deleted to bypass those guards.

## Explicit extension / deviations / concerns

- Added `runtime_sessions.requested_cwd TEXT NOT NULL`, as authorized by the integration
  context, so the original alias/path survives restart for symlink-retarget validation.
- Added required `created_at REAL NOT NULL` and `updated_at REAL NOT NULL` columns to every
  runtime operational table.
- Runtime-session state has the canonical SQL `CHECK` values `ready`, `running`,
  `waiting_approval`, `recovering`, `interrupted`, `unavailable`, and `ended`. Both new unbound
  sessions and successfully bound inactive sessions are `ready`; admission is allowed only
  from `ready`. Later lifecycle/lease tasks own the transition mutation APIs.
- No lease mutation, approval decision, or service lifecycle APIs were added prematurely.
- No known blocker. Later lease integration will need to reconstruct `DirectoryIdentity`
  from `requested_cwd`, `canonical_cwd`, `directory_device`, and `directory_inode`; all four
  values are durable and immutable after bind.

## Follow-up verification: cancellation and canonical session states

The final async check initially timed out only in the managed sandbox. A faulthandler capture
showed the main event loop blocked in `selectors.select` while the sole executor worker was
already idle; the independent probe `asyncio.run(asyncio.to_thread(lambda: 1))` reproduced the
same sandbox timeout without Kairos or SQLite. Running the unchanged focused test with the
required unsandboxed permission passed:

```text
$ uv run pytest -q tests/test_runtime_storage.py::test_async_store_opens_connections_in_worker_and_propagates_cancellation
1 passed in 0.22s
```

No polling or sandbox-specific workaround remains in production. The facade uses
`asyncio.shield` and drains the worker before raising `CancelledError`. The test schedules two
cancellations while the repository call is blocked and verifies worker cleanup has completed
before the cancellation reaches caller cleanup.

Canonical session-state tests were written RED first (four focused failures for missing CHECK,
`starting`/`active`, and admission during recovery), then passed after the state correction:

```text
$ uv run pytest -q tests/test_runtime_storage.py -k \
  'required_constraints or create_authorizes or bind_negotiates or ended_or_unavailable'
4 passed, 11 deselected in 0.20s
```
