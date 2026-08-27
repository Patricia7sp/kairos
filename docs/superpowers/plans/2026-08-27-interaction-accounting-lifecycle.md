# Interaction Accounting and Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make normal interaction terminals imply durable usage, correct token/cost accounting, and drain admitted turns before composed-service shutdown.

**Architecture:** Add a focused admission gate around the complete public stream lifetime, keep the existing single ordered SQLite writer as the durability boundary, and centralize monetary calculation/aggregation semantics. WebSocket and CLI remain thin translators of typed service errors.

**Tech Stack:** Python 3.11, asyncio, dataclasses, Decimal, SQLite, FastAPI/WebSocket, pytest/unittest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-27-interaction-accounting-lifecycle-design.md`

## Global Constraints

- A normal `turn_end` or provider-derived `turn_error` is emitted only after transcript and usage/cost accounting are durable.
- Streaming usage remains coalesced to one batch write per terminal turn; never write once per provider delta.
- `input_tokens` and `output_tokens` are totals; `cache_read_tokens` and `reasoning_tokens` are non-additive subsets.
- Known per-request price is charged for every adapter attempt even when no token usage event exists.
- Unknown required price produces `cost_status='unknown'` and no partial estimated total.
- No new turn is admitted after composed shutdown begins; admitted turns retain ownership until provider cleanup and terminal durability finish.
- Existing sessions, route-level usage rows, WebSocket authentication/protocol behavior, CLI surfaces, retry safety, and credential redaction remain compatible.

---

### Task 1: Canonical token and estimated-cost semantics

**Files:**
- Modify: `kairos_providers/base.py`
- Create: `kairos_integration/cost_accounting.py`
- Modify: `kairos_integration/interaction_service.py`
- Test: `tests/test_interaction_cost_accounting.py`
- Modify: `tests/test_interaction_service.py`

**Interfaces:**
- Produces: `estimate_interaction_cost(price: ModelPrice, usage: TokenUsage | None, attempts: int, source: str | None) -> InteractionCost`.
- Preserves: `InteractionCost` transport/storage boundary and frozen `PreparedProviderAdapter.price`.
- Consumes: `ModelPrice.prompt`, `ModelPrice.completion`, and `ModelPrice.request` as `Decimal | None`.

- [ ] **Step 1: Write failing token-detail and request-price tests**

```python
from decimal import Decimal

from kairos_integration.cost_accounting import estimate_interaction_cost
from kairos_providers import ModelPrice, TokenUsage


def test_detail_tokens_are_not_added_to_billable_totals():
    result = estimate_interaction_cost(
        ModelPrice(prompt=Decimal("0.001"), completion=Decimal("0.002")),
        TokenUsage(
            input_tokens=100,
            output_tokens=40,
            cache_read_tokens=60,
            reasoning_tokens=10,
        ),
        attempts=1,
        source="catalog:test",
    )
    assert result.estimated_usd == 0.18
    assert result.status == "estimated"
    assert TokenUsage(input_tokens=100, output_tokens=40, reasoning_tokens=10).total == 140


def test_request_price_is_charged_without_usage():
    result = estimate_interaction_cost(
        ModelPrice(request=Decimal("0.03")),
        usage=None,
        attempts=2,
        source="catalog:test",
    )
    assert result.estimated_usd == 0.06
    assert result.status == "estimated"
```

- [ ] **Step 2: Write failing unknown-required-price test**

```python
def test_missing_price_for_observed_component_does_not_publish_partial_estimate():
    result = estimate_interaction_cost(
        ModelPrice(prompt=Decimal("0.001"), request=Decimal("0.03")),
        TokenUsage(input_tokens=100, output_tokens=40),
        attempts=2,
        source="catalog:test",
    )
    assert result.estimated_usd is None
    assert result.status == "unknown"
