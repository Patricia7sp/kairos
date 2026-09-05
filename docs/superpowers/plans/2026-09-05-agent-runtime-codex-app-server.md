# Agent Runtime e Codex App Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar sessões persistentes de agente Codex com paridade Web/CLI, aprovações explícitas, exclusão por projeto e recuperação sem replay.

**Architecture:** Manter `InteractionService` e acrescentar `InteractionRouter` e `AgentRuntimeService`. Um host local exclusivo por `KAIROS_HOME` mantém o serviço de runtime e seu único subprocesso Codex; Web e CLI acessam esse host por socket Unix. SQLite mantém identidade, fila, leases, eventos e decisões; o Codex mantém o contexto operacional da thread.

**Tech Stack:** Python >=3.11, asyncio, sqlite3, FastAPI, httpx, websockets, JavaScript da SPA existente, pytest e Vitest. Codex CLI 0.153.4 como primeira versão homologada; dependências Python atuais são suficientes.

**Spec:** `docs/superpowers/specs/2026-09-04-agent-runtime-codex-app-server-design.md`

## Global Constraints

- A primeira versão é local e de usuário único.
- Codex é o único runtime entregue no v1.
- Um processo Codex App Server é compartilhado e supervisionado pelo Kairos.
- Há no máximo um turno ativo por sessão.
- Sessões em diretórios diferentes podem executar em paralelo; sessões no mesmo diretório são serializadas.
- Tokens e detalhes internos de autenticação permanecem sob responsabilidade exclusiva do App Server.
- O Kairos nunca reenvia automaticamente uma mensagem depois de falha, pois uma tool pode já ter produzido efeitos externos.
- Não há aprovação automática, timeout permissivo nem resposta presumida.
- O novo modo só é habilitado na interface quando contrato, adapter e persistência estiverem disponíveis.
- Não implementar tenancy, adapters adicionais, reconstrução da thread pelo transcript ou rollback de tools.

## Base inspecionada e decisões de implementação

Base: `74e558a`, em `main`, sem alterações locais em 2026-09-05. Este documento é planejamento; suas caixas não representam trabalho executado.

