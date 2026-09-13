# Plano — T-09: Guard do ciclo de vida do gateway (cron)

Data: 2026-09-13 · Branch `feat/lifecycle-guard` · Base: `fbecba7` (PR #34)

## Objetivo

O guard legado de `kairos_cron/dispatch.py` era um matcher de substring
(`_RESTART_MARKERS`: `"pkill"`, `"kill -9"`, `"docker restart"`, ...): barrava
prosa que apenas citasse esses comandos — exatamente o falso positivo que a
T-09 proíbe ("padrão command-shaped que **não dispara em prosa**"). Entregar o
guard command-shaped ancorado em identificador concreto, imposto na criação do
job em todas as superfícies de escrita.

## Critério de pronto (TT-07 do spec)

- Agendar `hermes gateway restart` rejeitado na criação pela CLI **e** pela API.
- Prompt em prosa citando 'Kong API gateway restart' é aceito.
- Guard ancorado em: `hermes gateway`, `launchctl … hermes-gateway`,
  `systemctl … hermes-gateway`, `pkill` contra o gateway.

## Divergências registradas (kairos é independente do legado)

- A ferramenta `cronjob` do agente e o `terminal_tool` sob `_HERMES_GATEWAY=1`
  **não existem** em kairos (sem tool de modelo; a API Web é o narrow waist).
- O monitor de kairos **é executado no host** (subprocessos com `shlex`, sem
  shell) → o guard também cobre o **script de monitor** (novo vetor vs. legado,
  que só escaneava prompt + `script` como arquivo).

## Implementação

1. `kairos_cron/lifecycle_guard.py` (novo): `LifecycleGuardError(ValueError)`,
   regex de 5 ramos ancorados (kairos+hermes), colapso de `\`+nova linha,
   passthrough de data-sink (grep/journalctl/sqlite3/psql; `… | sh` barrado),
   `check_gateway_lifecycle(prompt, script=None)` com mensagem explicando o laço.
2. `dispatch.py`: guard removido; `reject_gateway_restart_job` e
   `LifecycleGuardError` re-exportados por compatibilidade (API pública intacta).
3. `jobs.py`: guard em `create` (prompt + monitor) e `set_monitor`; **removido
   do caminho de releitura** (`_validate_job`) — política de entrada, senão um
   job salvo antes de endurecimento trancaria o documento no boot.
4. `kairos_web/cron_api.py`: `operate` responde `422` com a justificativa do
   guard (antes do `ValueError` genérico).
5. Public surface `kairos_cron/__init__.py`: exporta `check_gateway_lifecycle`.

## Testes (13 novos)

- `test_cron.py`: comandos rejeitados (kairos gateway restart/stop, hermes
  gateway restart, systemctl --user, docker restart kairos, pkill/pkill -9,
  killall, launchctl kickstart ai.hermes.gateway); prosa aceita; data-sink
  aceito; `… | sh` barrado; continuação de linha barrada; monitor rejeitado.
- `test_cron_operational.py`: rejeição sem escrita; prosa/diagnóstico aceitos;
  monitor rejeitado no create e no set_monitor; leitura de job legado tolerante.
- `test_cron_surfaces.py`: CLI exit≠0 e API 422 com justificativa; prosa 201;
  monitor via API 422.

## Validação

- `ruff check` + `ruff format --check`.
- Suíte cron/schema/storage: **176 verdes**.
- vitest **168** (14 arquivos), `tsc --noEmit`.
- `ci.sh --fast`: só "Testes (unitários)" falha (Codex 0.154.0 vs 0.153.4, conhecido).
- Sweep: **2.237 aprovados** / 13 pulados / 24 desmarcados / 5.600 subtests.

## Docs

- `docs/agendamentos.md`: seção "Guard do ciclo de vida do gateway".
- `docs/functional-progress.md`: seção do lote com validação local.

## Entrega

Commit → push `feat/lifecycle-guard` → PR (#35) → 9 checks remotos → merge
`--delete-branch=false` → sync ff de `main` → próximo lote.