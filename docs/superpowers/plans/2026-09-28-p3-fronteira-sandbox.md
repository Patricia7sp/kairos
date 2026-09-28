# P3 — Sandbox de execução por sessão: a fronteira verdadeira (v1)

Lote em `feat/p3-fronteira-sandbox`, base `main` (pós #82/#83; #84/T-28 segue
em PR próprio). Recorte confirmado pela usuária em 2026-09-28: **Opção 1 — só
consertar**, sem superfície nova.

## Contexto

`docs/plano-ferramentas.md:95-99` marca o P3 como risco alto ("a sandbox já
existe para runtime; abrir para o turno comum amplia superfície"). O caminho de
**execução isolada por sessão** (`execution_kind=agent_runtime`) já existe e está
ligado na web (`kairos_web/server.py`) e no CLI (`kairos_cli/chat.py`): a sessão
roda dentro do worker Docker/Codex atestado (`kairos_runtime/docker_backend`),
com ferramentas Codex-native no container e aprovação por `on-request` do Codex.

Exploração revelou que três alegações/gaps da fronteira não têm efeito real ou
parâmetro:

1. **`showDockerWorker.execute` usa limites hardcoded** (`timeout_ms=5000`,
   `outputBytesCap=65536` em `docker_worker.py:212-231`) — a spec pede
   `config.yaml → code_execution.*` (`_reversa_sdd/tools/requirements.md:39`;
   timeout 300 s, 50 chamadas, 50 KB stdout, 10 KB stderr). `code_execution`
   não existe em lugar nenhum do código.
2. **Camada 1 (`CONTAINER_SKIP`) é caminho morto**: `ApprovalContext.
   isolated_backend` só existe em teste (`kairos_tools/approval.py:92`, `:225`).
   O único consumidor da cadeia no CLI é `kairos approvals test`
   (`kairos_cli/handlers.py:205`), sem flag para o backend isolado.
3. **O turno de uma sessão `agent_runtime` não tem recusa nomeada garantida**
   quando o runtime está indisponível — `InteractionRouter.stream` chama
   `runtime_client.submit` sem guarda de `None` (`kairos_integration/
   router.py:51`), e em alguns caminhos o `runtime_client` pode não existir.

## Entregas

### 1. Limites `code_execution.*` com efeito real

- `kairos_runtime/execution.py` (novo) — `ExecutionLimits` (frozen): 
  `timeout_seconds=300`, `max_stdout_bytes=50_000`, `max_stderr_bytes=10_000`;
  `parse_execution_limits(raw)` **fail-closed** (não-dict, tipo errado, não
  positivo, teto: timeout ≤ 3600 s, bytes ≤ 1 MiB → `ValueError` nomeado);
  `load_execution_limits(home)` lê `<home>/config.yaml → code_execution`
  (PyYAML já é dependência), ausente → defaults, inválido → **raise** (sem o
  `{}` silencioso do `kairos_cli.config.load_config`).
- `DockerWorker.execute` ganha `limits: ExecutionLimits | None = None`: quando
  fornecido aplica `timeoutMs = timeout_seconds*1000` (teto 300 s) e
  `outputBytesCap = max_stdout_bytes` no `command/exec`; `None` preserva o
  comportamento atual (5000/65536). O `kairos_runtime/experimental/__main__.py`
  passa `load_execution_limits(home)` quando o home é conhecido.
- Consumo real em produção: o `DockerSessionRuntime`/`SessionWorker` não passa
  por `execute()` (o Codex roda ferramentas próprias no container) — a fronteira
  que o Kairos **controla** é o executor pontual. Decisão documentada em D-RT.3.

### 2. Camada 1 com consumidor real

- `kairos approvals test` ganha `--isolated-backend` (`kairos_cli/handlers.py`
  + `kairos_cli/main.py`): com a flag, `ApprovalContext(isolated_backend=True)`
  → veredito `ALLOW` pela Camada 1 (`CONTAINER_SKIP`) mesmo para comando
  hardline — diagnóstico honesto de "neste backend atestado o sandbox é a
  fronteira". Sem a flag, nada muda.

### 3. Fail-closed do turno `agent_runtime`

- Testes de comportamento: sessão `agent_runtime` com socket de runtime
  ausente → erro nomeado (`unavailable`, "host de runtime indisponível") no web
  e no CLI chat — **nunca** degrada para o modo model; roteador com
  `runtime_client=None` não pode virar `AttributeError`.
- Corrigir qualquer caminho que degrade ou estoure.

### 4. Decisões D-RT.1–3 e registros

- `docs/decisoes.md`: D-RT.1 (execução por sessão = caminho `agent_runtime`/
  container atestado; `SANDBOX_ALLOWED_TOOLS` da spec não é gate de execução no
  Kairos — fica como rastreabilidade; a allowlist real do model path é
  `CHAT_TOOLS`), D-RT.2 (aprovação no runtime é o `on-request` do Codex com
  reviewer humano; Camada 1 é contrato de diagnóstico/futuro, agora alcançável
  por `approvals test --isolated-backend`), D-RT.3 (limites `code_execution.*`
  aplicados no executor que o Kairos controla; execs internos do Codex regidos
  pela atestação do container).
- `docs/functional-progress.md` pós-merge; plano datado (este arquivo).

## Fora de escopo (declarado)

- **Abrir o turno comum para o sandbox** (Opção 2 do recorte) — superfície nova,
  risco alto; fica como candidato quando houver demanda concreta.
- **Filtro de ferramentas `kairos_tools` dentro do container** — o sandbox não
  executa ferramentas do toolset; é Codex-native. D-RT.1.
- **Proxy dos `command/exec` internos do Codex** — fronteira de caps contornada
  pela atestação (memory/pids/cpu/no-new-privs), não por caps por comando.

## Validação

Suíte completa + `ruff check`/`ruff format --check` + `scripts/ci.sh --fast`.
Testes novos de comportamento (não snapshot), sem mock do caminho real:
subprocesso real para o CLI (testes padrão `test_cli_surface`), handler de
aprovação puro, limites com análise fail-closed, e cadeia de erro de runtime
com socket ausente. O fio Docker real continua no job `docker-sessions` do CI.