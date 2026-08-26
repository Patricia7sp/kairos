# Provider Gateway and Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o contrato moderno, o `ProviderGateway` e todos os adapters previstos, incluindo OpenRouter com catálogo dinâmico e filtro gratuito.

**Architecture:** Os adapters implementam um protocolo assíncrono único e não persistem credenciais. `ProviderGateway` combina registry, catálogo e cofre; consumidores legados continuam funcionando por uma ponte temporária até o plano de interação.

**Tech Stack:** Python 3.11, `dataclasses`, `httpx`, `cryptography`, `pytest`/`unittest`, SQLite para snapshots.

**Spec:** `docs/superpowers/specs/2026-08-26-providers-chat-completion-design.md`

## Global Constraints

- Nenhum teste chama API externa ou consome créditos.
- Segredos existem somente em memória e nunca aparecem em `repr`, logs, erros ou snapshots.
- O catálogo principal inclui apenas modelos com chat e saída textual.
- OpenRouter usa `data_collection="deny"`, `require_parameters=true` e não troca silenciosamente o ID do modelo.
- Cada task termina com testes focados, lint e commit convencional.

---

### Task 1: Contratos canônicos de adapter e streaming

**Files:**
- Modify: `kairos_providers/contracts.py`
- Create: `kairos_providers/adapter_contract.py`
- Modify: `kairos_providers/__init__.py`
- Test: `tests/test_provider_adapter_contract.py`

**Interfaces:**
- Consumes: `ProviderDescriptor`, `ProviderModelRef`, `CatalogModel`.
- Produces: `ProviderAdapter`, `AdapterRequest`, `ProviderEvent`, `ProviderError`, `ProviderErrorKind`, `ModelPrice`.

- [ ] **Step 1: Write the failing contract tests**

```python
def test_provider_error_repr_nao_expoe_segredo():
    err = ProviderError(ProviderErrorKind.AUTH, "credencial inválida", retryable=False)
    assert "sk-test" not in repr(err)

def test_catalog_model_identifica_preco_gratuito():
    model = catalog_model(price=ModelPrice(prompt=0, completion=0, request=0))
    assert model.is_free is True
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest -q tests/test_provider_adapter_contract.py`
Expected: FAIL importing `ProviderAdapter` and `ModelPrice`.

- [ ] **Step 3: Implement the minimal contracts**

```python
class ProviderErrorKind(StrEnum):
    AUTH = "auth"
    MODEL = "model"
    LIMIT = "limit"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    INCOMPATIBLE = "incompatible"
    INTERNAL = "internal"

@dataclass(frozen=True)
class ModelPrice:
    prompt: Decimal | None = None
    completion: Decimal | None = None
    request: Decimal | None = None

@dataclass(frozen=True)
class AdapterRequest:
    model: ProviderModelRef
    messages: tuple[CanonicalMessage, ...]
    tools: tuple[dict[str, Any], ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)

class ProviderAdapter(Protocol):
    descriptor: ProviderDescriptor
    async def discover_models(self) -> tuple[CatalogModel, ...]: ...
    async def test_connection(self) -> ConnectionStatus: ...
    def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]: ...
```

Define `CanonicalMessage` in the same file as a frozen dataclass with
`role: str`, `content: tuple[ContentPart, ...]`, optional `tool_call_id` and
optional canonical tool calls. Define `ContentPart` with `kind` and `value` so
adapters never consume WebSocket or provider-native payloads directly.

- [ ] **Step 4: Run focused tests and lint**

Run: `uv run pytest -q tests/test_provider_adapter_contract.py tests/test_provider_foundation.py && uv run ruff check kairos_providers tests/test_provider_adapter_contract.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/contracts.py kairos_providers/adapter_contract.py kairos_providers/__init__.py tests/test_provider_adapter_contract.py
git commit -m "feat(providers): define contrato moderno de adapters"
```

### Task 2: Snapshot store e ProviderGateway

**Files:**
- Create: `kairos_providers/catalog_store.py`
- Create: `kairos_providers/gateway.py`
- Modify: `kairos_providers/catalog.py`
- Test: `tests/test_provider_gateway.py`

