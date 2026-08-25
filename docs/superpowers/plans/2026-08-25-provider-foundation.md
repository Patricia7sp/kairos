# Provider Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar a fundação tipada de providers, catálogo de modelos e resolução de seleção que será consumida pelo Chat, pela página Modelos e pelo terminal.

**Architecture:** A implementação amplia os tipos públicos existentes sem quebrar os adapters atuais. Um registry separa descritores e factories, um catálogo combina fontes curadas/dinâmicas/cacheadas com precedência explícita, e um resolvedor puro escolhe `provider + modelo` pela cadeia mensagem → conversa → atividade → perfil → global.

**Tech Stack:** Python 3.12, dataclasses, `StrEnum`, protocolos de tipagem, `unittest`/pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-25-providers-modelos-chat-design.md`

## Global Constraints

- Esta etapa cobre somente a fase 1 da especificação: contratos, registry, catálogo e resolvedor.
- Nenhuma credencial ou segredo entra nos novos tipos, caches, logs ou exceções.
- O catálogo principal inclui somente modelos textuais adequados a chat/agentes; previews ficam identificados e desativados por padrão.
- Não existe fallback silencioso para outro provider.
- APIs atuais de `ProviderProfile`, `ProviderRegistry`, `BaseLLMProvider` e `ProviderManager` permanecem compatíveis.
- Testes usam fontes em memória e não acessam APIs reais.

---

### Task 1: Tipos canônicos de provider, modelo e seleção

**Files:**
- Create: `kairos_providers/contracts.py`
- Modify: `kairos_providers/base.py`
- Modify: `kairos_providers/__init__.py`
- Test: `tests/test_provider_foundation.py`

**Interfaces:**
- Consumes: `ProviderType` e `ModelDescriptor` existentes em `kairos_providers.base`.
- Produces: `ModelKind`, `ModelStability`, `CatalogOrigin`, `SelectionScope`, `SelectionReason`, `ProviderDescriptor`, `ProviderModelRef`, `ModelCapabilities`, `CatalogModel`, `ResolvedModelSelection`.

- [ ] **Step 1: Escrever testes que expressem os tipos públicos e suas invariantes**

```python
class ProviderContractTests(unittest.TestCase):
    def test_provider_model_ref_exige_provider_e_modelo(self):
        with self.assertRaisesRegex(ValueError, "provider"):
            ProviderModelRef(provider="", model="gpt-5.6-terra")
        with self.assertRaisesRegex(ValueError, "model"):
            ProviderModelRef(provider="openai", model="")

    def test_catalog_model_expoe_se_e_selecionavel(self):
        stable = CatalogModel(
            ref=ProviderModelRef("openai", "gpt-5.6-terra"),
            display_name="GPT 5.6 Terra",
            capabilities=ModelCapabilities(chat=True, tools=True),
            stability=ModelStability.STABLE,
            origins=frozenset({CatalogOrigin.CURATED}),
        )
        preview = dataclasses.replace(stable, stability=ModelStability.PREVIEW)
        self.assertTrue(stable.is_selectable(include_preview=False))
        self.assertFalse(preview.is_selectable(include_preview=False))
        self.assertTrue(preview.is_selectable(include_preview=True))

    def test_resolved_selection_carrega_razao_sem_segredos(self):
        resolved = ResolvedModelSelection(
            ref=ProviderModelRef("openai", "gpt-5.6-terra"),
            reason=SelectionReason.CONVERSATION_OVERRIDE,
        )
        self.assertEqual(resolved.reason.value, "conversation_override")
```

- [ ] **Step 2: Rodar os testes e confirmar RED**

Run: `uv run pytest tests/test_provider_foundation.py -q`
Expected: FAIL de importação porque `kairos_providers.contracts` ainda não existe.

- [ ] **Step 3: Implementar os tipos mínimos e imutáveis**

```python
class ModelKind(StrEnum):
    MODEL = "model"
    REMOTE_AGENT = "remote_agent"


class ModelStability(StrEnum):
    STABLE = "stable"
    PREVIEW = "preview"
    DEPRECATED = "deprecated"


class CatalogOrigin(StrEnum):
    CURATED = "curated"
    DYNAMIC = "dynamic"
    CACHE = "cache"