```

- [ ] **Step 3: Run the focused tests to verify RED**

Run: `uv run pytest -q tests/test_interaction_cost_accounting.py`

Expected: FAIL because `kairos_integration.cost_accounting` does not exist and the old token total adds reasoning twice.

- [ ] **Step 4: Implement the Decimal calculator and correct token total**

```python
def estimate_interaction_cost(
    price: ModelPrice,
    usage: TokenUsage | None,
    attempts: int,
    source: str | None,
) -> InteractionCost:
    quantities = (
        (usage.input_tokens if usage is not None else 0, price.prompt),
        (usage.output_tokens if usage is not None else 0, price.completion),
        (attempts, price.request),
    )
    if any(quantity > 0 and unit_price is None for quantity, unit_price in quantities):
        return InteractionCost(status="unknown", source=source)
    total = sum(
        Decimal(quantity) * unit_price
        for quantity, unit_price in quantities
        if quantity > 0 and unit_price is not None
    )
    if not any(quantity > 0 for quantity, _ in quantities):
        return InteractionCost(status="unknown", source=source)
    return InteractionCost(
        estimated_usd=float(total),
        status="estimated",
        source=source,
    )
```

Change `TokenUsage.total` to `input_tokens + output_tokens`. Replace the private `_cost_for` implementation in `InteractionService` with the new calculator.

- [ ] **Step 5: Verify calculator and interaction regressions**

Run: `uv run pytest -q tests/test_interaction_cost_accounting.py tests/test_interaction_service.py tests/test_interaction_retry.py`

Expected: PASS with cache/reasoning details excluded from additive cost and retry request pricing included.

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check kairos_integration/cost_accounting.py kairos_integration/interaction_service.py kairos_providers/base.py tests/test_interaction_cost_accounting.py tests/test_interaction_service.py`

```bash
git add kairos_providers/base.py kairos_integration/cost_accounting.py \
  kairos_integration/interaction_service.py tests/test_interaction_cost_accounting.py \
  tests/test_interaction_service.py
git commit -m "fix(interaction): corrige semantica de tokens e custos"
```

### Task 2: Correct route and session cost aggregation

**Files:**
- Modify: `kairos_state/repositories/usage.py`
- Test: `tests/test_interaction_persistence.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: route-level `TokenDelta` values with independent estimated/actual costs.
- Produces: correct `_merge_cost_fields(...) -> tuple[float | None, float | None, str, str | None]` and sticky mixed-route session identity.
- Preserves: `session_model_usage` composite primary key and existing schema.

- [ ] **Step 1: Write failing actual-only and mixed estimated/actual tests**

```python
def test_actual_only_costs_sum_without_requiring_estimates(db):
    repo = UsageRepository(db)
    route = billing_route("s1", provider="openrouter")
    repo.queue(route, TokenDelta(actual_cost_usd=0.02, cost_status="actual", cost_source="upstream"))
    repo.flush(now=1)
    repo.queue(route, TokenDelta(actual_cost_usd=0.03, cost_status="actual", cost_source="upstream"))
    repo.flush(now=2)
    row = db.execute(
        "SELECT estimated_cost_usd, actual_cost_usd, cost_status FROM session_model_usage"
    ).fetchone()
    assert row["estimated_cost_usd"] is None
    assert row["actual_cost_usd"] == 0.05
    assert row["cost_status"] == "actual"


def test_estimated_and_actual_costs_keep_independent_sums(db):
    repo = UsageRepository(db)
    route = billing_route("s1", provider="openrouter")
    repo.queue(route, TokenDelta(estimated_cost_usd=0.04, cost_status="estimated", cost_source="catalog"))
    repo.flush(now=1)
    repo.queue(route, TokenDelta(actual_cost_usd=0.05, cost_status="actual", cost_source="upstream"))
    repo.flush(now=2)
    row = db.execute(
        "SELECT estimated_cost_usd, actual_cost_usd, cost_status, cost_source FROM session_model_usage"
    ).fetchone()
    assert (row["estimated_cost_usd"], row["actual_cost_usd"]) == (0.04, 0.05)
    assert row["cost_status"] == "estimated"
    assert row["cost_source"] is None