**Interfaces:**
- Consumes: `ProviderAdapterRegistry`, `CredentialService`, `ModelCatalog`, `ProviderAdapter`.
- Produces: `CatalogSnapshotStore.load(provider)`, `CatalogSnapshotStore.save(provider, snapshot)`, `ProviderGateway.refresh(provider)`, `ProviderGateway.create_adapter(ref)`, and `ProviderCatalogResult(models: tuple[CatalogModel, ...], source: CatalogOrigin)`.

- [ ] **Step 1: Write failing gateway tests**

```python
async def test_refresh_falha_mantem_snapshot_cacheado(tmp_path):
    store = CatalogSnapshotStore(tmp_path / "model-catalog.json")
    store.save("openai", snapshot("openai", "gpt-ok"))
    gateway = gateway_with_adapter(FailingDiscoveryAdapter(), store=store)
    result = await gateway.refresh("openai")
    assert result.source is CatalogOrigin.CACHE
    assert [m.ref.model for m in result.models] == ["gpt-ok"]

def test_create_adapter_resolve_segredo_sem_guardar_no_registry():
    adapter = gateway.create_adapter(ProviderModelRef("openai", "gpt-ok"))
    assert adapter.descriptor.id == "openai"
    assert "secret-value" not in repr(gateway.registry)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_provider_gateway.py`
Expected: FAIL importing `CatalogSnapshotStore`.

- [ ] **Step 3: Implement atomic snapshot persistence and gateway composition**

```python
class ProviderGateway:
    def __init__(self, registry, catalog, credentials, snapshot_store): ...

    async def refresh(self, provider: str) -> ProviderCatalogResult:
        adapter = self._adapter_for_discovery(provider)
        try:
            models = await adapter.discover_models()
        except ProviderError:
            return self._load_cached(provider)
        snapshot = CatalogSnapshot(tuple(models), self._clock(), self._clock() + self._ttl)
        self._snapshots.save(provider, snapshot)
        self._catalog.merge(models, origin=CatalogOrigin.DYNAMIC)
        return ProviderCatalogResult(tuple(models), CatalogOrigin.DYNAMIC)
```

Write snapshots through `tmp`, `fsync`, `os.replace`, mode `0600`; serialize prices as strings.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q tests/test_provider_gateway.py tests/test_provider_catalog.py tests/test_credential_vault.py && uv run ruff check kairos_providers tests/test_provider_gateway.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/catalog_store.py kairos_providers/gateway.py kairos_providers/catalog.py tests/test_provider_gateway.py
git commit -m "feat(providers): adiciona gateway e cache de catalogo"
```

### Task 3: OpenAI Responses adapter

**Files:**
- Create: `kairos_providers/adapters/openai_responses.py`
- Modify: `kairos_providers/adapters/__init__.py`
- Test: `tests/test_openai_responses_adapter.py`

**Interfaces:**
- Consumes: `ProviderAdapter`, `AdapterRequest`, `ProviderEvent`.
- Produces: `OpenAIResponsesAdapter(http: httpx.AsyncClient, api_key: str)`.

- [ ] **Step 1: Write failing HTTP-simulated tests**

```python
async def test_responses_stream_normaliza_texto_tool_e_usage(mock_transport):
    adapter = OpenAIResponsesAdapter(client_for(mock_transport), "secret")
    events = [event async for event in adapter.stream(request_with_tool())]
    assert [e.kind for e in events] == ["text_delta", "tool_call", "usage", "finish"]
    assert events[2].usage.input_tokens == 12

async def test_openai_auth_error_e_estavel(mock_transport):
    with pytest.raises(ProviderError) as ctx:
        await collect(adapter_for_status(401).stream(simple_request()))
    assert ctx.value.kind is ProviderErrorKind.AUTH
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_openai_responses_adapter.py`
Expected: FAIL importing `OpenAIResponsesAdapter`.

- [ ] **Step 3: Implement `/responses`, SSE parsing and `/models` discovery**

Use `Authorization: Bearer`, `stream=true`, map `response.output_text.delta`, function-call argument deltas, `response.completed.usage`, and HTTP errors to `ProviderErrorKind`.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q tests/test_openai_responses_adapter.py tests/test_providers.py && uv run ruff check kairos_providers/adapters/openai_responses.py tests/test_openai_responses_adapter.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/adapters/openai_responses.py kairos_providers/adapters/__init__.py tests/test_openai_responses_adapter.py
git commit -m "feat(providers): implementa OpenAI Responses"
```