class SelectionScope(StrEnum):
    MESSAGE = "message"
    CONVERSATION = "conversation"
    ACTIVITY = "activity"
    PROFILE = "profile"
    GLOBAL = "global"


class SelectionReason(StrEnum):
    MESSAGE_OVERRIDE = "message_override"
    CONVERSATION_OVERRIDE = "conversation_override"
    ACTIVITY_RULE = "activity_rule"
    PROFILE_DEFAULT = "profile_default"
    GLOBAL_DEFAULT = "global_default"


@dataclass(frozen=True)
class ProviderModelRef:
    provider: str
    model: str
    kind: ModelKind = ModelKind.MODEL

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider é obrigatório")
        if not self.model.strip():
            raise ValueError("model é obrigatório")
```

Adicionar `ModelCapabilities`, `ProviderDescriptor`, `CatalogModel` e `ResolvedModelSelection` como dataclasses congeladas. `CatalogModel.is_selectable()` retorna falso para modelos sem `chat`, depreciados, ou preview quando `include_preview=False`. Reexportar os tipos em `kairos_providers.__init__`; manter `ModelDescriptor` como contrato legado e documentar sua futura conversão para `CatalogModel`.

- [ ] **Step 4: Rodar testes focalizados e confirmar GREEN**

Run: `uv run pytest tests/test_provider_foundation.py tests/test_providers.py tests/test_gateway.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/contracts.py kairos_providers/base.py kairos_providers/__init__.py tests/test_provider_foundation.py
git commit -m "feat(providers): adiciona contratos canonicos de modelos"
```

### Task 2: Registry de descritores e factories

**Files:**
- Create: `kairos_providers/provider_registry.py`
- Modify: `kairos_providers/__init__.py`
- Test: `tests/test_provider_foundation.py`

**Interfaces:**
- Consumes: `ProviderDescriptor`; factories com assinatura `Callable[..., BaseLLMProvider]`.
- Produces: `RegisteredProvider`, `ProviderAdapterRegistry.register()`, `.describe()`, `.create()`, `.list_descriptors()`.

- [ ] **Step 1: Escrever testes de registro, criação e erro explícito**

```python
class ProviderAdapterRegistryTests(unittest.TestCase):
    def test_registry_descreve_e_cria_adapter_sem_credencial_armazenada(self):
        registry = ProviderAdapterRegistry()
        descriptor = ProviderDescriptor(id="fake", display_name="Fake")
        registry.register(descriptor, lambda **kw: {"model": kw["model"]})
        self.assertEqual(registry.describe("fake"), descriptor)
        self.assertEqual(registry.create("fake", model="m"), {"model": "m"})

    def test_provider_desconhecido_falha_sem_fallback(self):
        with self.assertRaisesRegex(UnknownProviderError, "missing"):
            ProviderAdapterRegistry().create("missing")

    def test_listagem_e_deterministica(self):
        registry = ProviderAdapterRegistry()
        registry.register(ProviderDescriptor(id="z", display_name="Z"), lambda **_: object())
        registry.register(ProviderDescriptor(id="a", display_name="A"), lambda **_: object())
        self.assertEqual([p.id for p in registry.list_descriptors()], ["a", "z"])
```

- [ ] **Step 2: Rodar o teste e confirmar RED**

Run: `uv run pytest tests/test_provider_foundation.py::ProviderAdapterRegistryTests -q`
Expected: FAIL porque `ProviderAdapterRegistry` não existe.

- [ ] **Step 3: Implementar registry focado em descriptors e factories**

```python
class UnknownProviderError(LookupError):
    pass


@dataclass(frozen=True)
class RegisteredProvider:
    descriptor: ProviderDescriptor
    factory: Callable[..., Any] = field(repr=False, compare=False)


class ProviderAdapterRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, RegisteredProvider] = {}

    def register(self, descriptor: ProviderDescriptor, factory: Callable[..., Any]) -> None:
        self._entries[descriptor.id] = RegisteredProvider(descriptor, factory)

    def describe(self, provider: str) -> ProviderDescriptor:
        try:
            return self._entries[provider].descriptor
        except KeyError as exc:
            raise UnknownProviderError(provider) from exc

    def create(self, provider: str, **kwargs: Any) -> Any:
        self.describe(provider)
        return self._entries[provider].factory(**kwargs)

    def list_descriptors(self) -> list[ProviderDescriptor]:
        return [self._entries[key].descriptor for key in sorted(self._entries)]