```

- [ ] **Step 2: Write failing mixed-route session-summary test**

```python
def test_session_summary_becomes_sticky_mixed_after_distinct_routes(db):
    repo = UsageRepository(db)
    repo.queue(billing_route("s1", provider="openai", base_url="https://api.openai.com"), TokenDelta(input_tokens=2))
    repo.flush(now=1)
    repo.queue(billing_route("s1", provider="openrouter", base_url="https://openrouter.ai/api/v1"), TokenDelta(input_tokens=3))
    repo.flush(now=2)
    row = db.execute(
        "SELECT billing_provider, billing_base_url, billing_mode, input_tokens FROM sessions WHERE id='s1'"
    ).fetchone()
    assert tuple(row) == ("mixed", "", "mixed", 5)
    repo.queue(billing_route("s1", provider="openai", base_url="https://api.openai.com"), TokenDelta(input_tokens=7))
    repo.flush(now=3)
    row = db.execute(
        "SELECT billing_provider, billing_base_url, billing_mode, input_tokens FROM sessions WHERE id='s1'"
    ).fetchone()
    assert tuple(row) == ("mixed", "", "mixed", 12)
```

- [ ] **Step 3: Run focused tests to verify RED**

Run: `uv run pytest -q tests/test_interaction_persistence.py tests/test_state.py -k 'cost or mixed'`

Expected: FAIL because actual-only merges become unknown and the session summary uses the last route.

- [ ] **Step 4: Implement independent monetary merging**

Implement helpers that sum each numeric channel independently:

```python
def _sum_optional(current: float | None, incoming: float | None) -> float | None:
    if current is None:
        return incoming
    if incoming is None:
        return current
    return current + incoming
```

Derive status as `actual` only when merged inputs are actual-only, `estimated` when a complete estimate participates, and `unknown` only when no complete monetary channel exists or an unknown billed component was recorded. Preserve `cost_source` only when both non-null sources match.

- [ ] **Step 5: Implement sticky route-neutral session identity**

Before updating `sessions`, compare the current route identity with the incoming route. Use:

```python
def _session_route_identity(row, incoming: BillingRoute) -> tuple[str, str, str]:
    current = (row["billing_provider"], row["billing_base_url"], row["billing_mode"])
    new = (incoming.billing_provider, incoming.billing_base_url, incoming.billing_mode)
    if current[0] == "mixed" or (current[0] is not None and current != new):
        return ("mixed", "", "mixed")
    return new
```

Select route fields together with cost fields in the existing session update transaction.

- [ ] **Step 6: Verify repository and schema compatibility**

Run: `uv run pytest -q tests/test_interaction_persistence.py tests/test_state.py tests/test_schema.py`

Expected: PASS; route rows remain distinct and legacy session rows remain readable.

- [ ] **Step 7: Lint and commit**

Run: `uv run ruff check kairos_state/repositories/usage.py tests/test_interaction_persistence.py tests/test_state.py`

```bash
git add kairos_state/repositories/usage.py tests/test_interaction_persistence.py tests/test_state.py
git commit -m "fix(state): corrige agregacao de custos e rotas"
```

### Task 3: Enforce the terminal usage-durability boundary

**Files:**
- Modify: `kairos_integration/interaction_contract.py`
- Modify: `kairos_integration/interaction_service.py`
- Modify: `kairos_integration/event_protocol.py`
- Modify: `kairos_web/server.py`
- Modify: `kairos_cli/chat.py`
- Test: `tests/test_interaction_service.py`
- Test: `tests/test_interaction_composition.py`
- Test: `tests/test_web_chat_transport.py`
- Test: `tests/test_cli_chat.py`

**Interfaces:**
- Produces: `InteractionServiceError(error_kind: str, message: str, retryable: bool)` and `InteractionPersistenceError()` with `error_kind='persistence'`, a normalized message, and `retryable=True`.
- Consumes: `SQLiteAsyncInteractionPersistence.flush_usage()` and queue-restoration behavior from `UsageRepository.flush()`.
- Guarantees: a failed flush cannot be followed by normal `turn_end` or the original provider terminal.

- [ ] **Step 1: Write failing successful-turn flush test**

```python
async def test_flush_failure_replaces_normal_terminal_with_persistence_error():
    service, persistence = service_with_failing_usage_flush(events=[text("ok"), usage(2, 1), finish("stop")])
    events = [event async for event in service.stream(envelope())]
    assert [event.kind for event in events] == ["turn_start", "delta", "turn_error"]
    assert events[-1].error_kind == "persistence"
    assert events[-1].retryable is True
    assert persistence.usage.pending_count() == 1