### Task 4: Anthropic Messages adapter

**Files:**
- Create: `kairos_providers/adapters/anthropic_messages.py`
- Test: `tests/test_anthropic_messages_adapter.py`

**Interfaces:**
- Consumes: canonical adapter contract.
- Produces: `AnthropicMessagesAdapter(http, api_key)`.

- [ ] **Step 1: Write failing tests for content blocks and tool use**

```python
async def test_anthropic_preserva_tool_use_id(mock_transport):
    events = await collect(anthropic(mock_transport).stream(request_with_tool()))
    call = next(e for e in events if e.kind == "tool_call")
    assert call.tool_call.id == "toolu_01"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_anthropic_messages_adapter.py`
Expected: FAIL importing the adapter.

- [ ] **Step 3: Implement native Messages streaming**

Send `x-api-key`, `anthropic-version`, top-level `system`, `messages`, `tools`; map `content_block_delta`, `input_json_delta`, `message_delta` and usage.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q tests/test_anthropic_messages_adapter.py && uv run ruff check kairos_providers/adapters/anthropic_messages.py tests/test_anthropic_messages_adapter.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/adapters/anthropic_messages.py tests/test_anthropic_messages_adapter.py
git commit -m "feat(providers): implementa Anthropic Messages"
```

### Task 5: Gemini and Ollama adapters

**Files:**
- Create: `kairos_providers/adapters/gemini_native.py`
- Create: `kairos_providers/adapters/ollama_native.py`
- Test: `tests/test_gemini_native_adapter.py`
- Test: `tests/test_ollama_native_adapter.py`

**Interfaces:**
- Produces: `GeminiNativeAdapter(http, api_key=None, oauth_token=None)` and `OllamaNativeAdapter(http, base_url)`.

- [ ] **Step 1: Write failing tests for Gemini functions and Ollama absence**

```python
async def test_gemini_normaliza_function_call(mock_transport):
    events = await collect(gemini(mock_transport).stream(request_with_tool()))
    assert next(e for e in events if e.kind == "tool_call").tool_call.name == "weather"

async def test_ollama_sem_daemon_e_unavailable(mock_transport):
    status = await ollama(connection_error_transport()).test_connection()
    assert status.ok is False
    assert status.state == "unavailable"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_gemini_native_adapter.py tests/test_ollama_native_adapter.py`
Expected: FAIL importing both adapters.

- [ ] **Step 3: Implement native streaming and model discovery**

Gemini maps `streamGenerateContent`, `functionCall`, `functionResponse` and usage metadata. Ollama maps `/api/tags`, `/api/chat`, NDJSON and never requires a credential.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q tests/test_gemini_native_adapter.py tests/test_ollama_native_adapter.py && uv run ruff check kairos_providers/adapters tests/test_gemini_native_adapter.py tests/test_ollama_native_adapter.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/adapters/gemini_native.py kairos_providers/adapters/ollama_native.py tests/test_gemini_native_adapter.py tests/test_ollama_native_adapter.py
git commit -m "feat(providers): moderniza Gemini e Ollama"
```

### Task 6: OpenAI-compatible profiles

**Files:**
- Create: `kairos_providers/adapters/openai_compatible.py`
- Create: `kairos_providers/provider_profiles.py`
- Test: `tests/test_openai_compatible_adapter.py`

**Interfaces:**
- Produces: `OpenAICompatibleProfile(id, base_url, models_url, allowed_headers)` and `OpenAICompatibleAdapter(profile, http, api_key)`.

- [ ] **Step 1: Write failing parameter/header tests**

```python
def test_custom_endpoint_rejeita_header_nao_permitido():
    with pytest.raises(ValueError, match="header não permitido"):
        custom_profile(headers={"Authorization": "roubar"})

async def test_deepseek_usa_profile_sem_branch_no_gateway(mock_transport):
    adapter = compatible(DEEPSEEK_PROFILE, mock_transport)
    assert adapter.descriptor.id == "deepseek"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_openai_compatible_adapter.py`
