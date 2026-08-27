# Task 6 Report

## Outcome

Implemented the interaction composition root exported as
`build_interaction_service(home: Path) -> InteractionService`.

The builder creates one coherent graph from exactly the supplied `home`:

```text
home
├── state.db -> one SQLite connection
│   ├── SessionRepository
│   ├── MessageRepository
│   └── UsageRepository
├── config.yaml -> SelectionContextLoader
└── build_provider_gateway(home)
    ├── provider registry/factories
    ├── curated + cached ModelCatalog -> ModelSelectionResolver
    ├── lazy CredentialService
    └── shared provider HTTP clients
```

There are no module-level service, connection, credential, gateway, catalog, resolver, loader, or
repository instances. The process environment's `KAIROS_HOME` is deliberately not consulted by
this root; even when it points elsewhere, state, configuration, catalog snapshots, and credentials
derive from the supplied path.

## TDD evidence

### RED 1: public builder and core graph

Command:

```bash
uv run pytest -q tests/test_interaction_composition.py
```

Result before production code:

```text
ImportError: cannot import name 'build_interaction_service' from 'kairos_integration'
1 error in 0.24s
```

This was the expected failure: the requested public builder did not exist.

### GREEN 1

After adding the minimal builder, export, shared SQLite repositories, provider gateway, resolver,
and selection loader:

```text
1 passed in 0.14s
```

### RED 2: explicit resource lifecycle

Added a behavior test requiring async context-manager support, idempotent close, gateway close, and
SQLite close. Result:

```text
TypeError: '_ComposedInteractionService' object does not support the asynchronous context manager protocol
1 failed, 1 passed in 0.22s
```

This failed for the intended missing lifecycle contract.

### GREEN 2

After adding composition-owned `aclose`, `__aenter__`, and `__aexit__`:

```text
2 passed in 0.15s
```

### RED 3: exact-home configuration and state isolation

Added a test with the supplied home and environment `KAIROS_HOME` pointing at different config
files. Result before config wiring:

```text
kairos_providers.selection.ModelSelectionUnavailableError: nenhuma seleção configurada
1 failed, 2 passed in 0.32s
```

This demonstrated that the loader was not yet receiving configuration from the supplied home.

### GREEN 3

After safe-loading only `home / "config.yaml"`, using its optional `profiles` mapping for profile
selection and the document itself for the global selection layer:

```text
3 passed in 0.19s
```

The test also verifies `state.db` is created only under the supplied home.

## Dependency and compatibility choices

- Reused `build_provider_gateway(home)` as the only provider composition factory. It already builds
  the registry, catalog, snapshot store, lazy credential service, and shared HTTP-client owner from
  that same home; none of those factories were duplicated.
- Built `ModelSelectionResolver` from the exact `gateway.catalog` instance, so resolution and
  adapter creation observe the same curated/cached catalog.
- Opened `home / "state.db"` with `kairos_state.connect`, ran the existing idempotent `migrate`, and
  supplied the same connection to all three repositories. Existing state rows and schema remain
  readable; no schema or repository serialization changed.
- Loaded configuration locally from the explicit path because the existing CLI config loader
  resolves its path through process environment. Calling it here would violate the one-home
  invariant.
- Did not supply provider billing metadata. Existing catalog/provider APIs do not expose a single
  authoritative billing base URL and auth mode for the frozen selection, so the service retains
  its established `billing_base_url=""` and `billing_mode="unknown"` defaults rather than using
  guessed hardcoded routes.
- Exported only `build_interaction_service` from `kairos_integration`. Future WebSocket and CLI
  consumers therefore need not import adapter factories or provider composition modules.

## Resource ownership and close behavior

`InteractionService` constructed directly with injected dependencies remains unchanged and does
not own or close its caller's gateway.

The composition root returns a private `InteractionService` subtype because it created and owns the
SQLite connection and provider gateway. That subtype:

- exposes the required `home` and shared `gateway` attributes;
- supports `await service.aclose()` and `async with service`;
- closes the composed gateway first and the SQLite connection in `finally`;
- makes close idempotent;
- does not add ownership semantics to ordinary injected `InteractionService` instances.

The provider composition itself creates HTTP clients lazily and closes them through
`gateway.aclose()`. Construction failure closes the SQLite connection; provider construction is
otherwise local/lazy and opens no HTTP client.

## Verification

Required focused command:

```bash
uv run pytest -q tests/test_interaction_composition.py tests/test_interaction_service.py && \
uv run ruff check kairos_integration tests/test_interaction_composition.py
```

Result:

```text
6 passed in 0.23s
All checks passed!
```

Formatting and whitespace:

```bash
uv run ruff format --check kairos_integration/composition.py \
  kairos_integration/__init__.py tests/test_interaction_composition.py
git diff --check
```

Result: all three files formatted; `git diff --check` produced no output.

Full suite, run once:

```bash
uv run pytest -q
```

Result:

```text
990 passed, 19 skipped, 5546 subtests passed in 14.96s
```

## Changed files

- `kairos_integration/composition.py` (new)
- `kairos_integration/__init__.py`
- `tests/test_interaction_composition.py` (new)
- `.superpowers/sdd/2026-08-26-interaction-service-persistence/task-6-report.md` (new)

## Self-review and concerns

Reviewed the final dependency graph, lifecycle, exact-home behavior, public export, diff whitespace,
and billing-metadata ruling. The focused tests exercise realistic mutations: removing the builder
export, consulting environment home, omitting config wiring, failing to close either composed
resource, or making close non-idempotent.

No blocking concern. The lifecycle is intentionally attached only to composition-created service
instances; future transport consumers must close the builder result (preferably with `async with`).

## Fix Round 1

### Findings and fixes

#### Critical: usage was not drained before SQLite close

Root cause: `InteractionService._record_usage()` queues into the injected `UsageRepository`, but
the composed subtype closed SQLite without calling the repository's persistence boundary. The
repository itself correctly restores a failed batch to its queue, so the missing ownership action
was in composition shutdown.

Covering test:

- `tests/test_interaction_composition.py::test_aclose_persiste_uso_enfileirado_por_stream`

RED:

```text
assert None == (1, 7, 3)
1 failed, 3 deselected in 0.21s
```

The composed service now retains the injected usage repository through the base service and calls
`flush()` before closing SQLite. The test streams a real interaction through a fake gateway,
closes the service, reopens `state.db`, and verifies API-call/input/output counters persisted.

Exception-safe covering test:

- `tests/test_interaction_composition.py::test_aclose_tenta_gateway_e_repete_flush_que_falhou`

RED was the raw first `RuntimeError("flush indisponível")`, caused by marking `_closed` before any
cleanup. GREEN verifies the first close still attempts the gateway, leaves SQLite open and the
batch queued, reports a `BaseExceptionGroup`, and a second close retries the flush, persists the
same `(1, 7, 3)` counters, retries gateway cleanup, and closes SQLite.

#### Important 1: owned lifecycle was erased from the public return type

Root cause: the builder was annotated as `InteractionService`, while `home`, `gateway`, `aclose`,
and the async context-manager methods existed only on a private subtype.

Covering test:

- `tests/test_interaction_composition.py::test_builder_declara_tipo_publico_com_lifecycle`

RED:

```text
ImportError: cannot import name 'ComposedInteractionService' from 'kairos_integration'
1 error in 0.27s
```

The owned subtype is now public as `ComposedInteractionService`, exported from
`kairos_integration`, and is the declared return type of `build_interaction_service`. Web/CLI can
therefore discover and type-check `home`, `gateway`, `aclose`, and `async with`. Directly injected
`InteractionService` remains the caller-owned base type and gains no automatic close behavior.

#### Important 2: construction failure after gateway creation leaked the gateway

Root cause: the original exception handler knew only about SQLite. Once
`build_provider_gateway(home)` returned, any later config/repository/resolver/service construction
failure transferred an owned async resource without a matching cleanup path.

Covering test:

- `tests/test_interaction_composition.py::test_falha_de_construcao_pos_gateway_fecha_gateway_e_banco`

RED:

```text
assert gateway.closed
E assert False
1 failed, 5 deselected in 0.21s
```

The synchronous builder now tracks gateway acquisition and, on failure, waits for
`gateway.aclose()` in a dedicated cleanup thread before returning control. This is deliberate: the
builder may be called while an event loop is already running, so `asyncio.run` on the caller thread
is invalid and silently scheduling cleanup would leak ownership beyond the failed call. The bridge
blocks until cleanup completes and propagates it. SQLite is always attempted afterward. If cleanup
also fails, a `BaseExceptionGroup` preserves the construction error plus every cleanup error.

The test forces service construction to fail after gateway creation from inside a running event
loop and verifies both `gateway.closed` and SQLite's `closed database` behavior.

