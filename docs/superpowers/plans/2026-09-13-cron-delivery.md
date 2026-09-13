# Plano — T-10: Integração com o DeliveryDispatcher do gateway (cron)

Data: 2026-09-13 · Branch `feat/cron-delivery` · Base: `c2e9003` (PR #35)

## Objetivo

O legado fazia `cron_delivery_targets()` derivar as plataformas configuradas do
gateway e engolia a entrega também pelo `cronjob` do agente. Em kairos não há
tool de modelo — o turno de cron só gera texto — e a entrega é ônus de
irmandade com o dispatcher existente (`kairos_gateway.service.GatewayService`).
Entregar a T-10 segundo o critério do spec: **destinos derivados dos adapters
registrados, sem lista de plataforma hardcoded; a saída do job percorre o
ledger durável de obrigações do gateway**.

## Critério de pronto (T-10 do spec)

- Origem no legado: `cron/scheduler.py::cron_delivery_targets`, consumido em
  `hermes_cli/web_routers/cron.py:103-105` (implicit `local` + derivados).
- Destinos derivados dos adapters registrados — sem lista hardcoded; o campo
  `delivery` de job valida o prefixo contra o conjunto registrado quando ele
  está disponível.
- A saída completada vira obrigação durável (`delivery_obligations`,
  `pending`) no ledger que o gateway drena — não há segundo sistema de entrega.

## Decisões

- Aderência: `plataforma:destino`; prefixo antes do primeiro `:` escolhe o
  adapter (semântica de `adapter_for`); forma imposta na criação e na
  releitura. Adequação dinâmica do adapter é decisão do dispatcher, que já
  suspende obrigações sem adapter.
- O `local` implícito é decisão da superfície de listagem (como no legado).
- `record_cron_delivery` abre `state.db` só quando o job tem `delivery`
  (volume virgem não materializa por causa de lote); payload truncado a 64 KiB.
- Persistência é best effort: falha ao gravar obrigação é logada e nunca vira
  falha do turno; turno falho nunca gera obrigação.

## Implementação

1. `kairos_cron/delivery.py` (novo): `DeliveryTargetError(ValueError)`,
   `cron_delivery_targets(adapters)`, `validate_delivery(delivery, adapters)`,
   `record_cron_delivery(home, obligation_id, target, payload)`.
2. `jobs.py`: `create(..., delivery=None)` opcional; forma revalidada em
   `_validate_job`; chave só gravada quando pedida (jobs existentes intactos).
3. `scheduler.py`: acumula texto `delta`; turno completado com `delivery`
   grava `cron-<execution>` (best effort via `logger.exception` — o BLE001 é
   suprimido por logar, então sem `noqa`).
4. `kairos_web/cron_api.py`: `CreateJob.delivery` opcional; `operate` mapeia
   `DeliveryTargetError` em `422` com a justificativa; plataforma validada
   contra `app.state.delivery_adapters` (vazio por padrão); novo
   `GET /api/cron/delivery-targets` (`local` + derivados).
5. CLI: `kairos cron create --deliver plataforma:destino`.
6. Public surface `kairos_cron/__init__.py`: exports de delivery.

## Testes (30 novos)

- `test_cron_delivery.py` (25): derivação sem lista fixa e `local` implícito;
  forma rejeitada (vazio, sem `:`, excedente, controle); plataforma
  desconhecida rejeitada quando há registrados; gravação/pending/idempotência/
  truncamento; integração do scheduler (turno completado grava payload exato,
  job sem delivery não grava, turno falho não grava, falha de storage não
  quebra o turno).
- `test_cron_surfaces.py` (5): CLI e API criam job com `delivery` persistido;
  CLI rejeita forma inválida sem nada escrito; API `422` com justificativa
  quando o prefixo não está entre registrados; `delivery-targets` autenticado,
  só `local` sem registro e derivado com registro.

## Validação

- `ruff check` + `ruff format --check`.
- Suítes cron/schema/storage e superfícies CLI/API verdes.
- vitest + `tsc --noEmit`; `ci.sh --fast` (só "Testes (unitários)" pela causa
  Codex conhecida); sweep completo (apenas as duas reprovações esperadas).

## Docs

- `docs/agendamentos.md`: seção "Entrega da saída pelo ledger do gateway".
- `docs/functional-progress.md`: seção do lote com validação local.

## Entrega

Commit → push `feat/cron-delivery` → PR → 9 checks remotos → merge
`--delete-branch=false` → sync ff de `main` → T-11 (catálogo de Blueprints).