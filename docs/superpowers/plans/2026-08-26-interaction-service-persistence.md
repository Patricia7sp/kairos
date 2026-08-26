# Interaction Service and Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar a rota canônica de conversa, persistir a seleção efetiva por turno e migrar WebSocket e terminal para o mesmo serviço.

**Architecture:** `InteractionService` recebe envelopes independentes de transporte, resolve um snapshot imutável e emite eventos canônicos enquanto grava mensagens e uso. Web e CLI viram tradutores finos e deixam de instanciar providers.

**Tech Stack:** Python 3.11, SQLite, FastAPI/WebSocket, dataclasses, async iterators, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-08-26-providers-chat-completion-design.md`

## Global Constraints

- Requer o plano `2026-08-26-provider-gateway-adapters.md` concluído.
- A seleção não muda durante um turno.
- Nenhum retry ocorre depois de texto, reasoning ou tool call.
- Sessões e mensagens existentes permanecem legíveis.
- Web e terminal não podem importar factories concretas de adapters.

---

### Task 1: Repositórios de seleção e metadados por turno

**Files:**
- Modify: `kairos_state/repositories/sessions.py`
- Modify: `kairos_state/repositories/messages.py`
- Modify: `kairos_state/repositories/usage.py`
- Test: `tests/test_interaction_persistence.py`

**Interfaces:**
- Produces: `SessionRepository.selection(session_id)`, `SessionRepository.set_selection(...)`, `MessageRepository.append_turn_message(...)`, `UsageRepository.record_event(...)`.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_troca_de_modelo_preserva_historico_e_afeta_proximo_turno(db):
    sessions = SessionRepository(db)
    sessions.create("s1", source="web", model="old")
    sessions.set_selection("s1", ProviderModelRef("openrouter", "openrouter/free"), {"free_only": True})
    assert sessions.selection("s1").ref == ProviderModelRef("openrouter", "openrouter/free")
    assert MessageRepository(db).for_api("s1") == []

def test_mensagem_guarda_selecao_efetiva_em_display_metadata(db):
    message_id = MessageRepository(db).append_turn_message("s1", "assistant", "ok", selection())
    row = db.execute("SELECT display_metadata FROM messages WHERE id=?", (message_id,)).fetchone()
    assert json.loads(row[0])["provider"] == "openrouter"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_interaction_persistence.py`
Expected: FAIL because repository methods do not exist.

- [ ] **Step 3: Implement JSON serialization with stable keys**

```python
@dataclass(frozen=True)
class PersistedSelection:
    ref: ProviderModelRef
    parameters: dict[str, Any]
    reason: SelectionReason

def set_selection(self, session_id, ref, parameters):
    payload = json.dumps({"provider": ref.provider, "parameters": parameters}, sort_keys=True)
    write_with_retry(lambda: self._update_model(session_id, ref.model, payload), budget=Budget.TRANSCRIPT)
```

- [ ] **Step 4: Verify repositories**

Run: `uv run pytest -q tests/test_interaction_persistence.py tests/test_state.py tests/test_schema.py && uv run ruff check kairos_state tests/test_interaction_persistence.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_state/repositories tests/test_interaction_persistence.py
git commit -m "feat(state): persiste selecao efetiva por turno"
```

### Task 2: Interaction envelope, selection snapshot and events

**Files:**
- Create: `kairos_integration/interaction_contract.py`
- Modify: `kairos_integration/__init__.py`
- Test: `tests/test_interaction_contract.py`

**Interfaces:**
- Produces: `InteractionEnvelope`, `InteractionSelectionSnapshot`, `InteractionEvent`, `InteractionEventKind`, `InteractionResult`.

- [ ] **Step 1: Write failing immutability tests**

```python
def test_snapshot_e_imutavel():
    snap = InteractionSelectionSnapshot(ref=ProviderModelRef("openai", "gpt"), parameters={})
    with pytest.raises(FrozenInstanceError):
        snap.ref = ProviderModelRef("openai", "other")

def test_envelope_exige_conversa_e_conteudo():
    with pytest.raises(ValueError):
        InteractionEnvelope(conversation_id="", source="web", content="")
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_interaction_contract.py`
Expected: FAIL importing contracts.

- [ ] **Step 3: Implement frozen transport-neutral dataclasses**

```python
@dataclass(frozen=True)
class InteractionEnvelope:
    conversation_id: str
    source: str
    content: str
    profile: str | None = None
    activity: str | None = None
    override: ProviderModelRef | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class InteractionSelectionSnapshot:
    ref: ProviderModelRef
    reason: SelectionReason
    parameters: Mapping[str, Any]
    credential_id: str | None
```

