# CLI oficial do Kairos — lote 1: shell, config e contexto

Base `main` (pós-merge #86, `98fd3fb`). Recorte confirmado com a usuária em
2026-09-29: **shell interativo `kairos` + `config set/edit` + `kairos context`
+ guia de extensão + unificação de KAIROS_HOME**, mantendo o parser **argparse
declarativo** (sem rewrite). Nada de lógica nova no core — a CLI continua
sendo só uma camada de entrada sobre o mesmo `ComposedInteractionService`
(D-PC.1) que a Web usa.

## Contexto

O binário `kairos` já existe e já é o mesmo caminho da Web: `kairos_cli/chat.py:20`
e `kairos_web/server.py:30` importam o mesmo `build_interaction_router`;
`test_chat_parity.py` fixa payload NDJSON idêntico. Existem 53 comandos de
topo / 149 folhas (árvore declarativa `commands.py` → `HANDLERS`), parser
argparse gerado a partir dos dados, fast path `--version`, config em 5 camadas,
sessões, exit codes (1/2/69), teste de superfície. Decisões passadas:
D-PC.1, D-CLI.1–10.

A visão "Interface Web ─ CLI ─ API → Core" **já é a arquitetura**; os deltas
deste lote são entre o que existe e a experiência pedida:

1. **`kairos` sem comando** hoje imprime ajuda e sai (`main([]) →
   `ExitCode.USAGE`, testado). A visão pede uma entrada interativa de
   acolhimento ("Kairos CLI" com ações), **delegando aos comandos existentes**.
2. **`config set/edit/migrate`** estão declarados e caem em `NOT_IMPLEMENTED`
   (69). O lote implementa `set` e `edit`; `migrate` permanece declarado-recusa
   (sem migração real a aplicar hoje — fora de escopo).
3. **Duas resoluções de `KAIROS_HOME`**: `startup_fast.resolve_kairos_home`
   (única, usada por todos os handlers) e `config.get_config_path`, que
   recalcula por conta própria e ainda ignora `expanduser`. Unificar na única.
4. **Contexto de sessão** não é visível por comando; `kairos context` novo,
   lendo estado real (state.db + config), sem duplicar core.
5. **Extensibilidade declarada**: o registro já é o ponto de extensão; falta o
   guia "como adicionar um comando" e o teste que conta os próprios do Kairos
   precisa ganhar `context`.

## Entregas

### 1. Shell interativo `kairos` (bare, só-TTY)

Novo `kairos_cli/shell.py`:

- `main([])` abre o shell **somente quando** `stdin.isatty() and stdout.isatty()`
  (e sem `--json`); senão mantém ajuda + `ExitCode.USAGE` — scriptável e
  testável (os testes rodam não-TTY e a asserção `main([]) == USAGE` continua
  valendo).
- Loop com menu numerado que **delega ao `main` com argv** (mesma árvore,
  mesmos handlers, mesmo core — nenhuma lógica duplicada):
  - `analisar projeto` → `run "analise o projeto atual em <cwd>" --session <sessão>`;
  - `executar agente` → `chat --session <sessão>` (transporte interativo existente);
  - `mostrar contexto` → `context`;
  - `sair` (ou `exit`/`quit`/`0`) → encerra 0.
- Linha livre: se começa com comando conhecido → `main(argv)`; senão → tratada
  como prompt rápido (`run "<linha>"`). Número fora do menu → mensagem e repete.
- Sessão do shell: `KAIROS_SHELL_SESSION` ou `cli` (persistida em state.db
  como as demais do `chat`). `KeyboardInterrupt` → 130; retorno não-zero de um
  comando é impresso e o loop continua.
- Estrutura injetável para teste: `run_shell(evaluate=main, stdin=sys.stdin,
  stdout=sys.stdout, cwd=Path.cwd())` — os testes injetam um `evaluate` fake
  que grava argv, sem montar serviço.

### 2. `config set` e `config edit`

- `set KEY VALUE`: caminho pontilhado; valor parseado como YAML scalar quando
  possível (`true` → bool, `42` → int, `"texto"` → string; se `safe_load`
  devolver não-str, usa o tipo; senão string crua). Grava via `save_config`
  (tmpfile + `os.replace`), preservando as demais chaves. Chave vazia → USAGE.
- `edit`: abre `$EDITOR` (fallback `vi`) no `config.yaml`, via
  `config.open_in_editor()` — seam `spawn=` injetável para teste. Sem editor
  definido → usa `vi`. Falha do editor → ERRO com a saída.
- Parser já existe (`main.py:209-211`); só faltava o handler.

### 3. `kairos context`

Novo `kairos_cli/context.py` + comando `context` (IMPLEMENTED) + handler:

- Sem `state.db` → honesto: "nenhum turno persistido ainda" (exit OK), nunca
  cria banco (mesma regra do `insights`).
- Com `state.db`: total de sessões, sessão mais recente (id, `execution_kind`,
  contagem de mensagens, `updated_at`), e o contexto do ambiente: `KAIROS_HOME`,
  perfil, modo de container, versão — via `startup_fast` e `kairos_state`.
- `--json` emite JSON; texto alinhado por padrão. Sem imports de providers
  (pesado) — só `kairos_state` + config, como `doctor`/`insights`.

### 4. Unificação de `KAIROS_HOME`

`config.get_config_path()` passa a `Path(resolve_kairos_home()) / "config.yaml"`
— uma única resolução (com `expanduser`), sem caminho paralelo.

### 5. Guia de extensão

Novo `docs/guia-cli-comandos.md`: como adicionar um comando (entrada na árvore
declarativa `commands.py`, handler em `handlers.py`, testes de superfície,
contagem em `PROPRIOS_DO_KAIROS`, regra anti-`reportar sucesso sem efeito` /
recusa 69, preferência do AGENTS.md: comando CLI antes de ferramenta nova).

### 6. Decisões e registro

- `docs/decisoes.md`: **D-CLI.11** (shell só-TTY delegando ao `main`; bare
  não-TTY continua ajuda+USAGE) e **D-CLI.12** (contexto do Kairos não é
  duplicação do core — lê estado persistido como `doctor`/`insights`); registro
  do lote em `docs/functional-progress.md` pós-merge.
- Testes: `tests/test_cli_shell.py` (guard TTY, delegação do menu, prompt
  rápido, `config set/edit`, `context`, `get_config_path` unificado).

## Fora de escopo (declarado)

- `config migrate` (sem migração pendente hoje) e qualquer mexida no parser
  (argparse declarativo confirmado).
- Mudanças no core ou na paridade CLI/Web (payloads NDJSON intocados).
- Novos comandos além de `context`; menu com atalhos/plugins de terceiros.

## Validação

Suíte completa + `ruff check` + `ruff format --check` + `scripts/ci.sh --fast`
verdes; `test_cli_surface` (parser e contagens) sem quebra — `context` entra em
`PROPRIOS_DO_KAIROS` para a contagem de 48 herdados se manter evidência.