```

Este registry é complementar ao `ProviderRegistry` legado de perfis. Não renomear nem remover o legado nesta etapa.

- [ ] **Step 4: Rodar testes focalizados e confirmar GREEN**

Run: `uv run pytest tests/test_provider_foundation.py tests/test_gateway.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/provider_registry.py kairos_providers/__init__.py tests/test_provider_foundation.py
git commit -m "feat(providers): adiciona registry de adapters"
```

### Task 3: Catálogo híbrido com precedência e cache válido

**Files:**
- Create: `kairos_providers/catalog.py`
- Modify: `kairos_providers/__init__.py`
- Test: `tests/test_provider_catalog.py`

**Interfaces:**
- Consumes: `CatalogModel`, `CatalogOrigin`, `ProviderModelRef`.
- Produces: `CatalogSnapshot`, `ModelCatalog.merge()`, `.list_models()`, `.find()` e `UnknownModelError`.

- [ ] **Step 1: Escrever testes para merge, filtros e cache**

```python
class ModelCatalogTests(unittest.TestCase):
    def model(
        self,
        model="m",
        *,
        origin=CatalogOrigin.CURATED,
        context=100,
        stability=ModelStability.STABLE,
    ):
        return CatalogModel(
            ref=ProviderModelRef("p", model),
            display_name=model,
            capabilities=ModelCapabilities(chat=True, tools=True, context_length=context),
            stability=stability,
            origins=frozenset({origin}),
        )

    def test_dinamico_prevalece_e_preserva_origens(self):
        catalog = ModelCatalog()
        catalog.merge([self.model(context=100)], origin=CatalogOrigin.CURATED)
        catalog.merge(
            [self.model(origin=CatalogOrigin.DYNAMIC, context=200)], origin=CatalogOrigin.DYNAMIC
        )
        found = catalog.find(ProviderModelRef("p", "m"))
        self.assertEqual(found.capabilities.context_length, 200)
        self.assertEqual(found.origins, {CatalogOrigin.CURATED, CatalogOrigin.DYNAMIC})

    def test_preview_e_deprecated_ficam_ocultos_por_padrao(self):
        catalog = ModelCatalog()
        catalog.merge(
            [
                self.model("stable"),
                self.model("preview", stability=ModelStability.PREVIEW),
                self.model("old", stability=ModelStability.DEPRECATED),
            ],
            origin=CatalogOrigin.CURATED,
        )
        self.assertEqual([m.ref.model for m in catalog.list_models("p")], ["stable"])
        self.assertEqual(
            [m.ref.model for m in catalog.list_models("p", include_preview=True)],
            ["preview", "stable"],
        )

    def test_snapshot_expirado_nao_substitui_cache_utilizavel(self):
        now = 1000.0
        catalog = ModelCatalog(clock=lambda: now)
        catalog.load_snapshot(
            CatalogSnapshot(models=(self.model(),), fetched_at=900, expires_at=1100)
        )
        catalog.load_snapshot(
            CatalogSnapshot(models=(self.model("expired"),), fetched_at=800, expires_at=999)
        )
        self.assertEqual(catalog.find(ProviderModelRef("p", "m")).ref.model, "m")
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `uv run pytest tests/test_provider_catalog.py -q`
Expected: FAIL porque `kairos_providers.catalog` ainda não existe.

- [ ] **Step 3: Implementar catálogo em memória determinístico**

```python
@dataclass(frozen=True)
class CatalogSnapshot:
    models: tuple[CatalogModel, ...]
    fetched_at: float
    expires_at: float

    def is_valid(self, now: float) -> bool:
        return now <= self.expires_at


class UnknownModelError(LookupError):
    pass


class ModelCatalog:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._models: dict[ProviderModelRef, CatalogModel] = {}
        self._snapshot: CatalogSnapshot | None = None
```

`merge()` usa a prioridade `CACHE < CURATED < DYNAMIC`; o item de maior prioridade fornece metadados e a união de `origins` é preservada. `list_models()` ordena por `display_name.casefold()` e filtra chat, preview e deprecated. `load_snapshot()` ignora snapshot expirado quando já há snapshot válido e mescla snapshots válidos com origem `CACHE`.

