# Plano — T-11: Catálogo de Blueprints tipados (cron)

Data: 2026-09-13 · Branch `feat/cron-blueprints` · Base: `8ace5cf` (PR #36)

## Objetivo

Entregar a T-11 segundo o critério do spec: **um schema de slots gera as quatro
superfícies** (formulário, slash command, prompt-semente, deep-link
`hermes://`), e `fill_blueprint` produz os kwargs de `create_job` — **sem
segundo motor de jobs**. Origem no legado: `cron/blueprint_catalog.py` e o
critério em `_reversa_sdd/cron/tasks.md` (77–81).

## Critério de pronto (T-11 do spec)

- Uma definição única renderiza as quatro superfícies: `blueprint_form_schema`,
  `blueprint_slash_command` (+ `parse_blueprint_slash`), `blueprint_seed_prompt`
  e `blueprint_deeplink`.
- `fill_blueprint` valida os valores e devolve kwargs de `JobStore.create` — o
  mesmo executor, guard de ciclo de vida e validação de delivery das demais
  superfícies.
- Usuário não digita cron cru: a recorrência vem do `schedule_template` do
  blueprint; só as partes amigáveis (hora, dia) são parametrizadas.

## Decisões

- **Sem lista de plataformas hardcoded**: o slot `deliver` é `text` com
  `strict=False` e default `local`. A forma `plataforma:destino` é validada no
  `JobStore`; a adequação dinâmica do adapter é do dispatcher (critério da
  T-10). `origin` do legado é tratado como `local` (não há origem em kairos).
- Jobs kairos não carregam `skills`: a lista fica como metadado e nunca entra
  nos kwargs de `create`.
- Prompts do catálogo autocontidos, em português, sem referências a skills
  externas que não existem no kairos.
- Catálogo enxuto e fiel: **12 blueprints** cobrindo todas as combinações de
  slots (`time`, `enum` strict, `text`, `weekdays`, `interval_min`,
  `interval_hours` em faixa `start-end/step`, `day`, `recurrence`).
- `BlueprintFillError(ValueError)`: enum strict fora das opções, obrigatório
  ausente e slot desconhecido; mapeia para `422` com a mensagem citando o slot.

## Implementação

1. `kairos_cron/blueprints.py` (novo): `BlueprintSlot`, `AutomationBlueprint`,
   `WEEKDAY_PRESETS`, `CATALOG`, `get_blueprint`, `blueprint_form_schema`,
   `blueprint_slash_command`, `parse_blueprint_slash`, `blueprint_deeplink`,
   `blueprint_seed_prompt`, `_humanize_schedule`, `blueprint_catalog_entry`,
   `_resolve_schedule`, `fill_blueprint`, `BlueprintFillError`.
2. `kairos_cron/__init__.py`: exports do catálogo.
3. `kairos_web/cron_api.py`: `GET /api/cron/blueprints`,
   `GET /api/cron/blueprints/{key}`, `POST /api/cron/blueprints/{key}/jobs`;
   `BlueprintFillError` → `422`.
4. CLI: `kairos cron blueprint list|show|create` (grupo aninhado em `cron`);
   `create` aceita pares `slot=valor` e o slash command inteiro colado.
5. `kairos_cli/commands.py` e `tests/test_cli_surface.py`: a árvore declara o
   grupo `blueprint`; o teste de subcomandos reais passa a incluí-lo.

## Testes (45 novos)

- `test_cron_blueprints.py` (37): integridade do catálogo (12 chaves únicas,
  tipos de slot válidos, `deliver` não-strict, prompts string), fill com
  defaults gera cron válido via `compute_next_run`, shape dos kwargs, `local`/
  `origin` → `delivery=None`, `plataforma:destino` → `{"target": ...}`;
  formulário, roundtrip do slash (defaults e overrides), deep-link, seed
  prompt; validação (slot desconhecido, obrigatório ausente, hora inválida,
  enum strict); integração real com `JobStore.create` (com e sem delivery).
- `test_cron_surfaces.py` (8): CLI `list`/`show`/`create` (defaults,
  overrides e roundtrip do slash; erros sem escrita), API autenticada
  `list`/`show`/`create`, 404 de chave inexistente, `422` de fill, guard de
  ciclo de vida e validação de delivery ainda impostos pelo caminho comum.

## Validação

- `ruff check` + `ruff format --check`.
- Suítes cron/CLI verdes (240); sweep completo (2266 passed, apenas as duas
  reprovações conhecidas do Codex); vitest 168 + `tsc --noEmit` limpos.
- `ci.sh --fast` (só "Testes (unitários)" pela causa Codex conhecida).

## Docs

- `docs/agendamentos.md`: seção "Blueprints de automação".
- `docs/functional-progress.md`: seção do lote com validação local.

## Entrega

Commit → push `feat/cron-blueprints` → PR → 9 checks remotos → merge
`--delete-branch=false` → sync ff de `main` → próximo lote.