Expected: FAIL importing profiles.

- [ ] **Step 3: Implement profiles for DeepSeek, Groq and custom**

Reuse canonical Chat Completions parsing, filter parameters by model capability, and permit only explicit attribution headers in custom profiles.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q tests/test_openai_compatible_adapter.py && uv run ruff check kairos_providers/adapters/openai_compatible.py kairos_providers/provider_profiles.py tests/test_openai_compatible_adapter.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/adapters/openai_compatible.py kairos_providers/provider_profiles.py tests/test_openai_compatible_adapter.py
git commit -m "feat(providers): consolida endpoints OpenAI-compatible"
```

### Task 7: OpenRouter first-class adapter

**Files:**
- Create: `kairos_providers/adapters/openrouter.py`
- Modify: `kairos_providers/contracts.py`
- Test: `tests/test_openrouter_adapter.py`

**Interfaces:**
- Produces: `OpenRouterAdapter`, `OpenRouterRoutingPolicy`, `CatalogModel.is_free`, price and supported-parameter metadata.

- [ ] **Step 1: Write failing catalog and routing tests**

```python
async def test_openrouter_mapeia_gratuito_tools_e_preco(mock_transport):
    models = await openrouter(mock_transport).discover_models()
    free = next(m for m in models if m.ref.model.endswith(":free"))
    assert free.is_free is True
    assert free.capabilities.tools is True

async def test_openrouter_envia_politica_segura(mock_transport):
    await collect(openrouter(mock_transport).stream(simple_request()))
    body = mock_transport.requests[0].json()
    assert body["provider"] == {
        "data_collection": "deny",
        "require_parameters": True,
        "allow_fallbacks": True,
    }
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_openrouter_adapter.py`
Expected: FAIL importing `OpenRouterAdapter`.

- [ ] **Step 3: Implement `/models` filters and Chat Completions streaming**

Request text output, map `supported_parameters`, modalities, `expiration_date`, `pricing`, `context_length`, and preserve `openrouter/free`. Send optional `HTTP-Referer`/`X-OpenRouter-Title` only from non-secret configuration.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q tests/test_openrouter_adapter.py tests/test_provider_catalog.py && uv run ruff check kairos_providers/adapters/openrouter.py kairos_providers/contracts.py tests/test_openrouter_adapter.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/adapters/openrouter.py kairos_providers/contracts.py tests/test_openrouter_adapter.py
git commit -m "feat(providers): integra OpenRouter e modelos gratuitos"
```

### Task 8: Registry composition and legacy bridge

**Files:**
- Create: `kairos_providers/composition.py`
- Modify: `kairos_providers/manager.py`
- Modify: `kairos_providers/adapters/__init__.py`
- Test: `tests/test_provider_composition.py`

**Interfaces:**
- Produces: `build_provider_gateway(home: Path) -> ProviderGateway`; temporary `ProviderManager` delegates listing/connection to the gateway.

- [ ] **Step 1: Write failing composition tests**

```python
def test_composition_registra_todos_os_providers(tmp_path):
    gateway = build_provider_gateway(tmp_path)
    ids = [d.id for d in gateway.registry.list_descriptors()]
    assert ids == ["anthropic", "custom", "deepseek", "gemini", "groq", "ollama", "openai", "openrouter"]
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_provider_composition.py`
Expected: FAIL importing `build_provider_gateway`.

- [ ] **Step 3: Compose factories and bridge legacy calls**

Make `ProviderManager.list_all_models()` convert `CatalogModel` to `ModelDescriptor` only at the legacy boundary. Do not add provider-specific branches outside `composition.py` and profiles.

- [ ] **Step 4: Run provider suite and full Python suite**

Run: `uv run pytest -q tests/test_provider_composition.py tests/test_provider*.py tests/test_openrouter_adapter.py tests/test_providers.py && uv run pytest -q`
Expected: PASS with no external requests.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/composition.py kairos_providers/manager.py kairos_providers/adapters/__init__.py tests/test_provider_composition.py
git commit -m "feat(providers): compoe gateway com todos os adapters"
```