#### Important 3: partial provider close was terminal and stopped at the first error

Root cause: `_ProviderHttpClients.aclose()` set `_closed=True` before iterating, awaited clients
sequentially without per-client error handling, and therefore both skipped later clients and made
the failed client impossible to retry.

Covering tests:

- `tests/test_provider_composition.py::ProviderManagerLifecycleTests::test_gateway_tenta_todos_os_closes_e_permite_repetir_falhas`
- `tests/test_provider_composition.py::ProviderManagerLifecycleTests::test_gateway_tenta_demais_clientes_apos_cancelamento`

Initial RED for ordinary failure:

```text
RuntimeError: falha transitória no close
1 failed, 15 deselected in 0.20s
```

Cancellation mutation RED (catch narrowed to `Exception`):

```text
asyncio.exceptions.CancelledError
1 failed, 16 deselected in 0.19s
```

Provider cleanup now enters a non-reopenable `_closing` state, attempts every client even after an
error or cancellation, removes only successfully closed clients, and groups failures. A later
`aclose()` retries exactly the remaining clients; only complete success marks `_closed=True`.
Creating a new adapter after shutdown starts is rejected, preventing replacement clients from
appearing between retries. The narrow provider-composition change is required because the
interaction composition owns this gateway and cannot itself enumerate or close its internal HTTP
clients.

### Lifecycle and error semantics after the fix

Normal owned shutdown order is:

```text
UsageRepository.flush -> ProviderGateway.aclose -> SQLite close -> mark service closed
```

- All provider clients are attempted and failures/cancellation are aggregated.
- A usage-flush failure keeps SQLite open and the coalesced batch intact for retry, while gateway
  cleanup is still attempted.
- A gateway failure after a successful usage flush does not retain SQLite unnecessarily; the
  service remains retryable so the gateway can finish closing.
- `_closed` becomes true only after the persistence boundary and all eligible owned cleanup
  complete successfully. Successful close remains idempotent.
- Construction failure waits for gateway cleanup and attempts SQLite close; cleanup failures are
  grouped with the original build error.
- Caller-injected plain `InteractionService` dependencies remain caller-owned.

Exact-home isolation, `gateway.catalog` resolver identity, existing migration/readability behavior,
and the decision not to invent provider billing routes remain unchanged.

### Verification

Affected tests and lint:

```bash
uv run ruff check kairos_integration kairos_providers/composition.py \
  tests/test_interaction_composition.py tests/test_provider_composition.py
uv run pytest -q tests/test_interaction_composition.py tests/test_interaction_service.py \
  tests/test_provider_composition.py tests/test_provider_gateway.py \
  tests/test_interaction_persistence.py tests/test_state.py
```

Result:

```text
All checks passed!
99 passed in 0.79s
```

Full suite, run once after the fixes:

```bash
uv run pytest -q
```

Result:

```text
996 passed, 19 skipped, 5546 subtests passed in 14.93s
```

### Changed files

- `kairos_integration/composition.py`
- `kairos_integration/__init__.py`
- `kairos_providers/composition.py`
- `tests/test_interaction_composition.py`
- `tests/test_provider_composition.py`
- `.superpowers/sdd/2026-08-26-interaction-service-persistence/task-6-report.md`

No blocking concern remains from this review round.

## Fix Round 2

### Finding: concurrent `aclose()` duplicated one cleanup attempt

Root cause: both `_ProviderHttpClients.aclose()` and
`ComposedInteractionService.aclose()` used a check-then-await sequence. Async tasks can interleave
at the first client/gateway await, so two callers that both observed `_closed=False` independently
snapshotted the same clients or independently flushed the same usage repository. On the provider
side that could close a client twice and make the second successful path delete an already removed
dictionary key. On the service side it duplicated usage flush, gateway close, and SQLite close.

### RED evidence

Provider barrier regression:

- `tests/test_provider_composition.py::ProviderManagerLifecycleTests::test_aclose_concorrente_compartilha_tentativa_e_retry`

Command:

```bash
uv run pytest -q tests/test_provider_composition.py \
  -k aclose_concorrente_compartilha_tentativa_e_retry
```

Result before the fix:

```text
AssertionError: 2 != 1
1 failed, 17 deselected in 0.18s
```

Both callers reached the same barrier client, proving duplicate close attempts. The old paths also
produced different/spurious grouped results after the shared client was released.