- [ ] **Step 4: Verify and lint**

Run: `uv run pytest -q tests/test_interaction_contract.py && uv run ruff check kairos_integration tests/test_interaction_contract.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_integration/interaction_contract.py kairos_integration/__init__.py tests/test_interaction_contract.py
git commit -m "feat(interaction): define envelope snapshot e eventos"
```

### Task 3: Selection context loader

**Files:**
- Create: `kairos_integration/selection_context.py`
- Test: `tests/test_interaction_selection.py`

**Interfaces:**
- Consumes: session repository, profile/global config mappings.
- Produces: `SelectionContextLoader.load(envelope) -> ModelSelectionContext`.

- [ ] **Step 1: Write precedence tests**

```python
def test_contexto_monta_cinco_camadas_sem_escolher_fallback(db):
    context = loader(db, profile=ref("p"), global_default=ref("g")).load(
        envelope(override=ref("m"), activity="vision")
    )
    assert context.message == ref("m")
    assert context.profile == ref("p")
    assert context.global_default == ref("g")

def test_sem_configuracao_nao_inventa_anthropic(db):
    context = loader(db).load(envelope())
    assert all(value is None for value in vars(context).values())
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_interaction_selection.py`
Expected: FAIL importing loader.

- [ ] **Step 3: Implement pure layer loading**

Keep provider/model parsing in one `parse_ref` helper. Do not test credentials or network in this component.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q tests/test_interaction_selection.py tests/test_model_selection.py && uv run ruff check kairos_integration/selection_context.py tests/test_interaction_selection.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_integration/selection_context.py tests/test_interaction_selection.py
git commit -m "feat(interaction): carrega precedencia de selecao"
```

### Task 4: InteractionService streaming and persistence

**Files:**
- Create: `kairos_integration/interaction_service.py`
- Test: `tests/test_interaction_service.py`

**Interfaces:**
- Consumes: `ProviderGateway`, `ModelSelectionResolver`, repositories, interaction contracts.
- Produces: `InteractionService.stream(envelope) -> AsyncIterator[InteractionEvent]` and internal `TurnAccumulator(text, reasoning, tool_calls, usage, finish_reason)` with `accept(event)`.

- [ ] **Step 1: Write failing end-to-end service test with fake adapter**

```python
async def test_turno_resolve_uma_vez_streama_e_persiste(db):
    service, resolver = service_with_fake_adapter(db, events=[text("olá"), usage(3, 2), finish("stop")])
    events = [event async for event in service.stream(envelope())]
    assert [e.kind for e in events] == ["turn_start", "delta", "usage", "turn_end"]
    assert resolver.calls == 1
    assert [r["role"] for r in MessageRepository(db).for_api("s1")] == ["user", "assistant"]
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_interaction_service.py`
Expected: FAIL importing service.

- [ ] **Step 3: Implement orchestration and transaction boundaries**

```python
async def stream(self, envelope):
    snapshot = self._resolve_snapshot(envelope)
    self._persist_user(envelope, snapshot)
    yield InteractionEvent.turn_start(snapshot)
    accumulator = TurnAccumulator()
    try:
        async for event in self._gateway.stream(snapshot, self._history(envelope)):
            accumulator.accept(event)
            yield InteractionEvent.from_provider(event)
    except ProviderError as exc:
        self._persist_error(accumulator, exc)
        yield InteractionEvent.turn_error(exc)
        return
    self._persist_assistant(accumulator, snapshot)
    yield InteractionEvent.turn_end(accumulator.finish_reason)
```

- [ ] **Step 4: Verify service and state suites**

Run: `uv run pytest -q tests/test_interaction_service.py tests/test_interaction_persistence.py tests/test_state.py && uv run ruff check kairos_integration tests/test_interaction_service.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_integration/interaction_service.py tests/test_interaction_service.py
git commit -m "feat(interaction): executa e persiste turnos canonicos"
```

### Task 5: Safe retry and tool boundaries

**Files:**
- Create: `kairos_integration/retry.py`
- Modify: `kairos_integration/interaction_service.py`
- Test: `tests/test_interaction_retry.py`

**Interfaces:**
- Produces: `RetryPolicy.can_retry(error, accumulator) -> bool` and bounded backoff injection.

- [ ] **Step 1: Write failing retry safety tests**

```python
async def test_network_error_antes_de_delta_repete_uma_vez():
    assert await run(flaky_before_output()).attempts == 2

