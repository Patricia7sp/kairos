# Lote: Blueprints na UI Web (2026-09-13)

Objetivo: fechar o gap documentado entre o catálogo tipado de blueprints
(backend completo desde a T-11) e a tela Agendamentos da UI Web. A usuária
escolheu "Integrar blueprints na UI Web (Recommended)" como front seguinte.

## Contexto

- Backend pronto (T-11, PR #37): `GET /api/cron/blueprints`,
  `GET /api/cron/blueprints/{key}`, `POST /api/cron/blueprints/{key}/jobs`
  (body `{"values": {slot: valor}}`, 422 `BlueprintFillError`), além de
  `GET /api/cron/delivery-targets` (T-10).
- UI Web é vanilla JS servido de `kairos_web/ui/` (sem build). O pacote `web/`
  é somente o harness de testes (vitest + tsc) que importa as views nativas.
- A tela `js/views/cron.js` já tinha o formulário manual; faltavam o campo de
  entrega e o catálogo/formulário de blueprints.

## Decisões

- Campo de entrega é **texto livre `plataforma:destino`** com `<datalist>`
  derivado de `delivery-targets` (não select fechado): os alvos do gateway são
  plataformas sem destino, e o formato exige `pref:dest` — a UI apenas sugere,
  nunca hardcoda, e o valor é validado no backend.
- Catálogo carrega de forma **não bloqueante** (`loadCatalog()`): a primeira
  pintura nunca espera o catálogo, e um request pendente não pode segurar o
  path de disposed da view (o teste de escape que já existia depende disso).
- O formulário de blueprint é renderizado a partir do schema `fields`
  (tipos time/enum/weekdays/text), com defaults e `required` conforme
  `optional`/`strict`. Valores "local" no slot `deliver` seguem semântica do
  backend (não geram delivery).

## Passos

- [x] `api.js`: `blueprints()`, `criarBlueprint(key, values)`, `alvosEntrega()`.
- [x] `cron.js`: campo lead de entrega + datalist; secção de blueprints; card
  grid + botão Usar; formulário dinâmico com submit/cancelar.
- [x] `components.css`: `k-tag`, `k-blueprint-grid`, `k-blueprint-form`,
  `k-actions`.
- [x] Testes `cron-flows.test.ts`: catálogo + fill → POST jobs; delivery
  manual (envia `plataforma:destino`, omite vazio/"local", datalist).
- [x] Validação: vitest 170 + tsc --noEmit limpos.
- [ ] Docs atualizados (`agendamentos.md`, `functional-progress.md`).
- [ ] Commit, push, PR, checks remotos, merge, sync `main`.

## Validação

- `npx vitest run` → 170 passed; `npx tsc --noEmit` → limpo.
- Nenhuma mudança em Python neste lote; suítes existentes permanecem verdes.