Composed-service barrier regressions:

- `tests/test_interaction_composition.py::test_aclose_concorrente_compartilha_um_cleanup`
- `tests/test_interaction_composition.py::test_cancelar_um_caller_nao_cancela_cleanup_compartilhado`

Command:

```bash
uv run pytest -q tests/test_interaction_composition.py \
  -k 'aclose_concorrente_compartilha_um_cleanup or cancelar_um_caller'
```

Result before the fix:

```text
assert 2 == 1  # duplicate UsageRepository.flush
BaseExceptionGroup: falha ao fechar InteractionService (CancelledError)
2 failed, 7 deselected in 0.25s
```

Cancellation of the first caller propagated into its gateway cleanup instead of cancelling only
that waiter, while the second caller started a separate cleanup attempt.

### Implementation and concurrency semantics

Each owner now keeps one `asyncio.Task[None]` for the active close attempt:

- `aclose()` creates the task synchronously before its first await, so another coroutine on the
  same event loop sees and joins the exact task rather than starting another attempt.
- All callers await `asyncio.shield(task)`. Cancelling one caller cancels only its wait; the cleanup
  task continues and other waiters complete normally.
- The task is created inside `aclose()` on the currently running loop. Constructors create no
  `asyncio.Lock`, `Event`, or loop-bound future, avoiding constructor-time/cross-loop affinity.
- A done callback retrieves the task result so cancellation of the only waiter cannot leave an
  unobserved task exception. Awaiters still receive the original result/error.
- Successful completion sets the existing `_closed` flag and later calls are idempotent no-ops.
- A failed completed task is replaced only by a later `aclose()` call, forming a new explicit retry
  attempt. Overlapping callers receive the same failure from the original task.
- Provider `_closing` is set before the attempt task is scheduled. Adapter creation therefore
  remains rejected from the instant shutdown begins, including between a failure and retry.
- Within one provider attempt, successful clients are removed once, failed clients remain, and the
  next attempt sees only those remaining clients. Thus each client is closed at most once per
  attempt and successful clients are not retried.
- Within one service attempt, usage flush, gateway close, and eligible SQLite close execute once.
  The Fix Round 1 retry and error aggregation semantics remain unchanged.

### GREEN and verification

Isolated regressions:

```text
tests/test_provider_composition.py ... 1 passed, 17 deselected in 0.13s
tests/test_interaction_composition.py ... 2 passed, 7 deselected in 0.20s
```

Broader affected set and lint:

```bash
uv run ruff check kairos_integration kairos_providers/composition.py \
  tests/test_interaction_composition.py tests/test_provider_composition.py
uv run pytest -q tests/test_interaction_composition.py tests/test_interaction_service.py \
  tests/test_provider_composition.py tests/test_provider_gateway.py \
  tests/test_interaction_persistence.py tests/test_state.py
```

Result:

```text
All checks passed!
102 passed in 0.81s
```

Full suite, run once after the concurrency fix:

```bash
uv run pytest -q
```

Result:

```text
999 passed, 19 skipped, 5546 subtests passed in 14.94s
```

### Changed files

- `kairos_integration/composition.py`
- `kairos_providers/composition.py`
- `tests/test_interaction_composition.py`
- `tests/test_provider_composition.py`
- `.superpowers/sdd/2026-08-26-interaction-service-persistence/task-6-report.md`

No blocking concern remains from this concurrency round.

## Fix Round 3

### Finding: shared attempt Task was still loop-bound across threads

Fix Round 2 serialized callers on one event loop, but its shared state was an `asyncio.Task`.
`asyncio.Task` belongs to the loop that created it, so a waiter on another loop could not await it.
The unsynchronized read/assignment was also not atomic across OS threads, leaving a second path to
duplicate cleanup before either thread published its Task.

### Deterministic RED evidence

Provider owner:

- `tests/test_provider_composition.py::ProviderManagerLifecycleTests::test_aclose_em_loops_de_threads_compartilha_tentativa`

The test starts one event-loop thread, blocks its provider-client close at a thread barrier, then
starts a second event-loop thread and releases both only after the second caller has entered
`aclose()`.

Command:

```bash
uv run pytest -q tests/test_provider_composition.py \
  -k loops_de_threads_compartilha_tentativa
```

RED result:

```text
AssertionError: first.is_alive() is True
1 failed, 18 deselected in 1.20s
```

The foreign-loop wait stranded the original caller; the prior implementation could also race into
two client closes and conflicting deletion.