async def test_erro_depois_de_tool_call_nunca_repete():
    result = await run(tool_then_network_error())
    assert result.attempts == 1
    assert result.partial_preserved is True
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_interaction_retry.py`
Expected: FAIL importing `RetryPolicy`.

- [ ] **Step 3: Implement retry only for retryable errors before observable output**

```python
def can_retry(self, error, accumulator):
    return error.retryable and not (
        accumulator.text or accumulator.reasoning or accumulator.tool_calls
    )
```

Maximum two attempts; inject clock/sleep for deterministic tests.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q tests/test_interaction_retry.py tests/test_interaction_service.py && uv run ruff check kairos_integration tests/test_interaction_retry.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_integration/retry.py kairos_integration/interaction_service.py tests/test_interaction_retry.py
git commit -m "feat(interaction): limita retry antes de efeitos observaveis"
```

### Task 6: Composition root for interaction

**Files:**
- Create: `kairos_integration/composition.py`
- Modify: `kairos_integration/__init__.py`
- Test: `tests/test_interaction_composition.py`

**Interfaces:**
- Produces: `build_interaction_service(home: Path) -> InteractionService`.

- [ ] **Step 1: Write failing dependency composition test**

```python
def test_composition_usa_gateway_e_state_compartilhados(tmp_path):
    service = build_interaction_service(tmp_path)
    assert isinstance(service.gateway, ProviderGateway)
    assert service.home == tmp_path
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_interaction_composition.py`
Expected: FAIL importing builder.

- [ ] **Step 3: Implement composition without module globals**

Build database connection factory, credential service, provider gateway, catalog/resolver and repositories from the same `KAIROS_HOME`.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q tests/test_interaction_composition.py tests/test_interaction_service.py && uv run ruff check kairos_integration tests/test_interaction_composition.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_integration/composition.py kairos_integration/__init__.py tests/test_interaction_composition.py
git commit -m "feat(interaction): compoe servico compartilhado"
```

### Task 7: Migrate WebSocket to InteractionService

**Files:**
- Create: `kairos_web/chat_transport.py`
- Modify: `kairos_web/server.py:607-732`
- Test: `tests/test_web_chat_transport.py`

**Interfaces:**
- Consumes: `InteractionService.stream`.
- Produces: versioned WebSocket JSON events.

- [ ] **Step 1: Write failing WebSocket protocol tests**

```python
def test_websocket_emite_protocolo_v1(auth_client, fake_service):
    with auth_client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "message", "protocol": 1, "session_id": "s1", "content": "oi"})
        assert ws.receive_json()["type"] == "turn_start"
        assert ws.receive_json()["protocol"] == 1
```

- [ ] **Step 2: Verify RED against direct ProviderManager usage**

Run: `uv run pytest -q tests/test_web_chat_transport.py`
Expected: FAIL because transport is absent.

- [ ] **Step 3: Implement translator and remove provider logic from `_chat_session`**

Map request to envelope and each interaction event to additive JSON. Preserve 4401 authentication and ping/pong.

- [ ] **Step 4: Verify Web tests**

Run: `uv run pytest -q tests/test_web_chat_transport.py tests/test_web.py && uv run ruff check kairos_web tests/test_web_chat_transport.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_web/chat_transport.py kairos_web/server.py tests/test_web_chat_transport.py
git commit -m "feat(web): migra websocket para InteractionService"
```

### Task 8: Migrate terminal chat path

**Files:**
- Create: `kairos_cli/chat.py`
- Modify: `kairos_cli/handlers.py`
- Modify: `kairos_cli/commands.py`
- Test: `tests/test_cli_chat.py`

**Interfaces:**
- Consumes: `build_interaction_service`, interaction events.
- Produces: `kairos chat --session ID [--provider P --model M]`.

- [ ] **Step 1: Write failing CLI parity test**

```python
def test_cli_chat_usa_o_mesmo_servico(monkeypatch, capsys):
    fake = FakeInteractionService([delta("olá"), turn_end()])
    monkeypatch.setattr(chat, "build_interaction_service", lambda home: fake)
    assert main(["chat", "--session", "s1", "oi"]) == 0
    assert capsys.readouterr().out == "olá\n"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_cli_chat.py`
Expected: FAIL because the chat command is not operational.

- [ ] **Step 3: Implement CLI event renderer**

Render text to stdout, errors to stderr, JSON event stream under `--json`, and never print credential metadata.

- [ ] **Step 4: Run CLI and full Python suites**

Run: `uv run pytest -q tests/test_cli_chat.py tests/test_cli.py tests/test_cli_surface.py tests/test_interaction*.py && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_cli/chat.py kairos_cli/handlers.py kairos_cli/commands.py tests/test_cli_chat.py
git commit -m "feat(cli): usa InteractionService no chat"
```