- [ ] **Step 4: Rodar testes focalizados e confirmar GREEN**

Run: `uv run pytest tests/test_provider_catalog.py tests/test_provider_foundation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/catalog.py kairos_providers/__init__.py tests/test_provider_catalog.py
git commit -m "feat(providers): implementa catalogo hibrido de modelos"
```

### Task 4: Catálogo curado inicial sem congelar disponibilidade remota

**Files:**
- Create: `kairos_providers/curated_catalog.py`
- Test: `tests/test_provider_catalog.py`

**Interfaces:**
- Consumes: `CatalogModel` e seus enums.
- Produces: `curated_models() -> tuple[CatalogModel, ...]`.

- [ ] **Step 1: Escrever testes sem impor contagem exata de modelos**

```python
class CuratedCatalogTests(unittest.TestCase):
    def test_inclui_recomendacoes_agentic_da_especificacao(self):
        refs = {model.ref for model in curated_models()}
        self.assertIn(ProviderModelRef("openai", "gpt-5.6-terra"), refs)
        self.assertIn(ProviderModelRef("anthropic", "claude-sonnet-5"), refs)
        self.assertIn(ProviderModelRef("gemini", "gemini-3.7-flash"), refs)

    def test_curadoria_principal_contem_somente_chat(self):
        self.assertTrue(all(model.capabilities.chat for model in curated_models()))

    def test_antigravity_e_remote_agent_preview(self):
        antigravity = next(m for m in curated_models() if m.ref.model == "antigravity")
        self.assertEqual(antigravity.ref.kind, ModelKind.REMOTE_AGENT)
        self.assertEqual(antigravity.stability, ModelStability.PREVIEW)
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `uv run pytest tests/test_provider_catalog.py::CuratedCatalogTests -q`
Expected: FAIL porque `curated_models` não existe.

- [ ] **Step 3: Implementar a curadoria declarativa**

Criar uma tupla de `CatalogModel` para os IDs explicitamente definidos na tabela “Catálogo inicial” da especificação. Para Groq, OpenRouter, Ollama e endpoint customizado, não inventar IDs estáticos: esses providers serão preenchidos por descoberta dinâmica nas etapas posteriores. Marcar `antigravity` como `kind=REMOTE_AGENT`, `stability=PREVIEW`, e origem `CURATED`.

- [ ] **Step 4: Rodar testes e confirmar GREEN**

Run: `uv run pytest tests/test_provider_catalog.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_providers/curated_catalog.py tests/test_provider_catalog.py
git commit -m "feat(providers): adiciona catalogo agentic curado"
```

### Task 5: Resolvedor puro da precedência provider/modelo

**Files:**
- Create: `kairos_providers/selection.py`
- Modify: `kairos_providers/__init__.py`
- Test: `tests/test_model_selection.py`

**Interfaces:**
- Consumes: `ProviderModelRef`, `ResolvedModelSelection`, `SelectionReason`, `ModelCatalog`.
- Produces: `ModelSelectionContext`, `ModelSelectionResolver.resolve()` e `ModelSelectionUnavailableError`.

- [ ] **Step 1: Escrever testes parametrizados da cadeia de precedência**

```python
class ModelSelectionResolverTests(unittest.TestCase):
    def setUp(self):
        self.refs = {
            name: ProviderModelRef("p", name)
            for name in ("message", "conversation", "activity", "profile", "global")
        }
        self.catalog = catalog_with(*self.refs.values())
        self.resolver = ModelSelectionResolver(self.catalog)

    def test_mensagem_vence_todas_as_demais_camadas(self):
        result = self.resolver.resolve(
            ModelSelectionContext(
                message=self.refs["message"],
                conversation=self.refs["conversation"],
                activity=self.refs["activity"],
                profile=self.refs["profile"],
                global_default=self.refs["global"],
            )
        )
        self.assertEqual(result.ref, self.refs["message"])
        self.assertEqual(result.reason, SelectionReason.MESSAGE_OVERRIDE)

    def test_cada_camada_e_usada_quando_as_superiores_ausentes(self):
        cases = [
            ("conversation", SelectionReason.CONVERSATION_OVERRIDE),
            ("activity", SelectionReason.ACTIVITY_RULE),
            ("profile", SelectionReason.PROFILE_DEFAULT),
            ("global_default", SelectionReason.GLOBAL_DEFAULT),
        ]
        for field, reason in cases:
            with self.subTest(field=field):
                result = self.resolver.resolve(
                    ModelSelectionContext(**{field: self.refs[field.removesuffix("_default")]})
                )
                self.assertEqual(result.reason, reason)

    def test_modelo_ausente_falha_sem_trocar_provider(self):
        missing = ProviderModelRef("paid", "missing")
        with self.assertRaisesRegex(ModelSelectionUnavailableError, "paid/missing"):
            self.resolver.resolve(
                ModelSelectionContext(message=missing, global_default=self.refs["global"])
            )
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `uv run pytest tests/test_model_selection.py -q`
Expected: FAIL porque o resolvedor não existe.