```

- [ ] **Step 2: Write failing provider-error precedence test**

```python
async def test_flush_failure_takes_terminal_precedence_over_provider_error():
    service, persistence = service_with_failing_usage_flush(
        events=[text("partial")], error=retryable_network_error()
    )
    events = [event async for event in service.stream(envelope())]
    assert [event.kind for event in events] == ["turn_start", "delta", "turn_error"]
    assert events[-1].error_kind == "persistence"
    assert "database" not in events[-1].error.lower()
    assert persistence.usage.pending_count() == 1
```

- [ ] **Step 3: Write failing recovery test**

```python
async def test_later_turn_drains_batch_restored_by_failed_terminal_flush(tmp_path):
    service = composed_service_with_one_flush_failure(tmp_path)
    first = [event async for event in service.stream(envelope(session="s1"))]
    assert first[-1].error_kind == "persistence"
    second = [event async for event in service.stream(envelope(session="s2"))]
    assert second[-1].kind == "turn_end"
    assert durable_usage_rows(tmp_path) == [("s1", 1), ("s2", 1)]
```

- [ ] **Step 4: Run focused tests to verify RED**

Run: `uv run pytest -q tests/test_interaction_service.py tests/test_interaction_composition.py -k 'flush or persistence_error'`

Expected: FAIL because `_flush_usage()` swallows the exception and emits normal terminal events.

- [ ] **Step 5: Implement typed durability failure**

Remove exception swallowing from `_flush_usage()`. Add transport-neutral service errors and teach `InteractionEvent.turn_error()` to serialize them:

```python
class InteractionServiceError(Exception):
    def __init__(self, error_kind: str, message: str, *, retryable: bool) -> None:
        self.error_kind = error_kind
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class InteractionPersistenceError(InteractionServiceError):
    def __init__(self) -> None:
        super().__init__(
            "persistence",
            "não foi possível persistir a contabilidade do turno",
            retryable=True,
        )


# In InteractionEvent.turn_error(), before the generic fallback:
if isinstance(error, InteractionServiceError):
    return cls(
        kind=InteractionEventKind.TURN_ERROR,
        error=error.message,
        error_kind=error.error_kind,
        retryable=error.retryable,
    )


try:
    await self._flush_usage()
except Exception:
    logger.exception("falha ao persistir contabilidade do turno")
    yield InteractionEvent.turn_error(InteractionPersistenceError())
    return
```

Do not include the caught exception text in the canonical event. Keep transcript persistence before this boundary and preserve the queued batch.

- [ ] **Step 6: Update WebSocket and CLI error translation tests**

Add assertions that Web protocol v1 emits:

```python
assert ws.receive_json() == {
    "type": "turn_error",
    "protocol": 1,
    "session_id": "s1",
    "error": "não foi possível persistir a contabilidade do turno",
    "error_kind": "persistence",
    "retryable": True,
}
```

Add CLI human assertions for stderr and status 1, and NDJSON assertions for the same safe fields.

- [ ] **Step 7: Verify interaction and transport suites**

Run: `uv run pytest -q tests/test_interaction_service.py tests/test_interaction_composition.py tests/test_web_chat_transport.py tests/test_cli_chat.py`

Expected: PASS with no normal terminal after flush failure.

- [ ] **Step 8: Lint and commit**

Run: `uv run ruff check kairos_integration kairos_web/server.py kairos_cli/chat.py tests/test_interaction_service.py tests/test_interaction_composition.py tests/test_web_chat_transport.py tests/test_cli_chat.py`

```bash
git add kairos_integration/interaction_contract.py kairos_integration/interaction_service.py \
  kairos_integration/event_protocol.py kairos_web/server.py kairos_cli/chat.py \
  tests/test_interaction_service.py tests/test_interaction_composition.py \
  tests/test_web_chat_transport.py tests/test_cli_chat.py
