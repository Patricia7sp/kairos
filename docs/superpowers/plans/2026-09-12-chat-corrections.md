# Chat, models and provider corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the verified browser failures in new conversations, model selection, catalog filters and provider readiness.

**Architecture:** Keep the existing vanilla JavaScript SPA, REST endpoints and InteractionService. Route parameters carry explicit conversation intent; persisted session selection is authoritative for existing conversations. Credential configuration and connection verification remain distinct states.

**Tech Stack:** JavaScript modules, Vitest/jsdom, Python/FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-providers-chat-completion-design.md`, with the corrective acceptance criteria below approved by the user on 2026-09-12.

## Global Constraints

- Preserve existing conversations, credentials, global configuration and runtime sessions.
- No silent provider/model fallback. New conversation selection must not mutate the global default.
- Support the deployed HTTP Tailscale origin, where `crypto.randomUUID` is absent.
- Never expose credentials or secret identifiers in DOM, responses, logs or reports.
- Availability tests use discovery requests, not generated model turns; a connection test does not prove a model can generate.
- User authorization covers corrections, verification, review, merge and deployment; do not request redundant approval.
- No new framework or dependency. Keep human-facing copy in Portuguese.
- Use real view/DOM and transport-boundary tests; do not test source text or duplicate implementation logic.

## Corrective acceptance criteria

1. Both an empty installation and Nova conversa work without `crypto.randomUUID`; prefer `crypto.getRandomValues` to produce an RFC4122 v4 ID.
2. `#/chat?provider=P&model=M&new=1` creates an empty conversation with P/M even when old conversations exist. On submit the session ID is new and P/M reaches the transport. Consume route intent so reload does not unexpectedly replace a persisted conversation.
3. Opening an existing model conversation uses its persisted selection, including overrides. Trocar modelo retains the current session intent; Aplicar ao próximo turno affects that conversation without changing the global default. On a draft without a persisted session, carry selection by route without calling the persisted-selection endpoint. Standalone Models can explicitly set the global default and must label that action accurately.
4. Guard navigation, pending history loads, provider probes and stream callbacks: stale work cannot overwrite another conversation or view. Prevent duplicate sends and session switching during an accepted stream, or fence/detach streams safely. Dispose sockets and animation frames through the router's AbortSignal/cleanup contract.
5. Composer eligibility depends on the selected provider, not any configured provider. No-credential Ollama requires a successful discovery connection check before enabling send. A failed probe is visibly recoverable through a retry action. Configured credentialed providers may attempt chat without automatic remote probes, with real transport failures shown; do not claim verified availability from credentials alone.
6. Models exposes working reasoning and minimum context controls, together with existing filters. Reasoning must come from explicit catalog metadata (e.g. supported parameters reasoning/include_reasoning), never guessed from model IDs. Unknown capability is excluded when filtering. Preview fetch must include preview records so its existing toggle actually works.
7. Provider cards distinguish not configured / credential configured / connection not tested / verified connection / failed connection; save updates credential copy and invalidates any previous successful test. Test results update the badge. Models counts configured providers using accurate wording.

### Task 1: Conversation creation, selection and lifecycle

**Files:**
- Modify: `kairos_web/ui/js/views/chat.js`, `kairos_web/ui/js/views/modelos.js`, `kairos_web/ui/js/chat-client.js` as needed.
- Modify: `kairos_web/server.py` only if persisted selection is absent from existing session APIs.
- Create: `web/src/__tests__/chat-flows.test.ts`.
- Test: `tests/test_web_provider_api.py`, `tests/test_web_chat_transport.py`, `tests/test_spa_chat_contract.py`.

**Interfaces:**
- Consumes `api.sessao(id)`, `api.mensagens(id)`, `api.selecionarModelo({provider, model, scope, session_id})`, `api.testarProvedor(id)`.
- Produces `chatView(root, rota, {signal})` cleanup and route-aware model selection. Existing exported markup/reducer interfaces stay compatible.
- Task 2 adds controls to the same Models view after this task commits; preserve its route handling.

- [ ] **Step 1: Add DOM regressions before implementation.** Use jsdom and stub only fetch/WebSocket external boundaries. Include these independent assertions after actual click/submit flows:

```ts
expect(sent.session_id).not.toBe("existing-session");
expect(sent.provider).toBe("openrouter");
expect(sent.model).toBe("vendor/model:free");
expect(root.querySelector("[data-chat-messages]")!.textContent).not.toContain("old message");
```

Also test no sessions plus missing randomUUID, existing override restoration, same-conversation change without a global POST, selected-provider failure despite another configured provider, Ollama failed probe/retry, route abort during API/history/probe loads, and late stream events after cleanup. Keep expected protocol payloads literal.

- [ ] **Step 2: Run RED and record failing behavior.**

```bash
npm --prefix web test -- src/__tests__/chat-flows.test.ts
```

