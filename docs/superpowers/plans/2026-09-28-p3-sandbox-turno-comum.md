# P3 — Sandbox de execução de código por sessão (turno comum, Opção 2)

Lote em `feat/p3-sandbox-turno-comum`, base `main` (pós-merge #84/#85, `80f1594`).
Recorte confirmado pela usuária em 2026-09-28 via conversa/recorte: **só
`bash`/`terminal`** num worker atestado por sessão; nada mais muda de superfície.

## Contexto

`docs/plano-ferramentas.md:95-99` marca o P3 como risco alto: "a sandbox já
existe para runtime; abrir para o turno comum amplia superfície". O v1 (PR #85)
consertou a fronteira existente (limites `code_execution.*`, Camada 1 com consumidor,
fail-closed do roteador) e declarou a Opção 2 **fora de escopo**. Este lote é a
Opção 2, com recorte mínimo decidido em conjunto.

A exploração de 2026-09-28 estabeleceu os pontos de junção:

1. **O turno comum executa ferramentas em processo no host.** `InteractionService.
   _execute_tool_calls` chama `execute_chat_tool(call)` (`kairos_integration/
   interaction_service.py:492` e `:497`) sem passar `execute=`; o default é
   `registry.dispatch` (`kairos_integration/chat_tools.py:266`) → handler do
   `kairos_tools` no processo do host. `bash_tool` (`kairos_tools/builtin.py:24`)
   roda `asyncio.create_subprocess_shell` no host. `bash` está em `MUTATING_TOOLS`
   (`chat_tools.py:35`): a aprovação por turno acontece antes, inalterada.
2. **A costura de injeção já existe**: `execute_chat_tool(call, *, execute=
   Callable[[str, dict], Any])` (`chat_tools.py:253`) — passando `execute=`, o
   despacho pode ser redirecionado por chamada sem tocar no registro.
3. **O executor atestado é descartável e fail-closed por construção.**
   `DockerWorker.execute(command, *, timeout_ms, limits)` (`kairos_runtime/
   experimental/docker_worker.py:213`) roda argv dentro do container via Codex
   `command/exec`, cwd fixo `/workspace` (`:249`), e **fecha o worker em todo
   erro** (`:263-268`) — sem chance de criança órfã. P3 v1 ligou `limits`
   (`code_execution.*`, `kairos_runtime/execution.py`).
4. **O turno comum não tem workspace de projeto.** `InteractionEnvelope`
   (`kairos_integration/interaction_contract.py:104`) não carrega `cwd`; o
   `/workspace` do container nasce de `snapshot_project(project)`
   (`docker_worker.py:100`). No turno comum **não há projeto para snapshottar**:
   o `/workspace` do sandbox é um ambiente vazio novo por sessão. Isso é
   deliberado (D-RT.4): o `bash` sandboxed **não vê os arquivos do host** —
   o acesso a arquivos continua pelos tools de arquivo aprovados, no host.

## Entregas

### 1. Despacho de `bash`/`terminal` para o worker no turno comum

Novo `kairos_integration/chat_sandbox.py`:

- `ChatBashSandbox`: objeto por serviço com cache de workers por `conversation_id`.
  Expõe `dispatch(conversation_id, name, arguments)` → `dict`/`coroutine`
  aplicável à costura de `execute_chat_tool`:
  - `bash`/`terminal` (resolvido por `SANDBOX_ALIASES`, `registry.py:40`) →
    `worker.execute(["/bin/sh", "-c", command], cwd=normalized, limits=limits)`
    (a imagem de worker é `python:3.11-slim-bookworm`, sem `bash` — `/bin/sh -c`
    equivale ao `create_subprocess_shell` do host);
  - qualquer outro nome → delega a `registry.dispatch` (host, comportamento atual).
- Tradução do resultado `command/exec` (`exitCode`/`stdout`/`stderr`) para o shape
  do `bash_tool` (`stdout`/`stderr`/`exit_code`/`success`) — o contrato que o
  modelo vê não muda.
- `timeout` do argumento do bash: efetivo = `min(timeout_arg, code_execution.timeout_seconds)`.
- `cwd` do argumento: `None` ou caminho que normalize **dentro** de `/workspace`
  (sem `..`, sem `~`) → repassado ao worker; fora de `/workspace` → erro nomeado
  (`invalid_arguments`), nunca chamada no host. O worker ganha `cwd` em
  `DockerWorker.execute` (default `/workspace`, validado sem escape) —
  mudança aditiva em `docker_worker.py`.
- **Fail-closed**: worker/limits indisponível ou Docker ausente → resultado de
  erro nomeado (`_error(call, "unavailable")`, `is_error=True`) — **nunca**
  `registry.dispatch`. O turno segue com o resultado de erro; o host não executa
  o comando.

### 2. Ciclo de vida por sessão

- Cache **um worker ativo por conversa**, criado lazy no primeiro `bash`
  sandboxed da sessão, reutilizado entre turnos da mesma conversa
  (`ChatBashSandbox`, injetado na `InteractionService` pela composição root).
- Worker falido (autofechado pelo `execute`) **não é reutilizado**: removido do
  cache; a próxima chamada da sessão cria outro. `aclose()` do serviço fecha todos.
- Sem worker por chamada: o custo (inspeção de imagem + container + codex server)
  só volta a ocorrer por sessão. Eviction por idle fica **fora de escopo**
  (declarado): footprint limitado a conversas ativas; o `aclose` do serviço é a
  fronteira de liberação.

### 3. Portão fail-closed (opt-in no config)

- `config.yaml → chat.sandboxed_bash: true` para ativar. Ausente/`false` → nada
  muda (despacho host atual, comportamento histórico).
- Parse fail-closed na linha de `parse_execution_limits` (valor não-bool →
  `ValueError` nomeado; nunca cala para o default). O parse vive em
  `parse_chat_sandbox_config` (usado por `build_chat_sandbox` na composição);
  `kairos config check` valida só a well-formedness do YAML (sem schema) — o
  gate real da chave é o parse acima, no caminho de composição.
- Sem docker mas com a chave ligada → o `bash` da sessão responde `unavailable`
  (erro nomeado), nunca host.

### 4. Testes (comportamento, sem ler fonte, sem fake escondendo o caminho real)

- Tradução `command/exec` → shape `bash_tool` (função pura).
- Despacho: `bash`/`terminal` → argv `["sh","-c",command]` + `limits`; outros
  nomes reencaminham ao `registry.dispatch`. O caminho real do Docker fica nos
  testes de integração (job `docker-sessions`/`KAIROS_EXTERNAL_SANDBOX_TEST=1`);
  para o teste do contrato do despacho, injeta-se um worker-double que registra
  argv — o objeto sob teste continua sendo o `ChatBashSandbox` real.
- Ciclo de vida: um worker por conversa, reuso entre turnos, worker falho não é
  reutilizado, `aclose` fecha todos.
- Portão: config com `chat.sandboxed_bash` não-bool → `ValueError`;
  ausente → off (turno com `bash` roda em host, exatamente como hoje).
- Hook do serviço: o turno sempre passa `execute=` (closure), mas com sandbox
  off a closure resolve para `registry.dispatch` — comportamento idêntico ao
  histórico; com sandbox ativo, `bash`/`terminal` vão ao worker (e nunca ao host).
- Integração (offline, `KAIROS_EXTERNAL_SANDBOX_TEST=1` + imagem
  `kairos:external-sandbox`): conversa real com `bash` — o comando roda dentro do
  container atestado, resultado devolvido; host intacto.

### 5. Decisões e registros

- `docs/decisoes.md`: **D-RT.4** — sandbox no turno comum só para `bash`/
  `terminal`; rede `none`; `/workspace` vazio por sessão sem mounts do host
  (`Mounts == []`, política do `WorkerPolicy`); `cwd` fora de `/workspace`
  recusado; sem fallback para o host; aprovação por turno do `bash` inalterada.
  Os demais CHAT_TOOLS (arquivos/web/git/calendar/mcp) permanecem no host.
- `docs/functional-progress.md` pós-merge; plano datado (este arquivo).

## Fora de escopo (declarado)

- **Outras ferramentas no sandbox** (arquivos, `web_search`/`web_extract`, `git`,
  `calendar`, `mcp__*`) — permanecem no host (recorte da usuária).
- **Mount/volume do host no worker**, mesmo read-only — a política atesta
  `Mounts == []`; `/workspace` vazio é a fronteira entre os dois mundos.
- **Rede**: o worker é `network none` por atestação; nada abre egress.
- **Round-trip de arquivos host↔sandbox** (checkpoint/review à la runtime).
- **Eviction por idle do cache de workers** por sessão.

## Validação

Suíte completa + `ruff check`/`ruff format --check` + `scripts/ci.sh --fast`
verdes. Caminho real do Docker no job `docker-sessions` do CI com
`KAIROS_EXTERNAL_SANDBOX_TEST=1` e a imagem `kairos:external-sandbox`
(extensão de `tests/test_external_sandbox_integration.py`). Build da imagem
(job `imagem+integração`) inalterado.