Composed service:

- `tests/test_interaction_composition.py::test_service_aclose_em_loops_de_threads_compartilha_tentativa`
- `tests/test_interaction_composition.py::test_cancelar_waiter_em_outro_loop_nao_cancela_cleanup_global`

Command:

```bash
uv run pytest -q tests/test_interaction_composition.py \
  -k 'loops_de_threads_compartilha_tentativa or outro_loop_nao_cancela'
```

RED result included the exact cross-loop failure:

```text
RuntimeError: Task ... got Future ... attached to a different loop
1 failed, 1 passed, 9 deselected in 0.22s
```

### Loop-neutral coordination

Both resource owners now use the same proportionate pattern:

1. A short `threading.Lock` protects only close-attempt state transitions; it is never held across
   an `await` or resource cleanup.
2. The first caller atomically creates a `concurrent.futures.Future[None]` as the attempt outcome
   and schedules one runner Task on that caller's current event loop.
3. The runner Task remains private to its owner loop and is never awaited from another loop. It
   publishes success, the aggregated error, or cancellation into the thread-safe outcome future.
4. Each caller observes the thread-safe outcome with a short non-blocking loop-local wait
   (`asyncio.sleep(0.001)` between `done()` checks), then reads `result()`. It never awaits a
   foreign-loop Future and does not depend on cross-thread `call_soon_threadsafe` wakeups.
5. Cancelling one loop-local waiter stops only that caller's observation loop; it does not cancel
   the shared outcome or the cleanup runner.
   Other loops continue and receive the attempt result.
6. If the attempt fails, the completed outcome remains observable to overlapping callers. A later
   call atomically replaces it with a new attempt, preserving retry-after-failure.
7. Successful close sets `_closed` before publishing success; all later calls are idempotent.

Provider adapter creation now uses the same short state lock. `_closing` is set atomically when the
first outcome is installed, before the runner is scheduled, so no adapter can be created during
the cross-thread handoff or between failed and retry attempts. The existing per-attempt behavior
remains intact: successful clients are removed once, failed clients remain, and a retry closes only
those remaining clients.

The service test uses thread-safe lifecycle doubles to isolate coordination from SQLite's own
thread-affinity rule. It verifies exactly one usage flush, gateway close, and connection close
globally. The cancellation regression cancels a waiter on the second loop while the first loop is
blocked and verifies that the leader completes and no cleanup is duplicated.

The provider cross-loop test intentionally fails the shared first attempt. Both loop threads
receive a `BaseExceptionGroup`, the client is attempted once, and a later call from a third loop
successfully retries that remaining client exactly once.

### GREEN and verification

Isolated cross-loop regressions:

```text
tests/test_provider_composition.py ... 1 passed, 18 deselected in 0.13s
tests/test_interaction_composition.py ... 2 passed, 9 deselected in 0.18s
```

The isolated pair was repeated five times after the final loop-local observation change; all five
runs passed.

Broader affected lifecycle set:

```bash
uv run pytest -q tests/test_interaction_composition.py tests/test_interaction_service.py \
  tests/test_provider_composition.py tests/test_provider_gateway.py \
  tests/test_interaction_persistence.py tests/test_state.py
```

Result:

```text
105 passed in 0.88s
```

Lint, formatting, and whitespace:

```bash
uv run ruff format --check kairos_integration/composition.py \
  kairos_providers/composition.py tests/test_interaction_composition.py \
  tests/test_provider_composition.py
uv run ruff check kairos_integration kairos_providers/composition.py \
  tests/test_interaction_composition.py tests/test_provider_composition.py
git diff --check
```

Result: all files formatted, `All checks passed!`, and no whitespace errors.

Full suite, run once after the cross-loop fix:

```bash
uv run pytest -q
```

Result:

```text
1002 passed, 19 skipped, 5546 subtests passed in 14.59s
```

### Changed files

- `kairos_integration/composition.py`
- `kairos_providers/composition.py`
- `tests/test_interaction_composition.py`
- `tests/test_provider_composition.py`
- `.superpowers/sdd/2026-08-26-interaction-service-persistence/task-6-report.md`

No blocking concern remains from the cross-loop/thread concurrency review.

## Fix Round 4

### Critical finding and architectural correction

Fix Round 3 made the attempt outcome loop-neutral, but the attempt runner was still created by the
first caller while holding the owner's non-reentrant state lock. Three independent failure modes
remained:

1. `asyncio.create_task()` could raise after a pending outcome and provider `_closing` state were
   installed. No task then existed to complete the outcome, so current and future waiters could
   poll forever and the provider retained stranded clients.
2. An eager/custom task factory could synchronously execute the runner while the state lock was
   held. Both successful close paths reacquired that lock, which made launch capable of deadlock.
3. The runner itself published the outcome from inside its coroutine body. Cancellation before the
   first coroutine step, including normal `asyncio.run()` pending-task cancellation during loop
   shutdown, skipped that publisher and could abandon cleanup and every foreign-loop waiter.

Both owners now delegate attempt coordination to
`kairos_providers._async_cleanup.AsyncCleanupCoordinator`:

- A short `threading.Lock` reserves exactly one global attempt. Provider `_closing=True` is set in
  that same critical section, so adapter creation is rejected irreversibly from the first close.
- Task launch happens only after releasing the lock. A launch exception closes the unstarted
  wrapper coroutine, completes the thread-safe outcome with that exception, and leaves a later
  `aclose()` free to reserve a retry.
- Cleanup is run by a private `_PersistentCleanupTask` constructed directly on the current loop.
  It deliberately bypasses the caller's task factory, so an eager/custom factory cannot execute
  cleanup during reservation.
- The supervisor task refuses external `Task.cancel()`. Therefore normal `asyncio.run()` shutdown
  keeps driving and awaiting it instead of closing its loop while cleanup is pending. This is what
  makes cleanup independent of a canceled waiter without moving async resource close calls to a
  different loop.
- Actual usage flush, provider-client closes, gateway close, and SQLite close still run on the
  initiating owner loop/thread. In particular, HTTP client cleanup is not moved to an arbitrary
  permanent background loop, preserving possible transport loop affinity.
- A task done callback is the sole publisher. The task wrapper returns a captured cleanup error
  instead of finishing with an unobserved exception; the callback publishes success, grouped
  failure, or any unexpected terminal cancellation into a `concurrent.futures.Future`.
- Waiters in any loop observe that outcome with loop-local nonblocking sleeps. Canceling a waiter
  never obtains or cancels the supervisor task or the shared outcome.
- Success is marked before publishing the successful outcome, so later close calls are idempotent.
  A completed failed outcome is replaced only by the next explicit call. Provider clients removed
  after successful close remain removed; only failed clients are present for retry.

### Deterministic RED evidence

Interaction owner regressions:

```bash
uv run pytest -q tests/test_interaction_composition.py \
  -k 'owner_loop_sai or falha_ao_agendar or task_factory_eager'
```

Before the coordinator existed:

```text
FFF
ImportError: cannot import name '_async_cleanup' from 'kairos_providers'
AssertionError: task factory do caller executou cleanup
3 failed, 11 deselected in 0.30s
```

Provider owner regressions:

```bash
uv run pytest -q tests/test_provider_composition.py \
  -k 'owner_loop_sai or falha_ao_agendar or task_factory_eager'
```

Before the coordinator existed:

```text
FFF
ImportError: cannot import name '_async_cleanup' from 'kairos_providers'
AssertionError: task factory do caller executou cleanup
3 failed, 19 deselected in 0.28s
```

The owner-loop tests wrap the production launcher and call `task.cancel()` before the task can run
its first coroutine step. They then cancel the owner waiter, allow its `asyncio.run()` main
coroutine to return, and join from a second event-loop thread. The assertions prove the supervisor
refused cancellation, the owner loop remained alive until release, cleanup executed on the owner
thread, the foreign waiter completed successfully, and every cleanup side effect occurred once.

The launch-failure tests inject an exception at the single scheduling boundary. The first caller
receives the exception, provider adapter creation remains rejected, and a bounded second call
completes cleanup once. The custom-factory tests install a hostile loop task factory; cleanup
succeeds without invoking it.

### GREEN and stability evidence

Each owner's three new regressions passed independently:

```text
tests/test_interaction_composition.py: 3 passed, 11 deselected in 0.24s
tests/test_provider_composition.py: 3 passed, 19 deselected in 0.22s
```

All six were then repeated five times:

```bash
for run in 1 2 3 4 5; do
  uv run pytest -q tests/test_interaction_composition.py \
    tests/test_provider_composition.py \
    -k 'owner_loop_sai or falha_ao_agendar or task_factory_eager' || exit
done
```

