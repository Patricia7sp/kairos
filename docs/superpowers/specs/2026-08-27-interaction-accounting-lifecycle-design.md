# Interaction Accounting and Lifecycle Design

## Status

Approved in conversation on 2026-08-27.

## Goal

Close the three load-bearing gaps left after the canonical `InteractionService`
merge: a successful terminal event must imply durable usage, monetary accounting
must be internally consistent, and composed-service shutdown must not race with
admitted turns.

## Scope

This change covers:

- turn admission and graceful shutdown for `ComposedInteractionService`;
- the durability contract between transcript, usage, and terminal events;
- canonical token and cost semantics;
- route-neutral session cost aggregation;
- focused protocol changes needed to represent durability failures safely.

It does not add provider-side actual billing ingestion, distributed job queues,
partitioned persistence workers, or a new database.

## Design choices

### Rejected: report a successful but non-durable terminal state

One option was to emit `turn_end` with a `usage_pending` flag after a failed
flush. This keeps the conversation responsive but makes a completed turn mean
different things depending on a secondary flag and still loses accounting on a
crash. It is rejected because the approved invariant is stronger: normal
terminal success implies durable transcript and usage.

### Rejected: persist every provider usage event

Writing each streamed usage snapshot would avoid a final batch failure but
would reintroduce write amplification and SQLite contention. It is rejected.
Provider usage remains accumulated in memory and is written once per terminal
turn.

### Selected: terminal durability boundary plus an admission gate

Each composed turn enters an admission gate before durable ownership and leaves
it only after provider cleanup and terminal persistence. Shutdown closes
admission, waits for all admitted turns, performs the final usage drain, then
closes persistence and provider resources. A terminal event is emitted only
after the turn's usage batch is durable.

## Turn admission and shutdown

`ComposedInteractionService` owns an async admission gate with three states:

- `OPEN`: new turns may enter;
- `DRAINING`: no new turn may enter; admitted turns continue;
- `CLOSED`: resources are closed and every new turn is rejected.

Admission happens at the public `InteractionService.stream()` boundary, before
the producer task can acquire a session lease or persist the user message. The
admission token covers the entire async iterator lifetime, including downstream
backpressure, provider iterator cleanup, usage persistence, and typed terminal
delivery. Closing or cancelling the public iterator releases its token only
after owned cleanup finishes.

`ComposedInteractionService.aclose()` atomically changes `OPEN` to `DRAINING`,
then awaits the active-turn count reaching zero. Concurrent `aclose()` callers
share the existing cleanup attempt. After the drain:

1. flush any recoverable usage batch;
2. close the persistence worker;
3. close the provider gateway;
4. close the read-side SQLite connection;
5. publish `CLOSED`.

No new turn is admitted after `DRAINING` begins. A stream request rejected in
`DRAINING` or `CLOSED` raises a transport-neutral
`InteractionServiceUnavailableError`; WebSocket maps it to close code `1012`
and CLI prints a normalized error to stderr with exit status 1.

The gate must be cancellation-safe: cancellation of one shutdown waiter does
not reopen admission or cancel the shared drain. A turn that was already
admitted is not cancelled merely because shutdown started.

## Terminal durability contract

The durable terminal sequence for both success and provider error is:

1. persist the terminal assistant/error transcript;
2. queue exactly one aggregate usage/cost delta for the turn;
3. flush the usage batch transactionally;
4. emit the canonical `usage` event when usage or cost is present;
5. emit `turn_end` or the provider-derived `turn_error`.

If step 3 fails, the repository restores the batch to its in-memory queue. The
service must not emit the normal provider terminal. It emits one canonical
`turn_error` with kind `persistence`, `retryable=true`, and a normalized message
that contains no SQL text or credential data. The transcript remains readable,
but callers can distinguish that the turn did not cross the accounting
durability boundary.

The pending batch remains eligible for a later turn-boundary or shutdown drain.
If the final shutdown drain also fails, `aclose()` fails and remains retryable;
the persistence connection is not closed until the batch is durable. This
matches the existing cleanup retry contract.

There is still one batch flush per terminal turn, never one write per provider
delta.

## Canonical token semantics

`TokenUsage` fields have these meanings:

- `input_tokens`: total provider-reported input/prompt tokens;
- `output_tokens`: total provider-reported output/completion tokens;
- `cache_read_tokens`: subset of `input_tokens`, never additive to it;
- `reasoning_tokens`: subset of `output_tokens`, never additive to it;
- `cache_write_tokens`: independently reported accounting detail when present;
- `api_call_count`: number of billed adapter attempts.

Accordingly, basic token total is `input_tokens + output_tokens`.
`cache_read_tokens` and `reasoning_tokens` describe composition of those totals
and must not be added again. The existing `TokenUsage.total` property must be
corrected to follow this definition.

## Estimated and actual cost

