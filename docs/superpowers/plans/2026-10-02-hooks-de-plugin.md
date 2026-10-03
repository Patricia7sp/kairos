# Plugins de verdade: descoberta, estado e despacho de hooks

Base `main` (pós-merge #87, `4906077`). Recorte confirmado com a usuária em
2026-10-02: **o registro de hooks passa a ter consumidor real** — o scanner que
faltava, os emissores no caminho do turno e os dois comandos que hoje mentem ou
recusam (`plugins list`, `hooks list`).

## Contexto — o que está construído e o que não está ligado

`kairos_plugins` tem três módulos testados e **zero consumidores no build**:

- `manifest.py` — manifesto v2 (`load_manifest`), 5 `kind`s, coerção em vez de
  rejeição, falha suave de `requires_env` (`resolve_state`).
- `hooks.py` — os 37 hooks em 8 famílias, `HookRegistry.invoke_hook` com
  *run-all-then-pick-first*, falha isolada por callback, redação do dump de erro
  do provedor como default (G-21).
- `storage.py` — `plugin-data/` fora do diretório de instalação.

Apuração (grep em todo o código fora de `kairos_plugins/` e `tests/`):
`HookRegistry`, `load_manifest` e `resolve_state` **não são importados por
nenhum módulo do runtime**. As consequências, todas reais:

1. **Não existe scanner.** Nada lê `<home>/plugins/<nome>/plugin.yaml`. O
   requisito RF-01 da spec (scanner bundled + `$KAIROS_HOME/plugins`, manifesto
   inválido → aviso sem crash) não tem implementação.
2. **Nenhum hook dispara.** Um plugin pode declarar `provides_hooks` (o manifesto
   até valida os nomes contra os 37) e nunca ser chamado. É o pior tipo de dívida
   do projeto pelo avesso: não é sucesso fictício, é **capacidade declarada sem
   efeito** — o plugin se apresenta instalado e não faz nada.
3. **`kairos plugins list` não lê manifesto nenhum** (`handlers.py:341`):
   `raiz.iterdir()` lista **nomes de diretório** e imprime `len(VALID_HOOKS)`
   como "hooks disponíveis" — um diretório vazio com nome de plugin aparece como
   plugin instalado.
4. **`kairos hooks list` recusa com 69** dizendo a verdade ("os hooks reais vivem
   em `kairos_plugins` e são registrados pelos plugins no runtime") — mas o
   runtime não registra ninguém. A recusa está certa **e** omite que a parte de
   "registrados pelos plugins" também não existe.

A spec (`_reversa_sdd/plugins/`) confirma o desenho: `invoke_hook` (não
`dispatch_hook`), *run-all* com isolamento, `requires_env` → `disabled_missing_env`
visível com o que falta, streaming observa e não transforma, e o ponto exato de
cada um dos 37 hooks no núcleo está marcado 🔴 **não mapeado**. Esse último item é
a razão de este lote ser **estreito**: não é possível emitir honestamente 37 hooks
num build que não tem kanban, subagente, gateway-typed dispatch nem runtime de
verificação no caminho do turno.

## Entregas

### 1. `kairos_plugins/loader.py` — o scanner que faltava

- `load_plugins(home, *, disabled=())` → `LoadedPlugins(registry, plugins)`:
  varre `<home>/plugins/<nome>/plugin.yaml` (e um diretório *bundled*, se
  existir), carrega o manifesto, resolve o estado e registra os callbacks.
- **Falha suave onde a spec manda** (RF-01): manifesto inválido, YAML quebrado ou
  `plugin.yaml` ausente → `logger.warning` nomeando o plugin e o motivo, e os
  demais plugins continuam. Um plugin ruim não tira o host do ar.
- **Estado honesto por plugin**: `active`, `disabled` (interruptor do usuário) ou
  `disabled_missing_env` com a lista do que falta. O estado `disabled_missing_env`
  é visível **e** diz o que configurar — sumiço silencioso é pior.
- **Entrada do plugin**: `<dir>/plugin.py`, importado por caminho com nome de
  módulo único (`kairos_plugin_<slug>`) para não colidir no `sys.modules`. Os
  callbacks são funções de módulo com o **mesmo nome do hook declarado** em
  `provides_hooks`.
- **O manifesto é a única fonte** (fail-closed): um callback cujo nome não esteja
  em `provides_hooks` **não** é registrado. Um plugin não registra hook que não
  declarou — o inverso seria o `hooks.json` fantasma que a D-CLI.9 removes.
- `requires_raw_error: true` no manifesto → `registry.allow_raw_error(nome)`
  (G-21 já implementado em `hooks.py`).
- Sem `plugin.py` → plugin ativo, **zero callbacks**, reportado como tal
  ("declara N hooks, sem módulo `plugin.py`"). Não é erro e não é sucesso.
- `<home>/plugins` inexistente → nada é lido, nada é importado, registro vazio.
  Zero footprint no caminho quente (regra do cinto estreito do `AGENTS.md`).

### 2. `kairos_plugins/emitter.py` — o despacho do runtime

- `EMITTED_HOOKS`: os hooks que o runtime do Kairos emite **hoje** — fonte única
  para o emissor e para o relatório do CLI, para os dois não divergirem.
- `HookEmitter.emit(hook, **payload)`: sem registry ou sem callback registrado
  para o hook → **não faz nada** (nem `await` novo no caminho quente). Com
  callbacks, despacha fora do event loop (`asyncio.to_thread`): callback de
  plugin é síncrono por contrato (`invoke_hook` é sync) e a spec registra como
  risco aberto 🟡 que "um plugin lento em `pre_tool_call` degrada todo turno sem
  sinal". Isolar a thread é o que transforma esse 🟡 em algo que não segura o
  loop do turno.

### 3. Seis emissores no `InteractionService` — só de observação

| Hook | Onde dispara |
|---|---|
| `on_session_start` | `_stream_owned`, depois da sessão assegurada |
| `on_session_end` | `_stream_owned`, no `finally` (usando `run_persistent_cleanup`, o padrão já estabelecido no arquivo) |
| `pre_tool_call` | `_execute_tool_calls`, imediatamente antes de `execute_chat_tool` |
| `post_tool_call` | idem, com o resultado |
| `pre_llm_call` | `_stream_tool_turn`, antes da rodada do provedor |
| `post_llm_call` | idem, com o desfecho da rodada |

- **Observação não altera o turno**: nenhum retorno de callback muda parâmetros,
  argumentos, resultado ou streaming. Os `transform_*` e o portão `pre_verify`
  **continuam sem emissor** — mudar o que o modelo ou o usuário veem é outra
  decisão, com política própria (D-PLUG.2).
- `pre_tool_call`/`post_tool_call` **só** disparam quando a ferramenta vai
  realmente executar: recusa por aprovação, ferramenta não habilitada e limite de
  ferramentas não emitem — a ferramenta não rodou, e um observador que acredita que
  rodou é pior que nenhum observador.
- **Conteúdo não viaja no payload**: `pre_tool_call` leva identidade da chamada
  (ferramenta, argumentos, conversa, origem) e `post_tool_call` leva o desfecho
  (`is_error`, status) — **não** o corpo do resultado (limitado a 32 KiB e já
  entregue ao modelo; copiá-lo para todo plugin registrado em toda chamada é
  imposto de latência e memória). O hook cujo propósito é o conteúdo é
  `transform_tool_result`, que não tem emissor neste lote.
- `InteractionService.__init__` ganha `hooks: HookEmitter | None = None`
  (default `None` = caminho intocado), e `ComposedInteractionService` repassa,
  como `chat_sandbox` foi repassado no lote D-RT.4.

### 4. Composição: `build_plugin_hooks(home, config)`

No `kairos_integration/composition.py`, no padrão de `build_chat_sandbox`:
lê `plugins.disabled` do `config.yaml` (parse fail-closed: não-lista levanta
`ValueError`) e devolve `HookEmitter | None` — `None` quando não há nada para
registrar.

### 5. Os dois comandos passam a dizer a verdade

- **`kairos plugins list`**: inventário real — nome, versão, `kind`, estado, o que
  falta, hooks declarados, hooks registrados e o erro de carga, se houver.
  `--json`. Some o `iterdir()` e o `len(VALID_HOOKS)` como "hooks disponíveis".
- **`kairos hooks list`**: sai da recusa 69. Por hook: **família**, **emissor
  neste build** (`sim` / `não — sem emissor`) e **plugins registrados**. Os 31
  sem emissor são ditos como tais — o próprio `manifest.py` chama hook que nunca
  dispara de "pior que hook nenhum"; esconder isso seria a mesma mentira em
  formato de tabela.
- `hooks use` continua recusando: ativar hook por nome **é** fictional enquanto os
  hooks forem registrados pelos plugins no manifesto.

## Fora de escopo (declarado)

- Transformações (`transform_*`) e o portão `pre_verify` — cada um precisa de
  decisão de política (o que acontece quando o plugin mente), e essa decisão não
  vem junto com a fiação.
- `provides_tools` (RF-05): registro automático de ferramenta de plugin no
  `ToolRegistry` — é superfície de toolset (e paga footprint por request), lote
  próprio.
- `plugins install/remove/update` (RF-07): é gerenciador de pacotes, com rede e
  escrita no diretório de instalação; o lote entrega leitura honesta do que já
  está instalado.
- Os 8 hooks de kanban, os 2 de subagente e os de gateway/stream/API: não há
  emissor neste build (ver o 🔴 da spec). Reportados como "sem emissor", não
  inventados.
- Dashboard/TUI de plugins: `/api/dashboard/plugins` é chamado pelo bundle legado
  e não existe no backend; alinhar o painel é outro lote.

## Validação

Suíte completa + `ruff check` + `ruff format --check` + `scripts/ci.sh --fast`
verdes. Testes novos em `tests/test_plugin_hooks.py`, todos de comportamento:
turno real com plugin instalado fires `pre_tool_call`/`post_tool_call`/os de
LLM/sessão; plugin que levanta não derruba o turno; hook não declarado não
registra; manifesto inválido não derruba o scan; `requires_env` ausente fica
visível com o que falta; `requires_raw_error` libera o dump só para quem pediu;
`plugins list`/`hooks list` refletem o registro real. **Sem change-detector** e
sem ler código-fonte em teste.