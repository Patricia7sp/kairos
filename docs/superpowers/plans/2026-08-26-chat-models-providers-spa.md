# Chat, Models and Providers SPA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar Chat operacional na SPA principal e páginas completas de Modelos e Provedores sobre as APIs canônicas.

**Architecture:** A SPA sem build em `kairos_web/ui` permanece a interface principal. Views ES modules consomem REST e WebSocket por clientes dedicados; o Chat usa layout aprovado com sessões à esquerda, conversa central e contexto à direita.

**Tech Stack:** HTML, CSS, JavaScript ES modules, FastAPI, Vitest/TypeScript para contratos de cliente, pytest para REST/WebSocket.

**Spec:** `docs/superpowers/specs/2026-08-26-providers-chat-completion-design.md`

## Global Constraints

- Requer os planos de gateway/adapters e InteractionService concluídos.
- A SPA nunca recebe nem renderiza segredo ou fragmento de segredo.
- Sem provider utilizável, o composer permanece desabilitado.
- Toda a navegação funciona por teclado e comunica estados via ARIA.
- OpenRouter exibe todos os modelos e oferece filtro **Somente gratuitos**.

---

### Task 1: REST APIs canônicas de modelos e providers

**Files:**
- Create: `kairos_web/provider_api.py`
- Modify: `kairos_web/server.py:192-345`
- Test: `tests/test_web_provider_api.py`

**Interfaces:**
- Produces: `GET /api/models`, `POST /api/models/refresh`, `POST /api/models/selection`, `GET /api/providers`, `POST /api/providers/{id}/credentials`, `POST /api/providers/{id}/test`.

- [ ] **Step 1: Write failing REST contract tests**

```python
def test_models_expoe_preco_gratuidade_capacidades_e_origem(client):
    model = client.get("/api/models?provider=openrouter&free_only=true").json()["models"][0]
    assert set(model) >= {"id", "provider", "is_free", "pricing", "capabilities", "origins"}


def test_provider_response_nao_contem_segredo(client):
    payload = client.get("/api/providers").text
    assert "sk-test" not in payload
    assert "masked_key" not in payload
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_web_provider_api.py`
Expected: FAIL because legacy response lacks canonical fields.

- [ ] **Step 3: Implement thin gateway-backed routes**

Use Pydantic request models with `scope in {conversation, profile, global}`. Convert Decimal prices to strings. Credential save accepts secret input but returns only `credential_id`, `auth_method`, `state` and connection status.

- [ ] **Step 4: Verify API and security tests**

Run: `uv run pytest -q tests/test_web_provider_api.py tests/test_web.py tests/test_security_audit.py && uv run ruff check kairos_web tests/test_web_provider_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_web/provider_api.py kairos_web/server.py tests/test_web_provider_api.py
git commit -m "feat(web): expoe APIs canonicas de providers e modelos"
```

### Task 2: JavaScript API and WebSocket clients

**Files:**
- Modify: `kairos_web/ui/js/api.js`
- Create: `kairos_web/ui/js/chat-client.js`
- Modify: `web/src/__tests__/web.test.ts`

**Interfaces:**
- Produces: `api.modelos(filters)`, `api.atualizarCatalogo(provider)`, `api.selecionarModelo(payload)`, `api.salvarCredencial(provider, secret)`, `ChatClient`.

- [ ] **Step 1: Write failing client contract tests**

```typescript
it("serializes the free-only OpenRouter filter", async () => {
  await api.modelos({ provider: "openrouter", freeOnly: true });
  expect(fetchMock.url).toContain("provider=openrouter&free_only=true");
});

it("ignores additive websocket events", () => {
  const client = new ChatClient({ onEvent: vi.fn() });
  expect(() => client.accept({ protocol: 1, type: "future_event" })).not.toThrow();
});
```

- [ ] **Step 2: Verify RED**

Run: `npm --prefix web test -- --run web/src/__tests__/web.test.ts`
Expected: FAIL because methods and `ChatClient` do not exist.

- [ ] **Step 3: Implement URL filters, mutations and reconnect-safe WebSocket**

`ChatClient.sendMessage` includes protocol 1, session ID, content and optional override. It reconnects only before a turn starts and surfaces close 4401 as authentication loss.