1. `kairos_state/schema.py` está na versão 1. `initialize_schema()` e `migrate()` são entradas diferentes e ambas precisam entregar a versão 2. Há teste que fixa explicitamente a versão 1 em `tests/test_schema.py`.
2. Reutilizar `sessions`, `messages` e `session_turn_leases`. Acrescentar tabelas próprias para runtime; não usar `model_config` nem confundir `sessions.thread_id` de canais com a thread externa.
3. A migração nova deve usar `BEGIN IMMEDIATE` e chamadas individuais a `execute()`. `executescript()` dentro de `with conn` não garante rollback do conjunto de DDL. Testar falha no meio da migração, incluindo a atualização de versão.
4. Web e CLI hoje constroem serviços próprios. Um singleton Python não atende ao processo compartilhado exigido pela especificação. Criar `kairos runtime serve`, com lock de arquivo mantido por `flock`, socket Unix 0600 dentro de diretório 0700 e um App Server via stdio. O núcleo `model` continua biblioteca em cada processo. Registrar esta extensão em `docs/decisoes.md`.
5. O host é iniciado explicitamente em desenvolvimento e supervisionado no container. CLI não cria outro host como fallback. Sem host, apenas `agent_runtime` fica indisponível. Fechar Web/CLI fecha a assinatura, sem encerrar o host ou o turno.
6. Gerado e inspecionado localmente o schema de `codex-cli 0.153.4` com `codex app-server generate-json-schema`. Homologar essa versão exata primeiro; versões desconhecidas ficam indisponíveis até passar pela matriz do adapter. Não confundir versão do binário, RPC Codex e `AgentRuntimeProtocol v1`.
7. A documentação oficial descreve `thread/read` para consulta e `thread/resume` para retomada, além de login gerenciado por navegador, device code e API key. Referência consultada: [Codex App Server](https://learn.chatgpt.com/docs/app-server). Os nomes e valores enviados devem vir dos schemas gerados pelo binário homologado.
8. O cursor público pertence ao journal Kairos. Não presumir replay de deltas pelo Codex. Snapshot recuperado gera evento de reconciliação, sem inventar deltas perdidos; ausência de confirmação produz `interrupted`. Uma aprovação persistida só pode ser enviada para um pedido externo ainda válido e correlacionado.
9. `kairos_web/ui/` é a SPA entregue, não `web/src/`. Testes de frontend podem ficar em `web/src/__tests__/`, importando os módulos reais da SPA.

## Arquivos e responsabilidades

| Arquivos | Responsabilidade |
| --- | --- |
| `kairos_runtime/contracts.py`, `errors.py`, `policy.py` | Tipos canônicos, erros públicos e diretórios/perfis |
| `kairos_state/runtime_schema.py`, `repositories/runtime.py` | DDL e operações transacionais de runtime |
| `kairos_runtime/store.py`, `leases.py` | Acesso assíncrono ao banco e fila/ownership |
| `kairos_runtime/codex_rpc.py`, `codex_adapter.py`, `supervisor.py` | Framing RPC, tradução e ciclo de vida do Codex |
| `kairos_runtime/service.py`, `recovery.py` | Turnos, decisões, projeções e reconciliação |
| `kairos_runtime/host.py`, `client.py`, `wire.py` | Host exclusivo e contrato IPC |
| `kairos_integration/router.py` | Despacho por identidade persistida |
| `kairos_web/runtime_api.py`, `runtime_transport.py` | REST e assinatura WebSocket de runtime |
| `kairos_cli/runtime.py` | Comandos runtime e interação terminal |
| `kairos_web/ui/js/runtime-client.js`, `views/runtime.js` | Estado e controles runtime na SPA |
| `tests/runtime_support.py`, `fixtures/codex_app_server.py` | Banco temporário, relógio controlado e subprocesso fake |

Criar `kairos_runtime/__init__.py` na tarefa 1. O setuptools já inclui `kairos*`. Cada tarefa abaixo inclui testes novos no caminho indicado, ciclo vermelho/verde e commit próprio. Executar em worktree de implementação criado a partir desta base e do commit do plano.

## Contratos compartilhados

As assinaturas abaixo são o acordo entre tarefas; implementar tipos imutáveis com validação, sem dependência de FastAPI.

```python
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Sandbox = Literal["read_only", "workspace_write", "broad_access"]
Decision = Literal["accept", "decline"]


@dataclass(frozen=True)
class RuntimeSession:
    session_id: str
    runtime_kind: str
    cwd: str
    sandbox: Sandbox
    external_thread_id: str | None = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    protocol_version: int
    features: frozenset[str]


@dataclass(frozen=True)
class RuntimeEvent:
    protocol_version: int
    event_id: str
    session_id: str
    turn_id: str
    sequence: int
    cursor: str
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class RuntimeObservation:
    state: str  # active, completed, failed, cancelled, missing, unknown
    external_turn_id: str | None
    items: tuple[Mapping[str, Any], ...]
    pending_requests: tuple[Mapping[str, Any], ...]


class AgentRuntimeProtocol(Protocol):
    async def capabilities(self) -> RuntimeCapabilities: ...
    async def create_thread(self, session: RuntimeSession) -> str: ...
    async def resume_thread(self, session: RuntimeSession) -> RuntimeObservation: ...
    async def start_turn(self, session: RuntimeSession, turn_id: str, content: str) -> str: ...
    def observe(
        self, session: RuntimeSession, turn_id: str
    ) -> AsyncIterator[Mapping[str, Any]]: ...
    async def cancel_turn(self, session: RuntimeSession, external_turn_id: str) -> None: ...
    async def respond_approval(self, request_id: str, decision: Decision) -> None: ...
    async def inspect_turn(
        self, session: RuntimeSession, external_turn_id: str | None
    ) -> RuntimeObservation: ...
    async def reconcile(
        self, session: RuntimeSession, cursor: str | None
    ) -> RuntimeObservation: ...
    async def end_thread(self, session: RuntimeSession) -> None: ...
    async def aclose(self) -> None: ...
```

`observe()` entrega observações internas validadas; só o store atribui sequência, cursor e identidade canônica após commit. Features: `text`, `reasoning`, `tools`, `approvals`, `cancel`, `resume`, `snapshot_reconcile`; não anunciar `delta_replay` para o adapter inicial. `end_thread()` descarrega a thread quando suportado; nunca apaga seu histórico externo.

Erros em `errors.py`: `RuntimeErrorInfo(code: str, message: str, retryable: bool)`, subclasse de `Exception`, com `str(error)` contendo código e mensagem pública. Códigos: `unavailable`, `incompatible`, `thread_missing`, `invalid_directory`, `invalid_policy`, `session_busy`, `idempotency_conflict`, `lease_lost`, `approval_denied`, `approval_stale`, `cancel_partial`, `transport`, `invalid_event`, `sequence_gap`, `runtime_internal`. `retryable` autoriza repetir consultas; nunca autoriza reenviar `turn/start`.

## Task 1: Contrato, identidade e política

**Arquivos:** criar `kairos_runtime/__init__.py`, `contracts.py`, `errors.py`, `policy.py`; testar em `tests/test_runtime_contract.py` e `tests/test_runtime_policy.py`.

**Consome:** contratos acima. **Produz:** `authorize_directory(raw: str, allowed: tuple[str, ...]) -> str`, `validate_sandbox(profile: str, broad_enabled: bool, consent: bool) -> Sandbox`, `negotiate(caps: RuntimeCapabilities) -> RuntimeCapabilities`.

- [ ] Escrever teste de alias e bloqueio de diretório externo:

```python
def test_alias_tem_mesma_identidade(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    assert authorize_directory(str(alias), (str(project),)) == str(project.resolve())
```

- [ ] Rodar `uv run pytest -q tests/test_runtime_contract.py tests/test_runtime_policy.py`; esperar falha por import ausente.
- [ ] Implementar resolução estrita e autorização por igualdade a uma raiz cadastrada. Subdiretórios não cadastrados são recusados, evitando que dois nomes do mesmo projeto ganhem leases diferentes. Exigir diretório existente, reprovar arquivo e revalidar caminho e identidade filesystem antes do lease. Rejeitar troca de alvo de symlink. Implementação central:

```python
candidate = Path(raw).resolve(strict=True)
roots = {Path(value).resolve(strict=True) for value in allowed}
if not candidate.is_dir() or candidate not in roots:
    raise RuntimeErrorInfo("invalid_directory", "diretório não autorizado", False)
return str(candidate)
```

- [ ] Cobrir versão diferente de 1, campos aditivos ignoráveis, sequência positiva, payload imutável, `broad_access` sem configuração ou consentimento, e capabilities ausentes. Repetir os dois testes; esperar verde.
- [ ] Commit `feat(runtime): define contrato v1 e politicas de sessao`.

## Task 2: Migração transacional e journal

**Arquivos:** criar `kairos_state/runtime_schema.py`, `kairos_state/repositories/runtime.py`, `kairos_runtime/store.py`, `tests/runtime_support.py`, `tests/test_runtime_storage.py`; modificar `kairos_state/schema.py`, `migrations.py`, `connection.py`, `tests/test_schema.py` e `tests/test_state.py`.

**Consome:** `RuntimeSession`, `RuntimeEvent`. **Produz:** `RuntimeRepository(conn)` e facade `RuntimeStore(db_path)` assíncrona com os mesmos nomes: `create_session(session, source, parent_session_id=None) -> str`, `bind_thread(session_id, thread_id, capabilities) -> None`, `get_session(session_id) -> RuntimeSession`, `admit(session_id, key, content) -> str`, `append(turn_id, event_id, kind, payload) -> RuntimeEvent`, `events_after(session_id, cursor) -> tuple[RuntimeEvent, ...]`. Métodos auxiliares de transição e aprovação definidos nas tarefas 3 e 5 também pertencem ao repositório.

DDL exato a implementar, além dos timestamps `created_at`/`updated_at` nas tabelas operacionais:

| Tabela | Colunas e constraints |
| --- | --- |
| `sessions` | adicionar `execution_kind TEXT NOT NULL DEFAULT 'model' CHECK(execution_kind IN ('model','agent_runtime'))` |
| `runtime_sessions` | `session_id TEXT PRIMARY KEY REFERENCES sessions(id)`, `runtime_kind TEXT NOT NULL CHECK(runtime_kind='codex')`, `external_thread_id TEXT UNIQUE`, `canonical_cwd TEXT NOT NULL`, `sandbox_profile TEXT NOT NULL`, `broad_consent_at REAL`, `protocol_version INTEGER`, `capabilities_json TEXT`, `state TEXT NOT NULL`, `next_sequence INTEGER NOT NULL DEFAULT 1`, `directory_device INTEGER NOT NULL`, `directory_inode INTEGER NOT NULL` |
| `runtime_turns` | `id TEXT PRIMARY KEY`, `session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id)`, `idempotency_key TEXT NOT NULL`, `content_hash TEXT NOT NULL`, `external_turn_id TEXT`, `state TEXT NOT NULL`, `send_state TEXT NOT NULL`, `user_message_id INTEGER REFERENCES messages(id)`, `assistant_message_id INTEGER REFERENCES messages(id)`, `UNIQUE(session_id,idempotency_key)`, `UNIQUE(id,session_id)` |
| `runtime_events` | `event_id TEXT PRIMARY KEY`, `session_id TEXT NOT NULL`, `turn_id TEXT NOT NULL`, `sequence INTEGER NOT NULL`, `kind TEXT NOT NULL`, `payload_json TEXT NOT NULL`, `cursor TEXT NOT NULL UNIQUE`, `UNIQUE(session_id,sequence)`, FK composta `(turn_id,session_id)` para `runtime_turns(id,session_id)` |
| `runtime_approvals` | `id TEXT PRIMARY KEY`, `turn_id TEXT NOT NULL REFERENCES runtime_turns(id)`, `process_generation TEXT NOT NULL`, `external_request_id TEXT NOT NULL`, `external_item_id TEXT`, `request_json TEXT NOT NULL`, `decision TEXT`, `decided_at REAL`, `delivery_state TEXT NOT NULL`, `UNIQUE(process_generation,external_request_id)` |
| `runtime_directory_leases` | `canonical_cwd TEXT PRIMARY KEY`, `turn_id TEXT NOT NULL REFERENCES runtime_turns(id)`, `holder TEXT NOT NULL`, `generation INTEGER NOT NULL`, `acquired_at REAL NOT NULL`, `expires_at REAL NOT NULL`, `quarantined INTEGER NOT NULL DEFAULT 0` |
| `runtime_queue` | `ticket INTEGER PRIMARY KEY AUTOINCREMENT`, `turn_id TEXT NOT NULL UNIQUE REFERENCES runtime_turns(id)`, `canonical_cwd TEXT NOT NULL`, `state TEXT NOT NULL` |

Estados de turno: `queued`, `starting`, `running`, `waiting_approval`, `cancelling`, `recovering`, `completed`, `failed`, `cancelled`, `interrupted`. `send_state`: `not_sent`, `dispatching`, `confirmed`, `uncertain`. Índice parcial UNIQUE em `runtime_turns(session_id)` para todos os estados não terminais. Índices em fila `(canonical_cwd,state,ticket)`, approvals `(turn_id,delivery_state)` e leases `(expires_at)`.

- [ ] Escrever teste de migração v1 com mensagem existente, aplicar duas vezes e comparar transcript; injetar falha depois do primeiro DDL e verificar schema/versão integralmente v1. Teste da idempotência:

```python
first = repo.admit(session_id, "request-1", "olá")
assert repo.admit(session_id, "request-1", "olá") == first
with pytest.raises(RuntimeErrorInfo, match="idempotency"):
    repo.admit(session_id, "request-1", "outro conteúdo")
```

- [ ] Rodar `uv run pytest -q tests/test_runtime_storage.py`; esperar vermelho.
- [ ] Implementar transação explícita, FK e guards SQL para impedir alteração dos cinco campos de identidade após bind. `admit` insere mensagem e turno na mesma transação, consultando primeiro a chave idempotente. `append` atribui sequência por sessão e cursor `v1:<session_id>:<sequence>`; cursor de outra sessão ou adiante do journal é erro. Duplicata idêntica devolve evento existente; mesmo ID com outro conteúdo é `invalid_event`.

```sql
BEGIN IMMEDIATE;
UPDATE runtime_sessions SET next_sequence = next_sequence + 1
WHERE session_id = ? RETURNING next_sequence - 1;
-- Inserir o evento com a sequência retornada antes do COMMIT.
COMMIT;
```

- [ ] Acrescentar tabelas auditáveis ao fingerprint de reparo, sem classificá-las como objetos reconstruíveis. Tornar inicialização vazia e migração antiga convergentes. Rodar `uv run pytest -q tests/test_runtime_storage.py tests/test_schema.py tests/test_state.py` e verificar rollback, FK órfã, imutabilidade e rejeição de cursor inválido.
- [ ] Commit `feat(state): persiste sessoes e eventos de runtime`.

## Task 3: Ownership, fila e quarentena

**Arquivos:** criar `kairos_runtime/leases.py`, `tests/test_runtime_leases.py`; estender `kairos_state/repositories/runtime.py` e `kairos_runtime/store.py`.

**Consome:** tabelas da tarefa 2 e `session_turn_leases`. **Produz:** `RuntimeLeaseManager(store, clock)` com `enqueue(turn_id)`, `claim(turn_id, holder, ttl) -> int | None`, `renew(turn_id, holder, generation, ttl) -> bool`, `release(turn_id, holder, generation) -> bool`, `quarantine(turn_id)`. Métodos são async; geração retornada serve de fencing token. TTL inicial 30 s, renovação a cada 10 s, relógio injetável.

- [ ] Testar em duas conexões reais: sessões A/B no diretório P e C no Q; A/C adquirem, B espera, B adquire depois de A terminar. Usar relógio falso, sem sleeps:

```python
assert await leases.claim(turn_a, "host-1", 30) is not None
assert await leases.claim(turn_b, "host-1", 30) is None
assert await leases.claim(turn_c, "host-1", 30) is not None
```

- [ ] Rodar `uv run pytest -q tests/test_runtime_leases.py`; esperar vermelho.
- [ ] Implementar aquisição de lease de sessão, lease de diretório e atualização da fila na mesma transação `BEGIN IMMEDIATE`. Verificar primeiro ticket elegível do diretório. Renovação/liberação com `WHERE holder=? AND generation=?`; nunca liberar lease de outro dono. Não estender o comportamento de retry dos providers a runtime.
- [ ] Expiração move execução para recuperação e põe diretório em quarentena. Só confirmação de inatividade externa permite liberar. `interrupted` não prova inatividade: diretório continua bloqueado enquanto uma execução antiga puder estar viva. Na perda de lease, interromper envio e pedir cancelamento sem assumir confirmação.
- [ ] Cobrir dono antigo renovando após troca, cancelamento na fila sem RPC, falta de heartbeat, symlink trocado e integridade após rollback. Rodar `uv run pytest -q tests/test_runtime_leases.py tests/test_state.py tests/test_interaction_service.py`.
- [ ] Commit `feat(runtime): serializa projetos com leases duraveis`.

## Task 4: RPC Codex, supervisor e adapter simulado

**Arquivos:** criar `kairos_runtime/codex_rpc.py`, `supervisor.py`, `codex_adapter.py`, `tests/fixtures/codex_app_server.py`, `tests/test_codex_rpc.py`, `tests/test_codex_adapter.py`, `tests/test_codex_supervisor.py`, `tests/fixtures/codex_schema/README.md` e schemas usados nesse diretório.

**Consome:** `AgentRuntimeProtocol` e política. **Produz:** `CodexRpc.call(method, params) -> dict`, `CodexRpc.reply(request_id, result)`, `CodexAppServerAdapter`, `CodexSupervisor.start()`, `restart()`, `aclose()`; todas async. Supervisor fornece `generation: str` nova por subprocesso e `rpc: CodexRpc`.

- [ ] Criar fake executável por `sys.executable`, lendo JSON por linha do stdin e emitindo resposta/notification/request no stdout. Primeira fixture de handshake:

```json
{"id":1,"result":{"userAgent":"codex/0.153.4"}}
```

- [ ] Testar inicialização, respostas fora de ordem, notification antes da resposta, request de aprovação durante turno, EOF, JSON inválido, frame acima de 1 MiB e processo encerrado. Rodar `uv run pytest -q tests/test_codex_rpc.py tests/test_codex_adapter.py tests/test_codex_supervisor.py`; esperar falhas por implementação ausente.
- [ ] Implementar `create_subprocess_exec` com argumentos fixos e `--listen stdio://`; handshake `initialize`, aguardar resposta, depois `initialized`. Reader único distribui respostas por ID e notificações por thread/turn. Registrar observador antes de `turn/start` para não perder eventos adiantados. Escrita serializada; stdout nunca é log.
- [ ] Gerar schemas em diretório temporário com binário 0.153.4, versionar somente os métodos/tipos usados e registrar versão/hash no README. Mapear `thread/start`, `thread/resume`, `thread/read`, `turn/start`, `turn/interrupt`, pedidos de command/file approval e eventos text/reasoning/tool/usage/turn. Manter IDs externos e geração do processo para correlação. Pedido desconhecido é erro explícito sem consentimento.

```python
THREAD_SANDBOX = {
    "read_only": "read-only",
    "workspace_write": "workspace-write",
    "broad_access": "danger-full-access",
}
params = {
    "cwd": session.cwd,
    "sandbox": THREAD_SANDBOX[session.sandbox],
    "approvalPolicy": "on-request",
    "approvalsReviewer": "user",
}
```

- [ ] Enviar também `sandboxPolicy` explícita em turnos, usando schema de `TurnStartParams`: `readOnly`, `workspaceWrite` ou `dangerFullAccess`. Em workspace, fixar writableRoots ao projeto, networkAccess falso e excluir diretórios temporários externos. Não enviar permissões aceitas que excedam o perfil; tais pedidos exigem sessão sucessora com novo consentimento. Revalidar política efetiva no resume e desabilitar overrides do cliente.
- [ ] Supervisor reinicia com backoff 1/2/4/8/16/30 s, limitado a 5 tentativas consecutivas; drena stderr com redaction e tamanho limitado, aguarda término/reap e nunca chama `turn/start` no restart. Testar cancelamento do cleanup e ausência de processos órfãos.
- [ ] Repetir os três arquivos de testes; commit `feat(runtime): adapta Codex App Server com supervisor`.

## Task 5: Serviço, aprovações e recuperação

**Arquivos:** criar `kairos_runtime/service.py`, `recovery.py`, `tests/test_runtime_service.py`, `tests/test_runtime_recovery.py`, `tests/test_runtime_approvals.py`; estender store e repository.

**Consome:** adapter, store, leases. **Produz:** `AgentRuntimeService.submit(session_id, content, idempotency_key) -> str`, `subscribe(session_id, cursor=None) -> AsyncIterator[RuntimeEvent]`, `decide(session_id, approval_id, decision) -> None`, `cancel(session_id, turn_id) -> None`, `end(session_id) -> None`, `recover() -> None`, `aclose() -> None`. Exceto `subscribe`, métodos são async. Store acrescenta `transition(turn_id, expected, target) -> bool`, `save_approval(turn_id: str, process_generation: str, external_request_id: str, external_item_id: str | None, request: dict) -> str`, `decide_approval(approval_id, decision) -> bool`, `finish(turn_id, state, content, usage) -> None`, `nonterminal_turns() -> tuple[dict, ...]`.

- [ ] Testar que segunda submissão com mesma chave não inicia outra execução e que evento já está no banco quando assinante o recebe:

```python
turn_id = await service.submit(session_id, "olá", "one")
assert await service.submit(session_id, "olá", "one") == turn_id
async for event in service.subscribe(session_id):
    assert event in await store.events_after(session_id, None)
    if event.kind == "turn_end":
        break
assert fake.start_count == 1
```

- [ ] Rodar `uv run pytest -q tests/test_runtime_service.py tests/test_runtime_recovery.py tests/test_runtime_approvals.py`; esperar vermelho.
- [ ] Implementar tasks de execução pertencentes ao serviço, independentes das assinaturas. Acordar assinantes após commit; buscar journal pelo cursor para fechar corrida replay/live. Fila lenta do cliente não bloqueia o reader do Codex; desconectar assinante atrasado com cursor retomável.
- [ ] Antes de RPC: persistir mensagem, `dispatching` e identidade do turno. Após resposta: persistir external_turn_id e `confirmed`. Falha nessa janela produz `uncertain`, consulta externa e nunca retry do envio. Falha após `thread/start` antes do bind também não recria automaticamente thread; registrar recuperação inconclusiva preservando auditoria.
- [ ] Persistir pedido antes de `waiting_approval`; decisão por compare-and-set, resposta repetida igual é idempotente e conflitante dá erro. Validar sessão, turno, item, geração e pedido externo atual antes de envio. Persistir decisão primeiro, marcar entrega somente com evidência. Reconexão de interface mantém pedido; restart do Codex invalida correlação antiga até confirmação externa.
- [ ] Cancelamento muda para `cancelling`, envia interrupt e aguarda estado terminal; timeout produz recuperação/`cancel_partial`, sem mensagem de rollback. Negação não encerra sessão. Encerrar sessão com turno ativo primeiro cancela/reconcilia; finalizar apenas quando não há execução incerta.
- [ ] Reconciliar snapshot por external_thread_id, external_turn_id e item_id; nunca deduplicar deltas apenas por texto (dois deltas iguais podem ser legítimos). ID canônico de snapshot derivado de thread/turn/item/estado/hash. Append com sequência local, `kind='reconciled'`, payload com snapshot e motivo da lacuna; projeção usa snapshot como substituição do item, não concatenação de texto. Preservar eventos anteriores.
- [ ] Atualizar mensagem assistant, uso conhecido e estado terminal atomicamente; sem usage recebido, gravar desconhecido, nunca custo zero inventado. Não usar ledger de tentativa de provider para runtime. Marcar sessão ready após terminal confirmado, unavailable para thread ausente e interrupted para resultado incerto. Sessão sucessora usa parent_session_id e thread nova.
- [ ] Testar restart Kairos/Codex em cada fronteira de persistência, evento duplicado/conflitante, gap, partial text, thread ausente, aprovação negada/desconectada e runtime incompatível. Repetir testes; commit `feat(runtime): coordena turnos e recuperacao sem replay`.

## Task 6: Host compartilhado e InteractionRouter

**Arquivos:** criar `kairos_runtime/host.py`, `client.py`, `wire.py`, `kairos_integration/router.py`, `tests/test_runtime_host.py`, `tests/test_interaction_router.py`; modificar `kairos_integration/interaction_contract.py`, `composition.py`, `__init__.py` e `docs/decisoes.md`.

**Consome:** serviço tarefa 5. **Produz:** `RuntimeClient(socket_path)` com métodos públicos do serviço; `serve_runtime(home: Path) -> None` async; `build_interaction_router(home: Path) -> InteractionRouter`. Router oferece `stream(envelope)` com união de eventos model/runtime e `aclose()` para seus clientes. Acrescentar `idempotency_key: str | None = None` no fim de `InteractionEnvelope`; obrigatório somente para runtime.

- [ ] Testar dois clientes em processos distintos, um host e um subprocesso fake; encerrar um cliente durante aprovação e responder pelo outro. Testar segundo host recusado sem matar o primeiro. Rodar `uv run pytest -q tests/test_runtime_host.py tests/test_interaction_router.py`; esperar vermelho.
- [ ] Implementar mensagens JSON por linha em Unix socket, limite 1 MiB, request_id, método, params e resposta tipada. Métodos permitidos: `session.create`, `session.get`, `session.end`, `turn.submit`, `turn.cancel`, `events.subscribe`, `approval.decide`, `runtime.status`, `account.status`, `account.login`, `account.cancel`, `account.logout`. Um socket de assinatura emite somente eventos; comandos usam conexões separadas.

```json
{"id":"req-1","method":"turn.submit","params":{"session_id":"s1","content":"olá","idempotency_key":"one"}}
```

- [ ] Lock `$KAIROS_HOME/run/runtime.lock` com `flock(LOCK_EX|LOCK_NB)` durante toda a vida do host; remover socket antigo somente após adquirir lock e verificar tipo/dono. Verificar UID de peer em Linux. Configurar home canonical antes do lock, inclusive para aliases. Startup chama recover antes de aceitar novos turnos; shutdown impede admissão, reconcilia/cancela execuções e fecha subprocesso.
- [ ] Router consulta sessão no banco: ausência mantém criação model legada, campo ausente equivale a model, runtime sempre usa RuntimeClient. Rejeitar overrides de provider e alteração de identidade em runtime. Não criar App Server dentro de `build_interaction_service`. Desligar router não encerra host.
- [ ] Repetir testes incluindo processos, flag desabilitada e regressão `uv run pytest -q tests/test_interaction_composition.py tests/test_cli_chat.py tests/test_web_chat_transport.py`; commit `feat(integration): compartilha runtime entre Web e CLI`.

## Task 7: Autenticação oficial e redaction

**Arquivos:** criar `kairos_runtime/auth.py`, `redaction.py`, `tests/test_runtime_auth.py`, `tests/test_runtime_security.py`; modificar adapter e host.

**Consome:** CodexRpc. **Produz:** `RuntimeAuth.status() -> dict`, `login(mode: str, api_key: str | None = None) -> dict`, `cancel(login_id: str) -> None`, `logout() -> None`, todos async; `public_error(code: str) -> dict` e `sanitize_payload(kind: str, payload: dict) -> dict` síncronos.

- [ ] Testar modos `chatgpt`, `chatgptDeviceCode`, `apiKey` no fake e injetar credencial sentinela em erro, stderr e resposta desconhecida:

```python
assert sentinel not in database_dump
assert sentinel not in captured_logs
assert sentinel not in serialized_events
```

- [ ] Rodar `uv run pytest -q tests/test_runtime_auth.py tests/test_runtime_security.py`; esperar vermelho.
- [ ] Implementar `account/read`, `account/login/start`, `account/login/cancel`, `account/logout` e notificações conforme schemas. API key só transita em memória para RPC e não integra configuração, vault dos providers, fila, tracing ou journal. Não aceitar `chatgptAuthTokens`, não abrir arquivos de credenciais do Codex. Login não deve persistir URL/code; fornecer só à interface solicitante e limpar ao concluir/cancelar.
- [ ] Configurar CODEX_HOME dedicado ao runtime sob volume persistente, diferente do Codex que desenvolve o projeto. Não ler seu conteúdo no Kairos. Redaction por allowlist de campos de evento e mensagens de erro fixas; nunca serializar exception RPC bruta. Sanitizar mensagens técnicas e valores conhecidos sensíveis sem prometer remover segredos arbitrários escritos pelo usuário.
- [ ] Cobrir logout com turno ativo (recusar até encerramento), status sem login, falha/cancelamento do login e ausência de token no log. Repetir testes; commit `feat(runtime): integra login Codex sem persistir credenciais`.

## Task 8: REST, WebSocket e CLI com paridade

**Arquivos:** criar `kairos_web/runtime_api.py`, `runtime_transport.py`, `kairos_cli/runtime.py`, `tests/test_web_runtime_api.py`, `tests/test_web_runtime_transport.py`, `tests/test_cli_runtime.py`, `tests/test_runtime_parity.py`; modificar `kairos_web/server.py`, `chat_transport.py`, `kairos_cli/chat.py`, `commands.py`, `main.py`, `handlers.py`.

**Consome:** router e RuntimeClient. **Produz:** serializador único `runtime_event_to_json(event) -> dict`; REST `/api/runtime/status`, `/api/runtime/account`, `/api/runtime/login`, `/api/runtime/login/cancel`, `/api/runtime/logout`, `/api/runtime/sessions`, `/api/runtime/sessions/{session_id}/end`, `/api/runtime/sessions/{session_id}/turns`, `/api/runtime/sessions/{session_id}/cancel`, `/api/runtime/sessions/{session_id}/approvals/{approval_id}`. Consultas de sessão/mensagens existentes incluem metadados runtime. WS `/ws/runtime` recebe `subscribe` com session_id/cursor e usa tickets existentes.

- [ ] Escrever teste de roundtrip REST e igualdade Web/CLI:

```python
expected = runtime_event_to_json(event)
assert websocket_payload == expected
assert json.loads(cli_output_line) == expected
assert expected["execution_kind"] == "agent_runtime"
```

- [ ] Rodar `uv run pytest -q tests/test_web_runtime_api.py tests/test_web_runtime_transport.py tests/test_cli_runtime.py tests/test_runtime_parity.py`; esperar vermelho.
- [ ] Implementar rotas autenticadas com modelos estritos; 404 para sessão ausente, 409 para conflito/identidade imutável, 422 para cursor/política inválidos, 503 para runtime indisponível. Aprovação requer sessão e pedido correspondentes; nenhuma operação via GET produz efeito. Nunca incluir api_key em query string.
- [ ] Implementar CLI `runtime serve`, `runtime status`, `runtime login --method`, `runtime logout`, `runtime session create --cwd --sandbox`, `runtime session end`, `runtime approve --session --approval --decision`, `runtime cancel --session --turn`, `runtime watch --session --cursor`. `chat --session` roteia pela sessão persistida; aceitar `--idempotency-key` e gerar UUID por nova mensagem humana. Leitura de API key via prompt oculto; JSON nunca responde aprovação implicitamente.
- [ ] Ctrl-C solicita cancelamento explícito e acompanha confirmação; EOF/desconexão apenas sai da assinatura. Aprovação aparece no terminal e pode ser respondida por segundo comando. Adicionar `protocol_version`, IDs, cursor e `execution_kind` no wire runtime, preservando JSON model existente.
- [ ] Rodar regressão `uv run pytest -q tests/test_chat_parity.py tests/test_cli_chat.py tests/test_cli.py tests/test_cli_surface.py tests/test_web_chat_transport.py tests/test_spa_chat_contract.py` e testes novos; commit `feat(runtime): entrega paridade REST WebSocket e CLI`.

## Task 9: SPA de runtime

**Arquivos:** criar `kairos_web/ui/js/runtime-client.js`, `kairos_web/ui/js/views/runtime.js`, `web/src/__tests__/runtime.test.ts`; modificar `kairos_web/ui/js/api.js`, `app.js`, `views/chat.js`, `views/sessoes.js` e `kairos_web/ui/styles/views.css`.

**Consome:** REST/WS da tarefa 8. **Produz:** reducer exportado `reduceRuntime(state, event)` e painel registrado na SPA. Estado tem `sessionId`, `cursor`, `lastSequence`, `items`, `approvals`, `status`, `capabilities`.

- [ ] Testar reducer importado do módulo realmente servido:

```javascript
const after = reduceRuntime(before, event);
expect(reduceRuntime(after, event)).toEqual(after);
expect(after.cursor).toBe(event.cursor);
```

- [ ] Rodar `npm --prefix web test -- --run src/__tests__/runtime.test.ts`; esperar vermelho.
- [ ] Implementar criação com runtime, lista de projetos autorizados e sandbox; broad_access exige confirmação explícita. Exibir modo, projeto e sandbox fixados, fila, uso conhecido/desconhecido, texto/tools e pedidos de aprovação com aceitar/negar. Habilitar controles pelas capabilities, e login por navegador/device code/API key em formulário sem armazenamento local de credenciais.
- [ ] Reconectar com último cursor aplicado, detectar salto de sequência e buscar reconciliação. Não aplicar delta duas vezes. Exibir snapshot reconciliado substituindo o item correspondente e preservar indicação de lacuna. Nunca interpolar conteúdo de tool com innerHTML; usar textContent. Sessão unavailable/interrupted oferece continuar quando seguro ou criar sucessora sem copiar thread.
- [ ] Testar eventos maliciosos de HTML, reconexão, cursor repetido, aprovação sem conexão, cancelamento parcial e runtime desabilitado. Verificar import do módulo da SPA pelos testes e smoke da raiz com `tests/test_spa_chat_contract.py`.
- [ ] Rodar `npm --prefix web test`, `npm --prefix web run typecheck`, `uv run pytest -q tests/test_spa_chat_contract.py`; commit `feat(web): apresenta sessoes e aprovacoes de runtime`.

## Task 10: Container, CI e aceite operacional

**Arquivos:** modificar `Dockerfile`, `compose.yaml`, `docker/config.default.yaml`, `.github/workflows/ci.yml`, `tests/test_container.py`, `tests/test_compose_config.py`, `pyproject.toml`; criar `docker/s6-rc.d/runtime/run`, `runtime/type`, `runtime/dependencies.d/base`, `docker/s6-rc.d/user/contents.d/runtime`, `tests/test_runtime_e2e.py`, `tests/test_runtime_live.py`, `docs/agent-runtime.md` e `docs/agent-runtime-acceptance.md`.

**Consome:** entrega das tarefas 1–9. **Produz:** imagem com binário fixado, serviço s6, runbook e relatório de aceite com evidências.

- [ ] Testar smoke fake ponta a ponta: criar, enviar, desconectar, aprovar por CLI, retomar Web, reiniciar host, retomar thread, comparar journal e contagem de turn/start. Adicionar marcador `runtime_live` excluído por padrão e condicionado a `KAIROS_RUNTIME_LIVE=1`; CI nunca herda login pessoal.
- [ ] Rodar `uv run pytest -q tests/test_runtime_e2e.py tests/test_container.py --deselect tests/test_container.py::RealImageTests`; verificar vermelho dos novos requisitos antes de editar imagem.
- [ ] Instalar Codex 0.153.4 com artefato verificado e checksum registrado no Dockerfile, escolhido por arquitetura. Não usar latest nem copiar credenciais para camadas. Host s6 roda como usuário Kairos, `exec kairos runtime serve`, dentro do mesmo KAIROS_HOME. Configuração inicial:

```yaml
agent_runtime:
  enabled: false
  codex_binary: /usr/local/bin/codex
  codex_version: 0.153.4
  allowed_directories: []
  broad_access_enabled: false
```

- [ ] Montar somente projetos cadastrados; não conceder socket Docker nem privileged para viabilizar sandbox. CODEX_HOME persiste no volume com acesso restrito. Se sandbox não funcionar no container, runtime fica indisponível com diagnóstico; nunca trocar para broad_access silenciosamente. Saúde geral mantém providers ativos e status runtime separado.
- [ ] Rodar `scripts/ci.sh --fast`, `uv build`, `docker build --check .`, `docker build -t kairos:test .`, `uv run pytest -q tests/test_container.py::RealImageTests`. Registrar versão, commit, resultados e skips; CI completa exige todos os jobs obrigatórios, não apenas testes locais.
- [ ] Preparar e executar smoke real opt-in em dois diretórios temporários autorizados: login/status/logout por modos suportados, dois turnos na mesma thread, restart real do host e Codex, fila no mesmo projeto e paralelismo entre projetos. Login humano ou API key fornecida para esse teste é dependência explícita; se ausente, registrar `não executado` e não declarar aceite completo.
- [ ] Antes de redeploy, identificar stack, commit e imagem atuais por leitura; criar backup consistente do SQLite pelo backup API e guardar referência recuperável da imagem anterior. Aplicar pelo fluxo Komodo existente somente com autorização vigente para deploy. Não realizar downgrade destrutivo do schema. Validar container healthy, `/api/health`, versão do runtime e smoke `model`/`agent_runtime` após implantação.
- [ ] Atualizar `docs/agent-runtime-acceptance.md` com comandos, horários e resultados sem credenciais. Commit `feat(runtime): empacota host e documenta aceite operacional`. Merge/push/deploy seguem a autorização da sessão de execução; este plano não é evidência de que ocorreram.

## Matriz de cobertura e revisão do plano

| Requisito da especificação | Tarefas |
| --- | --- |
| Identidade, diretórios, sandbox imutáveis | 1, 2, 4, 8 |
| Protocolo versionado, capabilities e erros | 1, 4, 6, 8 |
| Lease de sessão/projeto, fila e paralelismo | 2, 3, 6, 10 |
| Thread persistida, envio único e journal antes da publicação | 2, 4, 5 |
| Aprovação explícita, desconexão, negação e cancelamento | 4, 5, 8, 9 |
| Cursor, deduplicação, gaps e recuperação dos processos | 2, 5, 6, 10 |
| Thread ausente, encerramento e sessão sucessora | 5, 8, 9 |
| Autenticação oficial e ausência de credenciais auditadas | 7, 8, 9, 10 |
| Paridade Web/CLI e regressão dos providers | 6, 8, 9, 10 |
| Fake em CI e aceite real/container | 4, 10 |

Revisão: contratos internos definidos neste plano; RPC externo ancorado no schema instalado; nenhuma suposição de replay externo ou aprovação recuperável por ID antigo. A entrega está dividida em tarefas revisáveis, mantendo a feature desabilitada até a integração. O próximo passo de execução é a tarefa 1, em worktree própria.