Result: five consecutive runs of `6 passed, 30 deselected` (0.32-0.33s each).

Both complete composition files, including all earlier idempotency, retry, cancellation, and
cross-loop regressions:

```bash
uv run pytest -q tests/test_interaction_composition.py tests/test_provider_composition.py
```

Result:

```text
36 passed in 0.54s
```

Broader lifecycle/composition/provider set:

```bash
uv run pytest -q tests/test_interaction_composition.py tests/test_interaction_service.py \
  tests/test_provider_composition.py tests/test_provider_gateway.py \
  tests/test_interaction_persistence.py tests/test_state.py
```

Result:

```text
111 passed in 1.06s
```

Lint, formatting, and whitespace:

```bash
uv run ruff check kairos_providers/_async_cleanup.py kairos_providers/composition.py \
  kairos_integration/composition.py tests/test_interaction_composition.py \
  tests/test_provider_composition.py
uv run ruff format --check kairos_providers/_async_cleanup.py \
  kairos_providers/composition.py kairos_integration/composition.py \
  tests/test_interaction_composition.py tests/test_provider_composition.py
git diff --check
```

Result: `All checks passed!`, all five Python files already formatted, and no whitespace errors.

Full suite, run once after the implementation:

```bash
uv run pytest -q
```

Result:

```text
1008 passed, 19 skipped, 5546 subtests passed in 15.30s
```

### Changed files

- `kairos_providers/_async_cleanup.py` (new shared coordinator)
- `kairos_providers/composition.py`
- `kairos_integration/composition.py`
- `tests/test_provider_composition.py`
- `tests/test_interaction_composition.py`
- `.superpowers/sdd/2026-08-26-interaction-service-persistence/task-6-report.md`

### Concerns

The supervisor intentionally makes normal event-loop shutdown wait for cleanup, so a resource
whose own `aclose()` never returns can prolong shutdown indefinitely; no timeout policy was added
because forcibly abandoning a loop-affine close would violate this round's ownership guarantee.
As with any asyncio resource, a caller that forcibly invokes `loop.close()` without the normal
pending-task cancellation/drain protocol cannot be made safe by library coordination. Supported
`asyncio.run()`/server shutdown is covered deterministically, and no blocking concern remains for
the requested lifecycle semantics.

## Fix Round 5

### Important finding: runtime cancellation was globally disabled

`_PersistentCleanupTask.cancel()` returned `False` for every caller. That kept normal
`asyncio.run()` shutdown from aborting loop-affine cleanup, but it also disabled cancellation used
inside the cleanup itself. In particular:

- `asyncio.timeout()` calls `cancel()` on the current task to interrupt the timed await;
- `asyncio.TaskGroup` cancels its parent task when a child fails so the body exits promptly;
- AnyIO-style cancellation scopes rely on the same normal runtime task-cancellation contract.

With the unconditional override, a timeout or failed TaskGroup child could leave the cleanup body
waiting until some unrelated event occurred, including forever when no fallback existed.

CPython 3.11.16 provides a stable distinction without inspecting caller stacks. `Runner.close()`
calls `asyncio.runners._cancel_all_tasks(loop)` after `run_until_complete(main)` has stopped the
loop. `_cancel_all_tasks()` calls `task.cancel()` while `loop.is_running()` is false, then resumes
the loop with `run_until_complete(gather(...))` to drain pending tasks. By contrast,
`Timeout._on_timeout()` and `TaskGroup._on_task_done()` call `cancel()` while the owner loop is
running.

The supervisor now refuses cancellation only while its loop is stopped. While the loop is
running, it delegates to `asyncio.Task.cancel(msg)`. This preserves Runner shutdown survival while
restoring native timeout, TaskGroup, and cancellation-scope behavior. There is no caller-stack
inspection and cleanup still runs on the initiating resource loop.

The prior interaction/provider tests that monkeypatched the launcher to call `task.cancel()` were
removed. They simulated cancellation from inside a running loop, which is exactly the runtime case
that must now work. Their shutdown coverage was replaced by regressions using an unmodified
`asyncio.run()` lifecycle.

### Deterministic regressions and RED evidence

New shared-coordinator coverage lives in `tests/test_async_cleanup.py`:

- `test_asyncio_run_shutdown_drena_cleanup_antes_do_primeiro_passo` schedules the close waiter,
  yields exactly once so the supervisor is reserved behind the main-task continuation, asserts the
  cleanup has not taken its first step, and then returns from the real `asyncio.run()` main
  coroutine. Runner shutdown drains the cleanup.