- [ ] **Step 4: Verify frontend tests and typecheck**

Run: `npm --prefix web test && npm --prefix web run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_web/ui/js/api.js kairos_web/ui/js/chat-client.js web/src/__tests__/web.test.ts
git commit -m "feat(web): adiciona clientes de providers e chat"
```

### Task 3: Providers page

**Files:**
- Create: `kairos_web/ui/js/views/provedores.js`
- Modify: `kairos_web/ui/js/app.js`
- Modify: `kairos_web/ui/styles/views.css`
- Modify: `web/src/__tests__/web.test.ts`

**Interfaces:**
- Consumes: canonical provider API.
- Produces: provider cards, credential form, connection test, refresh and advanced routing form.

- [ ] **Step 1: Write failing DOM behavior tests**

```typescript
it("blocks secret echo after credential save", async () => {
  await renderProviders({ savedSecret: "sk-test" });
  expect(document.body.textContent).not.toContain("sk-test");
});

it("shows Ollama without credential form when available", async () => {
  await renderProviders({ providers: [ollamaAvailable()] });
  expect(screen.getByText("Sem credencial necessária")).toBeTruthy();
});
```

- [ ] **Step 2: Verify RED**

Run: `npm --prefix web test -- --run web/src/__tests__/web.test.ts`
Expected: FAIL because provider view is still `emBreveView`.

- [ ] **Step 3: Implement accessible provider cards and forms**

Use labels, `aria-live` for connection state, password input cleared immediately after submit, and explicit confirmation before replacing a stored credential.

- [ ] **Step 4: Verify**

Run: `npm --prefix web test && npm --prefix web run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_web/ui/js/views/provedores.js kairos_web/ui/js/app.js kairos_web/ui/styles/views.css web/src/__tests__/web.test.ts
git commit -m "feat(web): entrega pagina de provedores"
```

### Task 4: Models page with OpenRouter filters

**Files:**
- Modify: `kairos_web/ui/js/views/modelos.js`
- Modify: `kairos_web/ui/styles/views.css`
- Modify: `web/src/__tests__/web.test.ts`

**Interfaces:**
- Consumes: model API and selection mutation.
- Produces: filters for provider/free/tools/vision/reasoning/context/stability, selection dialog and cache status.

- [ ] **Step 1: Write failing filter and selection tests**

```typescript
it("free-only hides priced OpenRouter models", async () => {
  const view = await renderModels([freeModel(), paidModel()]);
  await view.click("Somente gratuitos");
  expect(screen.getByText(freeModel().name)).toBeTruthy();
  expect(screen.queryByText(paidModel().name)).toBeNull();
});

it("model switch offers next turn or new conversation", async () => {
  await chooseModel("openrouter/free");
  expect(screen.getByText("Aplicar ao próximo turno")).toBeTruthy();
  expect(screen.getByText("Iniciar nova conversa")).toBeTruthy();
});
```

- [ ] **Step 2: Verify RED**

Run: `npm --prefix web test -- --run web/src/__tests__/web.test.ts`
Expected: FAIL because filters/actions are absent.

- [ ] **Step 3: Implement local filtering over canonical fields**

Render prices without floating-point conversion, label cache origin/time, hide deprecated models, and keep previews behind an explicit filter.

- [ ] **Step 4: Verify**

Run: `npm --prefix web test && npm --prefix web run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_web/ui/js/views/modelos.js kairos_web/ui/styles/views.css web/src/__tests__/web.test.ts
git commit -m "feat(web): completa catalogo e selecao de modelos"
```

### Task 5: Chat layout and session rail

**Files:**
- Create: `kairos_web/ui/js/views/chat.js`
- Modify: `kairos_web/ui/js/app.js`
- Modify: `kairos_web/ui/styles/views.css`
- Modify: `kairos_web/ui/styles/components.css`
- Modify: `web/src/__tests__/web.test.ts`

**Interfaces:**
- Consumes: `ChatClient`, sessions/messages APIs.
- Produces: approved three-column Chat UI.

- [ ] **Step 1: Write failing empty and configured state tests**