`ModelPrice.prompt`, `completion`, and `request` are USD-denominated normalized
unit prices from the catalog. Estimated cost is:

```text
prompt component     = input_tokens  * prompt price
completion component = output_tokens * completion price
request component    = api_call_count * request price
estimated cost       = sum of every known applicable component
```

Cache and reasoning details do not add another charge unless the catalog later
introduces distinct cache/reasoning price fields. This design does not infer
such prices.

Request price is charged when known even if no token usage event was received.
If a component has non-zero observed quantity but its required price is
unknown, the full estimate status is `unknown`; a partial numeric estimate must
not be presented as complete. Zero quantity does not require a price.

All internal calculations use `Decimal`. Database and JSON boundaries continue
to use the existing numeric fields, converting only at the boundary. Catalog
price and pricing provenance are frozen with the prepared adapter for the turn.

`actual_cost_usd` is populated only from a trustworthy upstream billed-cost
event. Until such an event exists it remains `NULL`. Estimated and actual costs
are independent:

- repeated `estimated` values sum into `estimated_cost_usd`;
- repeated `actual` values sum into `actual_cost_usd` even when no estimate is
  present;
- mixing estimated-only and actual-only records preserves both numeric sums;
- `cost_status` is `actual` only when every billed component has actual cost,
  `estimated` when a complete estimate exists but actual is incomplete, and
  `unknown` when neither complete actual nor complete estimate exists;
- `cost_source` is retained only when all merged values share the same source.

## Route-level and session-level aggregation

`session_model_usage` remains route-specific. Its primary key continues to
separate session, model, billing provider, base URL, billing mode, and task.
Route rows may retain their route metadata and aggregate token/cost fields.

The `sessions` row is a route-neutral summary. Token and monetary totals may be
summed across routes, but route identity fields must not claim that the summary
belongs to the last route processed:

- with exactly one distinct route, retain its provider/base URL/mode;
- after more than one distinct route contributes, set summary route identity
  to `billing_provider='mixed'`, `billing_base_url=''`, and
  `billing_mode='mixed'`;
- once mixed, later updates remain mixed.

No schema migration is required for this representation. Existing rows remain
readable.

## Components

### `InteractionAdmissionGate`

A focused async component owned by composed services. It exposes an async
context manager for turn admission and a cancellation-resistant `drain()` for
shutdown. It contains no database or provider logic.

### `InteractionService`

Accepts an optional admission gate. Directly injected services without a gate
retain their existing caller-owned behavior. It treats usage flush failure as a
typed persistence terminal error and uses corrected cost calculation.

### `SQLiteAsyncInteractionPersistence`

Remains the single ordered writer. Its close path is invoked only after the
admission gate drains. A failed flush leaves the worker and connection open for
retry.

### `UsageRepository`

Corrects cost merging and route-neutral session summaries. Batch extraction,
transactional write, and queue restoration remain unchanged.

### WebSocket and CLI translators

They translate the new normalized service-unavailable/persistence errors
without exposing internal exceptions. No transport implements its own shutdown
or accounting policy.

## Error handling

- Flush failure: restore batch, emit typed persistence `turn_error`, do not emit
  the original normal terminal event.
- Shutdown during active turns: reject new turns and await admitted turns.
- Shutdown waiter cancellation: shared drain continues.
- Final drain failure: `aclose()` raises and may be retried; persistence remains
  open.
- Unknown pricing: persist explicit `unknown` status and `NULL` cost rather than
  a partial estimate.
- Provider error: preserve the normalized provider error only after usage is
  durable; if persistence fails, persistence error takes terminal precedence.

## Testing strategy

Tests must cover:

- flush failure produces persistence `turn_error`, no `turn_end`, and retains
  the pending batch;
- a later boundary or shutdown successfully drains the retained batch;
- shutdown rejects new turns, waits for a backpressured active turn, then closes
  in order;
- cancellation of a shutdown waiter does not cancel the drain;
- cache/reasoning detail tokens are not double-counted;
- request-only pricing is charged with absent token usage;
- unknown required price produces no partial estimate;
- actual-only and mixed estimated/actual merges remain correctly classified;
- one route retains identity and multiple routes produce a sticky `mixed`
  session summary;
- existing route rows and legacy sessions remain readable;
- WebSocket and CLI expose normalized persistence/service-unavailable errors
  without secrets;
- the full Python, Web, lint, formatting, Docker, and integration gates remain
  green.

## Success criteria

- Every emitted normal terminal event corresponds to durable transcript and
  usage/cost accounting.
- No admitted turn can outlive persistence/gateway shutdown.
- Cost estimates never double-count detail tokens or omit a known per-request
  charge.
- Session summaries never misrepresent a mixed-route aggregate as one route.
- Existing protocol/authentication, retry, ownership, and cleanup guarantees
  remain intact.