- `test_timeout_dentro_do_cleanup_cancela_task_supervisora` uses `asyncio.timeout(0)` around a
  bounded event wait. A short fallback makes the old defect fail deterministically instead of
  hanging the test process.
- `test_taskgroup_cancela_corpo_do_cleanup_quando_filho_falha` has a TaskGroup child fail while the
  cleanup body is waiting. It asserts the parent await receives `CancelledError`; a short fallback
  again turns cancellation swallowing into a bounded assertion failure.
- `test_cancelar_waiter_nao_cancela_cleanup_compartilhado` cancels one observer while another waits
  for the same blocked attempt, then verifies one cleanup attempt completes for the remaining
  waiter.

The composition integration regression
`test_shutdown_real_do_asyncio_run_drena_cleanup_no_loop_proprietario` additionally proves that a
pre-body cleanup launched during real Runner shutdown stays on the owner thread/loop, is observed
by a foreign-loop waiter, and performs usage flush, gateway close, and SQLite close exactly once.
The existing cross-loop waiter-cancellation regressions remain in place.

RED command before the production change:

```bash
uv run pytest -q tests/test_async_cleanup.py
```

Result:

```text
.FF.
FAILED test_timeout_dentro_do_cleanup_cancela_task_supervisora - assert False
FAILED test_taskgroup_cancela_corpo_do_cleanup_quando_filho_falha - assert False
2 failed, 2 passed in 0.20s
```

The Runner-shutdown and waiter-isolation tests already passed, confirming the defect was confined
to runtime cancellation rather than the shared-outcome design.

GREEN after delegating running-loop cancellation:

```text
4 passed in 0.13s
```

Focused cancellation/shutdown integration:

```bash
uv run pytest -q tests/test_async_cleanup.py tests/test_interaction_composition.py \
  -k 'asyncio_run or timeout_dentro or taskgroup_cancela or cancelar_waiter'
```

Result:

```text
6 passed, 12 deselected in 0.23s
```

### Preserved lifecycle guarantees

No attempt reservation, launch, publication, retry, resource-close, or waiter-observation path was
changed. The prior guarantees therefore remain covered by the focused lifecycle suite:

- scheduling failure publishes its outcome and permits retry;
- launch remains outside the state lock and bypasses custom/eager task factories;
- cleanup uses the initiating event loop and loop-affine provider resources;
- overlapping and cross-loop waiters share one attempt;
- canceling any waiter does not cancel the shared cleanup;
- failed attempts are retryable, successful attempts are idempotent, and provider clients are
  attempted at most once per attempt;
- usage flush, gateway close, SQLite close, exact-home composition, provider adapters, and
  construction rollback retain their existing semantics.

The optional completed-task/context retention minor was not changed in this round so it could not
obscure the cancellation correction.

### Verification

Focused lifecycle/provider/composition suite:

```bash
uv run pytest -q tests/test_async_cleanup.py tests/test_interaction_composition.py \
  tests/test_interaction_service.py tests/test_provider_composition.py \
  tests/test_provider_gateway.py tests/test_interaction_persistence.py tests/test_state.py
```

Result:

```text
114 passed in 0.91s
```

Lint, owned-file formatting, and whitespace:

```bash
uv run ruff check .
uv run ruff format kairos_providers/_async_cleanup.py tests/test_async_cleanup.py \
  tests/test_interaction_composition.py tests/test_provider_composition.py
uv run ruff format --check kairos_providers/_async_cleanup.py tests/test_async_cleanup.py \
  tests/test_interaction_composition.py tests/test_provider_composition.py
git diff --check
```

Result: `All checks passed!`, the four round-owned Python files were unchanged/already formatted,
and `git diff --check` produced no output. A repository-wide `ruff format --check .` also identified
six pre-existing formatting differences in files outside this round; they were deliberately not
rewritten as part of the cancellation fix.

Full suite, run once after the final code and tests:

```bash
uv run pytest -q
```

Result:

```text
1011 passed, 19 skipped, 5546 subtests passed in 15.42s
```

### Changed files

- `kairos_providers/_async_cleanup.py`
- `tests/test_async_cleanup.py` (new)
- `tests/test_interaction_composition.py`
- `tests/test_provider_composition.py`
- `.superpowers/sdd/2026-08-26-interaction-service-persistence/task-6-report.md`