```typescript
it("blocks composer with no usable provider", async () => {
  await renderChat({ providers: [] });
  expect(screen.getByRole("textbox")).toHaveProperty("disabled", true);
  expect(screen.getByText("Configurar um provedor")).toBeTruthy();
});

it("shows model context beside the conversation", async () => {
  await renderChat({ selection: openRouterFreeSelection() });
  expect(screen.getByText("OpenRouter")).toBeTruthy();
  expect(screen.getByText("Gratuito")).toBeTruthy();
});
```

- [ ] **Step 2: Verify RED**

Run: `npm --prefix web test -- --run web/src/__tests__/web.test.ts`
Expected: FAIL importing chat view.

- [ ] **Step 3: Implement responsive three-column layout**

Desktop: session rail, conversation, context panel. Tablet collapses context to a drawer; mobile collapses both rails. Preserve focus when deltas render and expose streaming status through `aria-live="polite"`.

- [ ] **Step 4: Verify DOM tests and typecheck**

Run: `npm --prefix web test && npm --prefix web run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_web/ui/js/views/chat.js kairos_web/ui/js/app.js kairos_web/ui/styles/views.css kairos_web/ui/styles/components.css web/src/__tests__/web.test.ts
git commit -m "feat(web): adiciona Chat central na SPA"
```

### Task 6: Streaming, partial errors and tool events in Chat

**Files:**
- Modify: `kairos_web/ui/js/views/chat.js`
- Modify: `kairos_web/ui/js/chat-client.js`
- Modify: `web/src/__tests__/web.test.ts`

**Interfaces:**
- Consumes: protocol v1 events.
- Produces: incremental message renderer and preserved partial response.

- [ ] **Step 1: Write failing event sequence tests**

```typescript
it("preserves partial text when turn_error arrives", async () => {
  const chat = await renderChat(configured());
  chat.feed(turnStart(), delta("parcial"), turnError("rate_limit"));
  expect(screen.getByText("parcial")).toBeTruthy();
  expect(screen.getByText(/limite/)).toBeTruthy();
});

it("renders tool lifecycle without exposing raw arguments", async () => {
  chat.feed(toolCall({ name: "search", summary: "Busca web" }), toolResult({ ok: true }));
  expect(screen.getByText("Busca web")).toBeTruthy();
  expect(document.body.textContent).not.toContain("raw_arguments");
});
```

- [ ] **Step 2: Verify RED**

Run: `npm --prefix web test -- --run web/src/__tests__/web.test.ts`
Expected: FAIL because event rendering is absent.

- [ ] **Step 3: Implement reducer-based turn state**

Keep one state object per active turn; batch text deltas with `requestAnimationFrame`; finalize only on `turn_end` or `turn_error`.

- [ ] **Step 4: Verify**

Run: `npm --prefix web test && npm --prefix web run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_web/ui/js/views/chat.js kairos_web/ui/js/chat-client.js web/src/__tests__/web.test.ts
git commit -m "feat(web): renderiza streaming tools e erros parciais"
```

### Task 7: Browser-level API/SPA parity tests

**Files:**
- Create: `tests/test_spa_chat_contract.py`
- Modify: `tests/test_web.py`
- Modify: `web/src/__tests__/web.test.ts`

**Interfaces:**
- Verifies: every SPA request has a server route and every versioned event is handled or ignored safely.

- [ ] **Step 1: Write failing parity tests**

```python
def test_spa_nao_referencia_rota_api_inexistente():
    paths = extract_api_paths_from_ui()
    assert paths <= registered_fastapi_paths()


def test_raiz_tem_chat_e_legacy_nao_e_link_principal(client):
    html = client.get("/").text
    assert "Chat" in html or "/ui/js/app.js" in html
    assert 'href="/legacy"' not in html
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_spa_chat_contract.py`
Expected: FAIL until all routes and navigation are aligned.

- [ ] **Step 3: Align route names and add explicit 404 handling**

Do not add stub endpoints. Either implement the referenced route or remove the SPA call.

- [ ] **Step 4: Run frontend and Web suites**

Run: `npm --prefix web test && npm --prefix web run typecheck && uv run pytest -q tests/test_spa_chat_contract.py tests/test_web.py tests/test_web_provider_api.py tests/test_web_chat_transport.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_spa_chat_contract.py tests/test_web.py web/src/__tests__/web.test.ts kairos_web/ui kairos_web/server.py
git commit -m "test(web): garante paridade da SPA de chat"
```
