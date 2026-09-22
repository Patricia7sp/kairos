# Lembretes proativos da agenda — monitor de cron tipo `calendar`

**Passo 2/P2 decidido em 22/09/2026:** a usuária autorizou o **monitor tipo
`calendar`** no `kairos_cron` para lembretes proativos da agenda local: a
fonte é o `.ics` já lido pela ferramenta `calendar` (v1, PR #77/#78), lida
**em processo** — sem segundo motor, sem shell —, e o modelo só roda quando há
uma ocorrência na janela ainda não lembrada. Reusa claim/ledger/entrega do
monitor de script. Expansão de RRULE é o passo seguinte (3) e não pré-condição
desta entrega.

## Objetivo

`kairos_cron` já roda o agente por cron e por monitor de script. O limiar de
um "lembrete proativo" é antagônico ao cron puro (o evento não tem hora fixa;
a hora vem do `.ics`) e ao monitor de script (não há comando que mude — há um
calendário que o agente já sabe ler). Este lote adiciona o **tipo de monitor
`calendar`**: o job dispara um turno **só** quando uma ocorrência entra na
janela `[agora, agora+janela_min]` e ainda não foi lembrada; a memória do que
já foi lembrado é o `monitor_state` persistido do job (mesmo storage dos
demais jobs). Ticks sem novidade ⇒ `suppressed`, sem custo de modelo nem
ledger (mesma regra do script: nunca "sucesso com efeito nenhum").

## Forma (fail-closed, mesma disciplina dos outros monitores)

- Monitor: `{"type": "calendar", "janela_min": int}` — `janela_min` inteiro
  1–1440 (default 120). `validate_monitor` aceita `script` **ou** `calendar`;
  forma desconhecida ⇒ `ValueError`.
- Estado: `{"remindidos": [chaves], "last_changed_at": iso|null,
  "last_checked_at": iso|null}` — as três chaves sempre presentes; chaves
  extras rejeitadas (`extra="forbid"` mental). Verificado por
  `validate_monitor_state(estado, kind="calendar")`.
- Chave de ocorrência: `start_utc_iso(s)|uid` (a parte da data permite podar
  chaves expiradas). Limite de sanidade: 500 chaves, 200 chars por chave.
- Decisão (`check_calendar_monitor`): fonte ausente/não configurada ou
  `problems` no parse ⇒ `SOURCE_ERROR` (nunca dispara por falha). Nenhuma
  ocorrência nova na janela ⇒ `NO_CHANGE`/`suppressed`. Ocorrência nova ⇒
  roda o agente e persiste as chaves da janela como `remindidos`.
- Limitação honesta documentada: RRULE não expandido (como a ferramenta) —
  evento recorrente é lembrado uma vez por UID enquanto não houver expansão.

## Entrega

1. `kairos_tools/calendar.py` — `calendar_source(home)` e `read_events(source)`
   viram **públicos** (os privados `_configured_source`/`_read_events` dão
   lugar aos públicos; a ferramenta continua chamando os mesmos).
2. `kairos_cron/calendar_monitor.py` (novo) — validação de monitor/estado,
   `check_calendar_monitor(home, monitor, estado, agora)`,
   `render_calendar_reminder(eventos)` (bloco injetado no prompt) e os tetos
   de forma (janela, chaves). Sem import de `kairos_cron.monitor` no topo é
   evitado ciclo — só `MonitorOutcome` (import tardio seguro não é necessário;
   `monitor` importa `calendar_monitor` apenas dentro de funções).
3. `kairos_cron/monitor.py` — `validate_monitor` despacha por tipo; `validate
   _monitor_state(state, kind="script")` e `default_monitor_state(kind)` cientes
   do tipo com default `"script"` (nada muda para o lote script existente).
4. `kairos_cron/jobs.py` — `_validate_job` valida `monitor_state` com o kind do
   `monitor`; `create`/`set_monitor` inicializam estado pelo kind; novo
   `set_calendar_monitor(job_id, janela_min)`; `record_suppressed_tick` grava por
   kind; guard de ciclo de vida corrigido (script condicionado ao tipo, e não à
   presença de qualquer `monitor`).
5. `kairos_cron/scheduler.py` — `run_monitor` ramifica por tipo;
   `_run_calendar_monitor` decide/persiste via claim; `tick` passa a injetar o
   bloco de lembretes no prompt (aparece entre o notepad e o prompt do job).
6. Superfícies:
   - CLI: `cron create [--monitor-calendar [--window-minutes N]]` (mutuamente
     exclusivo com `--monitor`) e subcomando novo `cron monitor-calendar-set
     ID [--window-minutes N]`; `monitor-run` despacha por tipo.
   - API: `PUT /api/cron/jobs/{id}/monitor` aceita `{type: "calendar",
     janela_min}` (modelo `MonitorSet`); `POST .../monitor/run` testa por tipo.
   - Painel: a linha de monitor exibe `calendar` como janela (read-only; o
     ajuste é CLI/API), sem "Testar fonte" (não há comando).
   - Blueprint: `agenda-lembrete` (intervalo de checagem, antecedência `janela_min`,
     tom) — `fill_blueprint` traduz `janela_min` em `monitor`.
7. Testes de comportamento (offline, `.ics` real em home temporária — sem ler
   fonte, sem mocks escondendo o caminho): decisão dispara com evento na
   janela e suprime depois (chave persistida), SOURCE_ERROR com
   `calendar.source` ausente e com `.ics` corrompido, forma/validação,
   `monitor-run` e `set_calendar_monitor` nas superfícies, blueprint
   `agenda-lembrete`, guard de ciclo de vida por tipo. Atualizar o conjunto de
   subcomandos esperado em `tests/test_cli_surface.py`.

## Gate

Suíte local inteira verde (`-m 'not runtime_live'`; cambio: a `RealImageTests`
é deselecionada sem a imagem) + `uv run ruff check .`/`format --check` +
`scripts/ci.sh --fast` antes do PR; CI 10/10 verde antes do merge. Após merge,
`git diff HEAD~1..HEAD` sem remoções acidentais e registro em
`docs/functional-progress.md`. RRULE/dateutil e MCP de terceiros seguem como os
passos 3 e 4 no plano das ferramentas.