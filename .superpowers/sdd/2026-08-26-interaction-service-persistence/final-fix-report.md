# Final interaction persistence fix wave

Date: 2026-08-27

Branch: `feat/interaction-service`

Worktree: `/home/paty7sp/projetos/kairos/.worktrees/providers-chat-completion`

## Outcome

All five Important final-review findings were addressed as one coherent wave. The final bounded full suite passes with **1,085 passed, 19 skipped, and 5,546 subtests passed**. Ruff lint, Ruff formatting verification, and `git diff --check` are clean.

The existing turn-ownership, retry, iterator-cleanup, lifecycle, protocol, CLI, and WebSocket behavior remains covered by the full suite. No credential secret is added to the public interaction contract, protocol JSON, transcript metadata, usage rows, or logs.

## Finding-to-code-and-test mapping

### 1. Selection parameters and non-sticky message overrides

Implementation:

- `kairos_providers/selection.py` extends `ModelSelectionContext` with message, conversation, activity, profile, and global parameter layers.
- `kairos_integration/selection_context.py` loads persisted conversation parameters and configured profile/global/activity parameters instead of dropping them.
- `kairos_integration/interaction_service.py` recursively merges the layers in documented low-to-high precedence: global, profile, activity, conversation, message. The effective merged mapping is frozen in the turn snapshot and sent to the adapter.
- `_persist_user` no longer calls `SessionRepository.set_selection`. A message override and its parameters are therefore turn-local and cannot replace the explicit conversation selection. Message rows continue to persist the effective provider/model/reason metadata.

Tests:

- `tests/test_interaction_selection.py::SelectionContextLoaderTests::test_contexto_retem_parametros_de_todas_as_camadas`
- `tests/test_interaction_service.py::InteractionServiceTests::test_snapshot_mescla_parametros_sem_tornar_override_de_mensagem_sticky`
- Existing snapshot immutability, selection precedence, transcript metadata, CLI, and Web transport tests remain green.

### 2. Frozen credential boundary across retries

Implementation:

- `kairos_providers/gateway.py` adds `PreparedProviderAdapter`, an opaque per-turn adapter factory. `ProviderGateway.prepare()` resolves the selected credential reference and secret-derived adapter configuration once, then freezes that configuration with the provider/model, non-secret billing route, and catalog price.
- `kairos_integration/interaction_service.py` prepares once before the first attempt and calls `prepared.create_adapter()` for every retry. Vault rotation between attempts cannot change the credential used by that turn.
- Only the non-secret credential identifier is retained in the internal snapshot. `event_protocol.py` still omits all credential metadata. The prepared factory has a redacted `repr`; secret material is confined to private adapter configuration and is not handed to transcript/usage repositories.
- A legacy prepared wrapper preserves injected gateways that expose only `create_adapter()`.

Tests:

- `tests/test_interaction_retry.py::InteractionRetryTests::test_retry_reuses_prepared_credential_after_vault_rotation` rotates the vault between attempts and proves both adapter factories receive the original secret while the protocol and snapshot representation do not expose it.
- Existing retry, iterator cleanup, cancellation, and error-sanitization coverage remains green.

### 3. Usage drain before service close, with recovery

Implementation:

- `kairos_integration/interaction_service.py` queues one aggregate usage delta per completed turn and performs one batched turn-boundary flush after transcript persistence, before emitting the terminal usage/end or usage/error sequence.
- `UsageRepository.flush()` retains its atomic batch and restores every route to the queue on failure. A later turn boundary retries the retained batch.
- `kairos_integration/composition.py` performs the final drain through the persistence owner during `aclose()`, preserving retryable shutdown semantics.
- Streaming still does not write once per provider delta.

Tests:

- `tests/test_interaction_service.py::InteractionServiceTests::test_turn_boundary_persiste_rotas_e_custos_conhecidos_antes_do_close` proves completed-turn usage is queryable before close.
- `tests/test_interaction_composition.py::test_turn_boundary_recupera_lote_apos_falha_transitoria` proves a failed flush restores the queue, the next turn drains both batches, and shutdown performs its final drain.
- Normal/error/turn-ownership tests now assert no pending batch remains after a successful turn boundary.

### 4. Aggregate retry usage, routes, and cost accounting

Implementation:

- `kairos_integration/interaction_service.py` emits `TurnAccumulator.usage`, not the last attempt's usage snapshot. Persisted and client-visible retry accounting now match.
- `kairos_providers/composition.py` defines non-secret billing routes for built-in, OpenRouter, custom, and local providers. `ProviderGateway.prepare()` freezes the effective route/auth mode and immutable catalog `ModelPrice` once per turn.
- `InteractionCost` carries estimated/actual values, status, and source. The service computes known catalog estimates from aggregate usage and request attempts. Unknown values remain explicit `None` with status `unknown`.
- `kairos_integration/event_protocol.py` includes a cost object on usage events; credentials remain excluded.
- `kairos_state/repositories/usage.py` persists route-level and session-summary token counters and existing estimated/actual cost schema fields in one transaction. Route identity includes session, model, billing provider, base URL, mode, and task, so OpenRouter/custom routes do not coalesce incorrectly. Mixed/unknown cost inputs conservatively produce explicit unknown aggregate status rather than a fabricated number.

Tests:

- `tests/test_interaction_retry.py::InteractionRetryTests::test_retry_preserves_usage_from_each_pre_output_attempt` verifies aggregate client usage after retry.
- `tests/test_provider_composition.py::test_preparo_congela_rotas_de_billing_openrouter_e_custom` verifies configured OpenRouter/custom route and price snapshots.
- `tests/test_interaction_service.py::InteractionServiceTests::test_turn_boundary_persiste_rotas_e_custos_conhecidos_antes_do_close` verifies known custom and OpenRouter estimates and distinct persisted routes.
- `tests/test_interaction_persistence.py::UsageEventTests::test_flush_persiste_custo_estimado_e_sumario_da_sessao` verifies route and session cost fields.
- `tests/test_web_chat_transport.py` and `tests/test_cli_chat.py` verify explicit unknown protocol cost and continued credential exclusion.

### 5. Non-blocking transcript persistence

Implementation:

- New `kairos_integration/persistence.py` owns a single ordered worker executor. Its SQLite connection and `SessionRepository`, `MessageRepository`, and `UsageRepository` are created inside that worker and never borrowed from the event-loop thread.
- Composed session creation, user/assistant/error transcript writes, and usage flushes cross this async boundary. The single worker preserves write ordering and repository transaction/retry behavior while keeping SQLite contention sleeps off the event loop.
- Cancellation waits for an already-submitted worker operation to reach a safe terminal state before propagating. Close is idempotent/retryable and drains usage before closing the worker connection/executor.
- The composition root retains its own main-thread connection for selection/history reads and legacy behavior, uses the exact supplied `KAIROS_HOME`, and closes both ownership domains correctly. Directly injected `InteractionService` repositories retain their established behavior.

Tests:

- `tests/test_interaction_async_persistence.py::AsyncPersistenceTests::test_transcript_contention_does_not_block_unrelated_coroutine` holds a SQLite write lock while a transcript write retries and proves an unrelated coroutine advances promptly; it also proves the eventual message is persisted.
- The composition lifecycle, concurrent close, cancellation, build-failure cleanup, turn lease, legacy readability, and exact-home suites all remain green.

## TDD and verification record

The sandbox executor intermittently failed to deliver thread-future completions, so database/executor pytest checks were run through the approved unsandboxed path with explicit timeouts. No command was left unbounded.

Red/green development evidence:

- Selection/non-sticky/aggregate retry subset initially failed in three expected places: missing parameter-layer fields, a sticky/non-serializable message override, and client usage `6` instead of aggregate `10`; after implementation: `3 passed`.
- Credential rotation test initially observed `primary, rotated`; after the prepared boundary: `1 passed` with `primary, primary`.
- Cost/service and persistence tests initially failed because `InteractionEvent.cost` and repository cost arguments did not exist; after implementation: `2 passed`.
- Web protocol cost assertion initially failed; after adding explicit cost serialization: `1 passed in 0.50s`.
- OpenRouter/custom billing-route test initially observed an empty custom base URL; after composition route freezing: `1 passed`.
- Async persistence test initially failed to import the missing boundary; after implementation, the exact bounded check passed: `1 passed in 0.53s`.

Final commands and results:

