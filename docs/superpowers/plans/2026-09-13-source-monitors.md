# Monitores de fonte para agendamentos recorrentes

Continuação autorizada do inventário funcional: um agendamento recorrente pode ter
uma fonte executada a cada tick devido; o agente só roda quando a saída da fonte
muda. Base: PR #31 integrada (`a896c6f`). Sem migração de banco; o ledger
`executions` não recebe linha para tick suprimido nem para erro de fonte, e o
orçamento de ocorrências não é consumido por eles.

## Contrato

- Persistência em `cron/jobs.json`: `monitor: {"type": "script", "script": "<cmd>"}`
  e `monitor_state: {last_output_hash, last_changed_at, last_checked_at}`.
  Tipos fora de `script`, campos extras e `once` com monitor são recusados; limpar
  um monitor de job não monitorado é erro. Habilitar monitor redefine o estado:
  próximo tick devido vira primeira verificação.
- Decisão por hash da saída: primeira verificação ou mudança → turno normal do
  agente, novo hash gravado na mesma reescrita do claim; saída igual → tick
  suprimido (`cron.no_change`), cadência avança, nada reservado; falha da fonte
  (timeout, saída não nula, executável ausente) → `cron.monitor_error`, nunca
  mudança, hash anterior intocado. Pausado/esgotado/não devido nunca executa fonte.
- Fonte: `shlex.split` + `shell=False`; processo em grupo próprio, morto por
  inteiro no timeout (30 s padrão, 120 s máximo); saída limitada (64 KiB padrão,
  256 KiB máximo); ambiente mínimo `HOME`/`PATH`/`LANG`/`LC_ALL`/`TZ`, sem
  `KAIROS_HOME`, token Web ou passphrase para o script.
- Superfície: CLI `cron monitor-set|monitor-clear|monitor-show|monitor-run` e
  grupo `monitoring list|status|test`; `cron create --monitor`; API
  `GET/PUT/DELETE /api/cron/jobs/{id}/monitor` e `POST .../monitor/run`; painel
  exibe fonte, última verificação e última mudança, com edição e teste.

## Implementação e validação

1. Backend TDD: execução real (sucesso, stderr, código não nulo, executável
   ausente, timeout matando o grupo de processos, truncamento), decisão
   first_run/no_change/source_error com eventos, hash antes do stream, avança de
   cadência em ticks suprimidos, orçamento e ledger intocados, pausado sem fonte.
2. CLI e API compartilham contrato (validação, 404/422/503); UI nativa com campo
   de fonte na criação, exibição por job e ações de editar/testar/remover, com
   vitest das superfícies.
3. Navegador real em armazenamento descartável com scheduler real/HTTP controlado;
   revisão independente, CI local, imagem e atualização de manual/progresso antes
   da publicação. Aceite não usa a conta nem os prompts da usuária.