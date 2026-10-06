# RF-08: observar o stream sem poder transformá-lo

Base `main` (pós-merge #88, `69e4616`). Recorte escolhido pela usuária em
2026-10-03: a família `STREAM` dos 37 hooks, que é **Must** (RF-08) da unit
`plugins` e hoje está registrada e nunca disparada.

## Contexto — por que esta família não pode usar o caminho do lote anterior

O lote #88 deu emissor a seis hooks de observação, todos **awaited**:
`HookEmitter.emit` segura o turno até `invoke_hook` responder em
`asyncio.to_thread`. Isso é aceitável em `pre_tool_call` (uma chamada por turno)
e **inaceitável por token**: `on_stream_delta` awaited em cada delta coloca um
callback de terceiro entre o token e o usuário, e a spec é explícita
(`_reversa_sdd/plugins/requirements.md:49`, `:93`):

> Streaming: `on_stream_start`, `on_stream_delta`, `on_stream_end`,
> `on_interim_message` — disparados **assincronamente fora do caminho do token**,
> com payloads normalizados **imutáveis**: observam, **não transformam** o stream.

E o critério de aceitação da spec é literalmente o teste que este lote tem que
passar:

> Dado um plugin registrado em `on_stream_delta` / Quando ele tenta alterar o
> texto recebido / Então o stream entregue ao usuário permanece inalterado

O legado resolve isso em `hermes-agent/agent/plugin_stream_hooks.py`: uma fila
bounded **por callback**, com thread daemon, e `enqueue_plugin_stream_hook()`
que só faz `put_nowait` — código de plugin nunca roda inline. O payload base é
`_stream_hook_base_payload()` (`run_agent.py:6878`): `turn_id`, `iteration`,
`session_id`, `model`, `provider`, `surface`; `on_stream_delta` acrescenta
`delta` + `kind` (`text`/`reasoning`); `on_stream_end` acrescenta `final_text`,
`finished`, `error`. `surface` é `user`/`agent` lá e não tem equivalente aqui:
o runtime é single-surface, e a chave equivalente é `source`, que o turno já
carrega.

## Decisões que este lote toma (e por quê)

1. **Dois conjuntos, um de cada modo de despacho.** `EMITTED_HOOKS` continua
   sendo os seis hooks awaited; a família stream ganha `ENQUEUED_HOOKS`. `emit`
   **recusa** a família stream com `UnknownHook`: quem tentar colocar a
   família stream no caminho awaited recebe erro de programação em vez de
   degradar o stream em silêncio. O contrato vira impossível de violar por
   descuido, não por convenção.
2. **A não transformação é estrutural, não uma guarda.** O callback recebe
   `callback(**item)`: a dupla asterisco entrega ao plugin um dict **novo**,
   dele. Reescrever `payload["delta"]` muda só essa cópia — e o texto que o
   usuário leu já estava decidido antes do despacho. Um `MappingProxyType`
   seria mais bonito no papel e decorativo no fato: `**` copia antes, e um
   `TypeError` só apareceria se o proxy escapasse do `**` — o que ele não
   escapa. O que sustenta o critério é não haver, no caminho do token, nada que
   o plugin alcance; não um erro de escrita ao tentar.
3. **Fila bounded, drop-oldest, com contador.** Cada callback de cada turno tem a sua fila
   (`maxsize`), enfileirar é `put_nowait`, e fila cheia descarta o **mais
   antigo** e tenta de novo. Descartar o mais antigo é a política certa para
   quem observa: um consumidor lento perde o passado, nunca o presente nem o
   `on_stream_end`, e nunca segura o token. Cada descarte conta e é logado
   esparso (primeiro, depois as potências de dois) — descarte silencioso
   esconderia uma telemetria furada.
4. **Esgotamento no fim do turno, com timeout.** O legado não drena (thread
   daemon morre com o processo). Aqui o dreno acontece no `finally` do turno,
   pelo mesmo `run_persistent_cleanup` de D-PLUG.6, com timeout: callback que
   trava não segura o fim do turno.
   O produtor conclui a limpeza operacional (histórico, gateway e posse) antes
   de entregar `turn_end`, e drena somente os observadores depois. Cada turno
   recebe filas próprias do registro compartilhado. A sentinela de fechamento
   espera espaço sem descartar uma observação extra, dentro do mesmo timeout.
   Workers usam threads daemon exclusivas, sem ocupar o executor padrão:
   callback bloqueado não impede o encerramento do processo depois do timeout.
5. **Ordem só existe dentro de um hook e turno.** Cada (turno, hook, plugin) tem um worker, e
   a fila dele preserva a ordem; entre `on_stream_start`, `on_stream_delta` e
   `on_stream_end` não há ordem, porque são workers distintos em threads
   distintas — e o de delta é, de propósito, mais lento que o de end. Quem
   precisar de ordem entre esses três usa o caminho awaited dos hooks de
   sessão. Prometer ordem aqui seria um contrato que a concorrência não
   sustenta.
6. **`on_interim_message` fica sem emissor, e o CLI diz isso.** Não existe
   superfície de mensagem interina no Kairos (o legado emite para o TUI, em
   `run_agent.py:6778`). Emitir seria inventar surface; o relatório de hooks
   continua listando-o como `SEM EMISSOR`.

## Entregas

1. `kairos_plugins/stream_dispatcher.py` — fila por callback, worker por
   callback, `enqueue` síncrono, `aclose` com dreno e timeout, contadores de
   fila cheia.
2. `kairos_plugins/emitter.py` — `ENQUEUED_HOOKS` ao lado de `EMITTED_HOOKS`,
   com o invariante de que são disjuntos e a família stream é toda enfileirada.
3. `kairos_integration/interaction_service.py` — `on_stream_start` na abertura
   de cada stream do provedor, `on_stream_delta` por delta de texto e de
   reasoning, `on_stream_end` no fechamento com `final_text`/`finished`/`error`,
   e o dreno no `finally` do turno.
4. `kairos_integration/composition.py` — `build_plugin_hooks` devolve emissor e
   despachante a partir do mesmo registro.
5. `kairos_cli/hooks.py` — o relatório passa a distinguir "emite (awaited)" de
   "enfileira (observador)", com as duas contagens.

## Fora de escopo (declarado)

- **Transformação de stream** (RF-09 `transform_llm_output`, RF-11 ordem de
  vitória): mudam o que o usuário vê; é a decisão de política que D-PLUG.2
  deixou de fora.
- **`transform_api_error_classification`** (RF-12/G-21): não é observação de
  stream e depende de decidir o que fazer quando o plugin mente sobre a
  classificação de erro.
- **`telemetry_schema_version`**: o legado injeta uma constante de versão no
  payload do observador. Aqui não há constante equivalente para importar, e
  criar uma para três hooks seria superfície nova sem consumidor — o contrato
  do payload viaja na versão do manifesto até existir um segundo consumidor.
- **`on_interim_message`** e os demais 28 hooks sem emissor: sem superfície no
  build, sem emissor, reportados como tal.

## Validação

`tests/test_plugin_stream_hooks.py`, todo de comportamento, com plugin de
verdade em disco:

- o callback que tenta alterar o payload **não** altera o texto que o
  consumidor do turno recebe (critério de aceitação da spec);
- callback que trava **não** segura o `turn_end`: o turno termina enquanto o
  callback está bloqueado, e o dreno entrega o que enfileirou;
- fila cheia: o turno termina, o contador de descarte sobe e o log diz que
  descartou (sem transform, sem travar);
- `on_stream_start`/`on_stream_end` com o payload base real do turno
  (`conversation_id`, `round_index`, `attempt`, `model`, `provider`,
  `source`), `final_text` acumulado e `error` no caminho de falha do provedor;
- `emit("on_stream_delta")` **levanta** — o caminho awaited não alcança a
  família stream;
- fechar um turno não recria os workers de outro turno ativo;
- fechar uma fila cheia não descarta uma observação extra, e a espera por
  capacidade respeita o timeout;
- falha de limpeza não entrega `turn_end` de sucesso, e `on_stream_end`
  reflete falha no fechamento do provider preservando a falha primária;
- um subprocesso com callback bloqueado sai após o dreno sem liberar o callback;
- `on_interim_message` continua `SEM EMISSOR` no relatório.

Suíte completa + `ruff check` + `ruff format --check` + `scripts/ci.sh --fast`
verdes. **Sem change-detector** e sem ler código-fonte em teste.