git commit -m "fix(interaction): exige uso duravel antes do terminal"
```

### Task 4: Drain admitted turns before composed shutdown

**Files:**
- Create: `kairos_integration/admission.py`
- Modify: `kairos_integration/interaction_contract.py`
- Modify: `kairos_integration/interaction_service.py`
- Modify: `kairos_integration/composition.py`
- Modify: `kairos_web/server.py`
- Modify: `kairos_cli/chat.py`
- Test: `tests/test_interaction_admission.py`
- Modify: `tests/test_interaction_composition.py`
- Modify: `tests/test_web_chat_transport.py`
- Modify: `tests/test_cli_chat.py`

**Interfaces:**
- Produces: `InteractionAdmissionGate.admit() -> AsyncContextManager[None]`, `InteractionAdmissionGate.drain() -> Awaitable[None]`, and `InteractionServiceUnavailableError`.
- Consumes: `InteractionServiceError` from Task 3 and existing persistent cleanup coordination in `ComposedInteractionService.aclose()`; `InteractionServiceUnavailableError` subclasses the former with `error_kind='unavailable'`, normalized message `serviço de interação indisponível`, and `retryable=True`.
- Guarantees: the admission token spans public iterator creation through provider cleanup, durability, terminal delivery, or explicit iterator close.

- [ ] **Step 1: Write failing gate unit tests**

```python
async def test_drain_rejects_new_turns_and_waits_for_active_turn():
    gate = InteractionAdmissionGate()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def active_turn():
        async with gate.admit():
            entered.set()
            await release.wait()

    turn = asyncio.create_task(active_turn())
    await entered.wait()
    drain = asyncio.create_task(gate.drain())
    await asyncio.sleep(0)
    with pytest.raises(InteractionServiceUnavailableError):
        async with gate.admit():
            pass
    assert not drain.done()
    release.set()
    await turn
    await drain
