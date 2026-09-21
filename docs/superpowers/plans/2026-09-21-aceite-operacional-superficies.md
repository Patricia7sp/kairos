# Aceite operacional — MCP, plugins, skills, TUI/desktop e perfis isolados

Item 3 (pendência `functional-progress.md` 91/549): "TUI/desktop, MCP, plugins,
perfis isolados e gerenciamento de skills precisam de aceite operacional
específico; suas suítes unitárias não certificam uso completo."

O que as duas explorações confirmaram: essas superfícies existem **como camadas
de componente**, sem consumidor vivo em runtime — e isso é **decisão de desenho**
(Tarefas 9–19 do `docs/decisoes.md`), não lacuna. O worker desliga
`plugins`/`skill_search` e não há processo MCP/TUI/Desktop. Portanto o aceite
honesto não é "rodar o agente com MCP levantado" (não existe), é: exercitar as
**entradas executáveis reais** (CLI, API web, contrato) num KAIROS_HOME
descartável, observar efeitos e recusas fail-closed, e **declarar o que não foi
certificado** (nenhum efeito fingido, nenhum checkmark herói).

## Contratos exercitados

### Suítes de componente (o que as unitárias cobrem, rodadas de verdade)

- Python: `test_tui_host.py` (13), `test_mcp.py`, `test_plugins.py`,
  `test_skills.py`, `test_cli_surface.py`, `test_web.py` → **267 testes + 5.132
  subtestes verdes**.
- TUI (`ui-tui` vitest): **17/17**; Desktop (`apps/desktop` vitest): **18/18**;
  `tsc --noEmit` limpo nas duas (e o host de compute `kairos_tui_host` é o alvo
  futuro do fluxo app-server, hoje sem consumidor).

### CLI — operações reais em home descartável (24 execuções observadas)

`bash /tmp/kairos-aceite-cli.sh <repo>` — cada linha registra rótulo, exit e
saída reais.

- MCP: `mcp list --json` real (pacote `mcp` ausente → integração no-op
  declarada; 8 ferramentas do servidor, 2 não publicadas com rationale);
  `mcp remove`/`mcp test` → **69**, sem efeito.
- Plugins: `plugins list` vazio e com `plugin.yaml` semeado (kinds, 37 hooks);
  `plugins install|remove|update` → **69**, diretório preservado.
- Skills: `skills list` vazio/semeado; `add|install|remove|tap` → **69**, skill
  preservada. Seed real `sync_bundled_skills` copiou **4 skills de primeiro
  nível** (o catálogo tem 201 embarcadas em 31 categorias aninhadas — o seed
  varre só filhos diretos; limitação honesta registrada); `sync status` confirma
  **4 rastreadas** no manifesto.
- Perfis isolados: `profile show` default / `.profile-name` (nome aparece) /
  `profile list` com dois homes; `profile delete` → **69**, diretório
  preservado.
- Fail-closed: `gui start|status` → **69 honesto** (mensagem cita a ausência de
  processo desktop); `hooks list|use --hook` → **69 honesto** (aponta para
  `kairos_plugins`); comando `kairos tui` **não existe**.

### Web — gestão de skills (TestClient, app real, home descartável) — 10/10

`uv run python /tmp/kairos-aceite-web-skills.py`: sem token → **401**;
listagem 200 com **201 skills** e categorias aninhadas; leitura de conteúdo
builtin aninhada e 404 para inexistente; PUT grava na raiz do usuário (arquivo
real conferido); travessia `..` → **400 e nada escrito fora do home**; toggle
desabilita de verdade (`skills-disabled.json` real) e a listagem reflete;
reabilitação idempotente.

### Contrato de plugins — operação real (script descartável) — 9/9

`uv run python /tmp/kairos-aceite-plugins.py`: manifesto v2 sem `requires_env`
→ **ACTIVE**; com env faltando → **DISABLED_MISSING_ENV + o que falta**; kind
desconhecido coagido para `standalone` (recusa não, degrada para o mais
restrito); hook inexistente no manifesto → **recusa explícita**; `HookRegistry`
run-all com um plugin quebrado → falha **isolada**, demais rodam; hook
desconhecido recusa (register e invoke); storage isolado em
`<home>/plugin-data/` com banco SQLite por plugin real.

## O que NÃO foi certificado (deliberadamente)

- Não há consumidor runtime: o Codex worker desliga `plugins`, `remote_plugin`
  e `skill_search`; jobs não carregam skills; nenhum pacote `kairos_*` de MCP
  alimenta o agente hoje. O aceite cobre as entradas e o contrato, **não**
  "agente usando plugin/MCP no ar" — isso não existe e não foi fingido.
- Servidor MCP (`kairos mcp serve`) não é um comando: o legado não foi portado
  (mesmo padrão de `acp`/`hooks`). Publicar as 2 ferramentas não-publicadas
  exige a IPC de aprovação primeiro (rationale em `kairos_mcp.server`).
- Instalação de plugins/tap de skills/Ollama operacional: recusa 69 ou adiado
  por decisão anterior — mantidos como falha fechada.

## Arquivo de evidência

- `/tmp/kairos-aceite-cli.log` (evidência CLI, 24 runs).
- `/tmp/kairos-aceite-web-skills.py` (10 checks, exit 0).
- `/tmp/kairos-aceite-plugins.py` (9 checks, exit 0).
- Suítes verdes citadas acima rodadas nesta branch (CI revalida).
- Registro consolidado em `docs/functional-progress.md` (seção nova).