- [ ] **Step 3: Implementar seleção pela primeira camada definida**

```python
@dataclass(frozen=True)
class ModelSelectionContext:
    message: ProviderModelRef | None = None
    conversation: ProviderModelRef | None = None
    activity: ProviderModelRef | None = None
    profile: ProviderModelRef | None = None
    global_default: ProviderModelRef | None = None


class ModelSelectionResolver:
    _ORDER = (
        ("message", SelectionReason.MESSAGE_OVERRIDE),
        ("conversation", SelectionReason.CONVERSATION_OVERRIDE),
        ("activity", SelectionReason.ACTIVITY_RULE),
        ("profile", SelectionReason.PROFILE_DEFAULT),
        ("global_default", SelectionReason.GLOBAL_DEFAULT),
    )

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    def resolve(self, context: ModelSelectionContext) -> ResolvedModelSelection:
        for field, reason in self._ORDER:
            ref = getattr(context, field)
            if ref is not None:
                try:
                    self._catalog.find(ref)
                except UnknownModelError as exc:
                    raise ModelSelectionUnavailableError(f"{ref.provider}/{ref.model}") from exc
                return ResolvedModelSelection(ref=ref, reason=reason)
        raise ModelSelectionUnavailableError("nenhuma seleção configurada")
```

- [ ] **Step 4: Rodar testes focalizados e confirmar GREEN**

Run: `uv run pytest tests/test_model_selection.py tests/test_provider_catalog.py tests/test_provider_foundation.py -q`
Expected: PASS.

- [ ] **Step 5: Rodar regressão da camada de providers e lint**

Run: `uv run pytest tests/test_providers.py tests/test_gateway.py tests/test_model_selection.py tests/test_provider_catalog.py tests/test_provider_foundation.py -q`
Expected: PASS.

Run: `uv run ruff check kairos_providers tests/test_provider_foundation.py tests/test_provider_catalog.py tests/test_model_selection.py`
Expected: PASS sem avisos.

- [ ] **Step 6: Commit**

```bash
git add kairos_providers/selection.py kairos_providers/__init__.py tests/test_model_selection.py
git commit -m "feat(providers): resolve selecao de provider e modelo"
```

### Task 6: Validar o marco e documentar a fronteira seguinte

**Files:**
- Modify: `docs/superpowers/specs/2026-08-25-providers-modelos-chat-design.md`
- Test: `tests/test_provider_foundation.py`, `tests/test_provider_catalog.py`, `tests/test_model_selection.py`

**Interfaces:**
- Consumes: todos os contratos e serviços produzidos nas Tasks 1–5.
- Produces: fase 1 marcada como implementada e comandos de validação reproduzíveis.

- [ ] **Step 1: Rodar toda a suíte Python**

Run: `uv run pytest -q`
Expected: todos os testes passam; skips existentes são reportados sem novos erros.

- [ ] **Step 2: Rodar verificações estáticas do projeto**

Run: `uv run ruff check .`
Expected: PASS.

Run: `uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 3: Atualizar o estado da especificação**

Alterar o cabeçalho para `Estado: fase 1 implementada; fases 2–6 pendentes` e acrescentar uma seção curta “Estado da implementação” listando os módulos criados. Não marcar cofre, adapters, persistência, UI ou Chat como concluídos.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-08-25-providers-modelos-chat-design.md
git commit -m "docs: registra fundacao de providers implementada"
```