```

- [ ] **Step 2: Write failing shutdown/stream integration test**

```python
async def test_composed_close_waits_for_backpressured_turn_before_resources(tmp_path):
    service, provider = composed_service_with_blocked_second_event(tmp_path)
    stream = service.stream(envelope())
    assert (await anext(stream)).kind == "turn_start"
    close_task = asyncio.create_task(service.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()
    with pytest.raises(InteractionServiceUnavailableError):
        await anext(service.stream(envelope(session="later")))
    await stream.aclose()
    await close_task
    assert provider.cleanup_complete
    assert service.persistence_closed
```

- [ ] **Step 3: Write failing shutdown-waiter cancellation test**

```python
async def test_cancelled_close_waiter_does_not_cancel_shared_drain(tmp_path):
    service, turn_release = service_with_active_turn(tmp_path)
    first = asyncio.create_task(service.aclose())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(service.aclose())
    turn_release.set()
    await second
    assert service.resources_closed_once
```

- [ ] **Step 4: Run focused tests to verify RED**

Run: `uv run pytest -q tests/test_interaction_admission.py tests/test_interaction_composition.py -k 'drain or backpressured or close_waiter'`

Expected: FAIL because no admission gate exists and close can race an active turn.

- [ ] **Step 5: Implement `InteractionAdmissionGate`**

Use an `asyncio.Condition` with state and active count. Do not hold the condition across user/provider work:

```python
class AdmissionState(StrEnum):
    OPEN = "open"
    DRAINING = "draining"
    CLOSED = "closed"


class InteractionServiceUnavailableError(InteractionServiceError):
    def __init__(self) -> None:
        super().__init__(
            "unavailable",
            "serviço de interação indisponível",
            retryable=True,
        )


class InteractionAdmissionGate:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._state = AdmissionState.OPEN
        self._active = 0

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._state is not AdmissionState.OPEN:
                raise InteractionServiceUnavailableError()
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                if self._active == 0:
                    self._condition.notify_all()

    async def drain(self) -> None:
        async with self._condition:
            if self._state is AdmissionState.OPEN:
                self._state = AdmissionState.DRAINING
            await self._condition.wait_for(lambda: self._active == 0)

    async def mark_closed(self) -> None:
        async with self._condition:
            self._state = AdmissionState.CLOSED
            self._condition.notify_all()
```

- [ ] **Step 6: Hold admission for the complete public stream lifetime**

Wrap the existing demand-paced producer lifecycle:

```python
async def stream(self, envelope):
    if self._admission is None:
        async for event in self._stream_with_owned_producer(envelope):
            yield event
        return
    async with self._admission.admit():
        async for event in self._stream_with_owned_producer(envelope):
            yield event
```

The `finally` paths must still cancel/join the producer and provider iterator before `admit()` exits.

- [ ] **Step 7: Integrate composed shutdown ordering**

Composition creates one gate and injects it into the service. `_close_attempt()` performs:

```python
await self._admission.drain()
await self._persistence.aclose()
await self.gateway.aclose()
self._connection.close()
await self._admission.mark_closed()
```

If persistence or gateway close fails, keep the gate in `DRAINING` and allow another `aclose()` attempt; never reopen admission.

- [ ] **Step 8: Map service-unavailable errors in WebSocket and CLI**

WebSocket closes with code `1012` and no internal reason. CLI prints `serviço de interação indisponível` to stderr and returns 1. Add literal assertions:

```python
assert websocket.close_code == 1012
assert "database" not in websocket.close_reason.lower()

assert cli_result.exit_code == 1
assert cli_result.stderr == "serviço de interação indisponível\n"
```

- [ ] **Step 9: Verify lifecycle, interaction, Web, and CLI suites**

Run: `uv run pytest -q tests/test_interaction_admission.py tests/test_interaction_composition.py tests/test_interaction_turn_ownership.py tests/test_interaction_service.py tests/test_web_chat_transport.py tests/test_cli_chat.py`

Expected: PASS; new admission is rejected during drain, active streams finish/close before resources, and transports remain safe.

- [ ] **Step 10: Run the complete Python, frontend, and container gates**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
git diff --check
uv run pytest -q --deselect tests/test_container.py::RealImageTests
for frontend in ui-tui web apps/desktop; do
  npm install --prefix "$frontend"
  npx --prefix "$frontend" tsc --noEmit -p "$frontend"
  npm test --prefix "$frontend"
done
docker build --check .
docker build -t kairos:test .
printf 'FROM kairos:test\nRUN printf %s > /opt/kairos/.venv/bin/kairos && chmod +x /opt/kairos/.venv/bin/kairos\n' \
  "'#!/bin/bash\ncase \"\$1\" in\n  gateway) echo \"GATEWAY uid=\$(id -u)\"; sleep 3 ;;\n  dashboard) echo \"DASH uid=\$(id -u)\"; sleep 3 ;;\n  boom) exit 42 ;;\n  echo) cat ;;\n  *) echo \"CLI uid=\$(id -u) args=\$*\" ;;\nesac\n'" \
  > Dockerfile.stub
docker build -t kairos:stub -f Dockerfile.stub .
uv run pytest -q tests/test_container.py::RealImageTests
```

Expected: Ruff, whitespace, Python, TypeScript, frontend, Docker build, and real-image integration gates all pass with zero failures.

- [ ] **Step 11: Commit the shutdown lifecycle**

```bash
git add kairos_integration/admission.py kairos_integration/interaction_contract.py \
  kairos_integration/interaction_service.py kairos_integration/composition.py \
  kairos_web/server.py kairos_cli/chat.py tests/test_interaction_admission.py \
  tests/test_interaction_composition.py tests/test_web_chat_transport.py tests/test_cli_chat.py
git commit -m "fix(interaction): drena turnos antes do shutdown"
```