```text
uv run ruff check --output-format concise kairos_integration kairos_providers/gateway.py kairos_providers/composition.py kairos_state/repositories/usage.py tests/test_interaction_selection.py tests/test_interaction_service.py tests/test_interaction_retry.py tests/test_interaction_persistence.py tests/test_provider_composition.py tests/test_web_chat_transport.py tests/test_interaction_composition.py tests/test_interaction_turn_ownership.py tests/test_interaction_async_persistence.py tests/test_cli_chat.py
All checks passed!

uv run ruff format --check kairos_integration kairos_providers/gateway.py kairos_providers/composition.py kairos_state/repositories/usage.py tests/test_interaction_selection.py tests/test_interaction_service.py tests/test_interaction_retry.py tests/test_interaction_persistence.py tests/test_provider_composition.py tests/test_web_chat_transport.py tests/test_interaction_composition.py tests/test_interaction_turn_ownership.py tests/test_interaction_async_persistence.py tests/test_cli_chat.py
23 files already formatted

git diff --check
exit 0

/bin/bash -lc 'timeout 120s uv run pytest -q tests/test_interaction_selection.py tests/test_interaction_service.py tests/test_interaction_retry.py tests/test_interaction_persistence.py tests/test_interaction_composition.py tests/test_interaction_turn_ownership.py tests/test_interaction_async_persistence.py tests/test_provider_gateway.py tests/test_provider_composition.py tests/test_web_chat_transport.py tests/test_cli_chat.py tests/test_cli.py tests/test_cli_surface.py'
Initial compatibility pass: 4 failed, 193 passed, 159 subtests passed in 4.04s

/bin/bash -lc 'timeout 30s uv run pytest -q tests/test_interaction_service.py::InteractionServiceTests::test_turno_resolve_uma_vez_streama_e_persiste tests/test_interaction_composition.py::test_composition_fecha_somente_os_recursos_que_criou tests/test_cli_chat.py::test_cli_json_is_versioned_canonical_ndjson_without_credential_metadata'
4 passed in 0.31s

/bin/bash -lc 'timeout 120s uv run pytest -q'
1085 passed, 19 skipped, 5546 subtests passed in 17.76s
```

The four compatibility failures were narrowly resolved by making envelope message parameters authoritative even with a legacy injected context loader, preserving the established closed-database exception after worker shutdown, and updating the CLI's exact usage payload expectation for the required explicit cost object.

## Files changed

Production:

- `kairos_integration/__init__.py`
- `kairos_integration/composition.py`
- `kairos_integration/event_protocol.py`
- `kairos_integration/interaction_contract.py`
- `kairos_integration/interaction_service.py`
- `kairos_integration/persistence.py` (new)
- `kairos_integration/selection_context.py`
- `kairos_providers/composition.py`
- `kairos_providers/gateway.py`
- `kairos_providers/selection.py`
- `kairos_state/repositories/usage.py`

Tests:

- `tests/test_cli_chat.py`
- `tests/test_interaction_async_persistence.py` (new)
- `tests/test_interaction_composition.py`
- `tests/test_interaction_persistence.py`
- `tests/test_interaction_retry.py`
- `tests/test_interaction_selection.py`
- `tests/test_interaction_service.py`
- `tests/test_interaction_turn_ownership.py`
- `tests/test_provider_composition.py`
- `tests/test_web_chat_transport.py`

## Self-review

- Verified that message parameters are copied/frozen and nested-merged without mutating persisted conversation configuration.
- Verified that credential lookup/reveal happens once in `prepare()`, retries only copy the private frozen adapter configuration, protocol JSON contains no credential field, and provider errors remain sanitized.
- Verified that provider usage snapshots are counted once per attempt, aggregate usage drives both persistence and the single client usage event, and no per-delta database write was introduced.
- Verified that usage queue extraction, transaction, and restoration remain atomic under the repository lock/transaction boundary.
- Verified that the async worker never receives the main thread-affine connection and that composed writes are ordered through one worker-owned connection.
- Verified close behavior for success, transient flush failure, concurrent callers, cancellation, and post-close compatibility through the existing lifecycle suite.
- Reviewed the complete diff and ran Ruff, format verification, whitespace validation, focused regressions, and the full suite after the final production changes.

## Residual concerns

- `actual_cost_usd` remains `NULL` because the canonical provider event contract currently exposes token usage but no trustworthy upstream billed-cost field. Known catalog-derived values are marked `estimated`; absent or incomplete pricing is explicitly `unknown` rather than guessed.
- Catalog price is intentionally frozen at turn preparation. A catalog refresh during an in-flight retry affects only later turns.
- The persistence worker serializes writes for one composed service. This preserves ordering and avoids event-loop stalls; very high multi-session write throughput may eventually justify partitioned workers, but that would require an explicit ordering design and is outside this fix wave.
- If a turn-boundary usage flush fails and no later turn occurs, visibility waits for the required final shutdown drain; the batch remains recoverable in memory in the meantime.