- [ ] **Step 3: Repair state transitions and selection.** Parse hash once per mount. Use an explicit active session selection with defaults only for new drafts. If needed expose a safe `selection: {provider, model, parameters, reason}` object on session detail from SessionRepository rather than infer provider from a model name. Use a generation counter and AbortSignal check after each await. Use `history.replaceState` for updating current session URL without remounting the view. Keep the active turn DOM node stable across terminal events.

```js
const params = new URLSearchParams(location.hash.split("?")[1] || "");
const wantsNew = params.get("new") === "1";
// IDs use randomUUID when present, otherwise versioned random bytes.
const bytes = crypto.getRandomValues(new Uint8Array(16));
bytes[6] = (bytes[6] & 0x0f) | 0x40;
bytes[8] = (bytes[8] & 0x3f) | 0x80;
```

Never send a global selection over an existing persisted override. Persisted conversation selection changes use scope conversation; draft selection stays in route state. Disable duplicate submission and unsafe navigation during a turn. On errors restore a usable composer or clear retry status, preserving partial output. Limit readiness discovery to the selected credentialless provider; expose retry.

- [ ] **Step 4: Run GREEN plus frontend typecheck and related backend tests.**

```bash
npm --prefix web test
npm --prefix web run typecheck
uv run pytest -q tests/test_web_provider_api.py tests/test_web_chat_transport.py tests/test_spa_chat_contract.py tests/test_model_selection.py
```

- [ ] **Step 5: Commit and request controller review.**

```bash
git add kairos_web web/src/__tests__ tests/test_web_provider_api.py
git commit -m "fix(chat): preserve conversation intent and model selection"
```

### Task 2: Catalog filters and truthful provider status

**Files:**
- Modify: `kairos_web/ui/js/views/modelos.js`, `kairos_web/ui/js/views/provedores.js`, `kairos_web/provider_api.py`.
- Create: `web/src/__tests__/model-provider-flows.test.ts`.
- Test: `tests/test_web_provider_api.py`.
- Modify: `docs/providers-e-chat.md`, `docs/superpowers/specs/2026-08-26-providers-chat-completion-design.md` to document fixed acceptance and operational limits.

**Interfaces:**
- Consumes route-aware Models view from Task 1.
- Produces `capabilities.reasoning` in the serialized model payload, from explicit supported parameters; true if reasoning/include_reasoning/reasoning_effort exists, otherwise null when no explicit assertion is available. Do not expand canonical provider contracts merely to duplicate information already in catalog metadata.
- Provider listing `configured` retains its credential meaning, not a live connection claim.

- [ ] **Step 1: Add failing tests for actual filter controls and status transitions.** Mount Models and Providers through fetch fixtures; click the reasoning control, enter a minimum context and verify the remaining rows. Verify providers initially untested, successful/failed test badge updates and credential replacement clearing success. The model serialization test uses `CatalogModel(..., supported_parameters=frozenset({"reasoning"}))` and expects:

```python
assert serialize_model(model)["capabilities"]["reasoning"] is True
```

Unknown metadata must not pass reasoning-only filtering. The preview toggle must include preview fetched via includePreview. Preserve all Task 1 tests.

- [ ] **Step 2: Run RED.**

```bash
npm --prefix web test -- src/__tests__/model-provider-flows.test.ts
uv run pytest -q tests/test_web_provider_api.py
```

- [ ] **Step 3: Implement controls and state labels.** Add the controls inside the existing filter form and wire FormData:

```html
<label><input type="checkbox" name="reasoning"> Raciocínio</label>
<label>Contexto mínimo <input type="number" name="minContext" min="0" step="1"></label>
```

```js
reasoning: values.has("reasoning"),
minContext: Math.max(0, Number(values.get("minContext")) || 0),
```

Use DOM-safe text assignment for refreshed credential and connection status. Guard asynchronous test/save ordering so a late test cannot mark a newly replaced credential verified. Count configured providers as configurados, and label test success Conexão verificada, never generation guaranteed.

- [ ] **Step 4: Run GREEN, typecheck and related tests; update operational docs.**

```bash
npm --prefix web test
npm --prefix web run typecheck
uv run pytest -q tests/test_web_provider_api.py tests/test_provider_gateway.py tests/test_provider_contract_matrix.py
```

- [ ] **Step 5: Commit and request controller review.**

```bash
git add kairos_web web/src/__tests__ tests/test_web_provider_api.py docs
git commit -m "fix(models): expose catalog filters and verified provider status"
```

## Delivery validation (controller)

- [ ] Run `scripts/ci.sh --fast` with the pinned Codex 0.153.4 executable first in PATH; save complete log.
- [ ] Obtain independent whole-branch review and address blocking findings.
- [ ] Exercise actual browser navigation on desktop and narrow viewport over HTTP. Validate payload routing with controlled transport, then perform minimal acceptance with the already configured free model if available; preserve global defaults.
- [ ] Publish PR, wait required CI checks, merge using authorized scope.
- [ ] Build application image from verified commit, verify fresh restorable backup, deploy only the application container with rollback ready. Preserve broker and worker images/state.
- [ ] Verify health, browser corrections and persisted history in production. Report exact completion and any provider-specific operational limitation.
