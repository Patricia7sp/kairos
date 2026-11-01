# Cancelamento durante cleanup — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para executar sequencialmente. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Propagar cancelamento externo depois do dreno, preservando fechamentos normais e falhas reais.
**Architecture:** Compor barreira persistente com serialização do coordinator nos fechamentos compartilhados; distinguir o resultado do filho cancelado do cancelamento de quem aguarda. Reaproveitar o módulo de cleanup existente.
**Tech Stack:** Python 3.11, asyncio, unittest/pytest existentes; nenhuma dependência nova.
**Spec:** `docs/superpowers/specs/2026-11-01-cancelamento-cleanup-design.md`, aprovado com quatro ressalvas incorporadas.

## Global Constraints

- Root sequencial; revisão independente final; nenhuma reclassificação Sonar antes do aceite integrado.
- O coordinator sozinho não é barreira; usar `run_persistent_cleanup` por fora de `coordinator.run` nos `aclose` serializados.
- Sem teto global de dreno: testar retenção e liberação controlada; conservar timeouts locais e documentar risco de hang.
- Falha real de cleanup é primária, com cancelamento externo como causa; sem falha, relançar cancelamento após o dreno.
- Preservar D-PLUG.6, perda de posse, GeneratorExit e expiração natural após exaustão de liberação.
- Regressões e provas dos seis candidatos a falso positivo devem viajar no PR; arquivos ignorados são apenas auxiliares.
- Sem provedores reais, `runtime_live`, exportação Sonar local ou merge/deploy nesta autorização.

## Review Focus

- Cancelamento entregue no callback de conclusão do filho, antes de o waiter retomar.
- Cancelamento repetido com limpeza bloqueada, close concorrente, tentativa falha e retry.
- Falha operacional junto de cancelamento, sem ocultar a falha ou vazar detalhes em logs.
- Leases e escritores liberados somente depois do dreno, incluindo exaustão de release.
- Nenhum envio Telegram depois de cancelamento; serviço Web injetado não deve ser fechado.

### Task 1: RPC e composição de limpeza

**Files:** `kairos_providers/_async_cleanup.py`, `kairos_runtime/codex_rpc.py`, `tests/test_async_cleanup.py`, `tests/test_codex_rpc.py`.
**Interfaces:** adicionar `complete_cleanup(cleanup, *, task_name) -> None` que propaga `PersistentCleanupOutcome`; adicionar `cancel_owned_task(task) -> None` que consome somente o cancelamento esperado do filho. `CodexRpc.aclose()` preserva assinatura e compõe barreira externa com um coordinator por instância.

- [x] RED: testes de cancelamento no fim do leitor, dreno bloqueado, close repetido/concorrente, falha de writer e retry. Aguardar efeitos reais do RPC; controlar somente transporte e agendamento.
- [x] Implementar helpers e fechamento completo do RPC, incluindo writer após falhas, com tentativa compartilhada e cancelamento depois do dreno.
- [x] GREEN: `uv run pytest -q tests/test_async_cleanup.py tests/test_codex_rpc.py`; esperado: zero falhas.
- [x] Commit: `fix(runtime): preserva cancelamento no fechamento do RPC`.

### Task 2: Relay

**Files:** `kairos_runtime/docker_backend/model_relay.py`, `tests/test_docker_model_relay.py`.
**Interfaces:** manter `end_turn(turn_id)` e `aclose()`; um coordinator por fechamento do relay e por requisição ativa. Consumir os helpers da Task 1.

- [x] RED: cancelamento no fim de runner/requisição, dreno bloqueado e cancelado novamente, close repetido/concorrente e requisição seguinte após end_turn.
- [x] Proteger fechamento integral e dreno de requisição; revogar admissão antes de aguardar e não apagar referência de uma requisição nova.
- [x] GREEN: `uv run pytest -q tests/test_async_cleanup.py tests/test_codex_rpc.py tests/test_docker_model_relay.py`; esperado: zero falhas.
- [x] Commit: `fix(runtime): drena o relay antes de propagar cancelamento`.

### Task 3: Shutdown Web

**Files:** `kairos_web/server.py`, `tests/test_web_shutdown_cancellation.py`.
**Interfaces:** manter `_lifespan(application)` e as regras de ownership de serviço/runtime_client.

- [x] RED: cancelamento no fim do scheduler e durante close do serviço, inclusive repetido; serviço próprio fecha, injetado permanece sob responsabilidade externa, estado é limpo e falha permanece explícita.
- [x] Proteger o shutdown completo com a barreira persistente e conservar os registros operacionais.
- [x] GREEN: `uv run pytest -q tests/test_web_shutdown_cancellation.py tests/test_web.py`; esperado: zero falhas.
- [x] Commit: `fix(web): conclui shutdown antes de propagar cancelamento`.

### Task 4: Telegram

**Files:** `kairos_gateway/inbound.py`, `tests/test_telegram_inbound.py`.
**Interfaces:** manter dispatch, protocolo e autorização; o stream deve relançar cancelamento e a digitação deve terminar antes da propagação do cancelamento do despacho.

- [x] RED: cancelamento direto da digitação, durante stream e no fim da digitação; nenhum envio final após cancelamento; erro operacional comum conserva tratamento anterior.
- [x] Corrigir somente as fronteiras de cancelamento e dreno; não alterar outros canais.
- [x] GREEN: `uv run pytest -q tests/test_telegram_inbound.py`; esperado: zero falhas.
- [x] Commit: `fix(telegram): preserva cancelamento e impede envio tardio`.

### Task 5: Ownership, provas portáveis e aceite final

**Files:** `kairos_integration/turn_ownership.py`, `tests/test_interaction_turn_ownership.py`, `tests/test_cancellation_contracts.py`, relatório em `docs/superpowers/reports/2026-11-01-cancelamento-cleanup.md`.
**Interfaces:** manter acquire/refresh/release e seus resultados; usar o helper de dreno com barreira durante a liberação, conservando expiração natural após tentativas esgotadas.

- [x] RED: cancelamento ao terminar heartbeat e deadline, repetição durante dreno, sucessor sem sobreposição e liberação com tentativas esgotadas.
- [x] Corrigir os finally sem mudar os except já corretos; preservar perda de posse e erros aninhados.
- [x] Versionar testes/evidência sanitizada dos seis candidatos: operação real de thread e SQLite, carrier de cleanup, supervisor e os dois except de ownership. Preservar as classificações como propostas até revisão final.
- [x] GREEN: `uv run pytest -q tests/test_interaction_turn_ownership.py tests/test_cancellation_contracts.py`; esperado: zero falhas.
- [x] Commit: `fix(runtime): preserva cancelamento durante liberação de leases`.
- [ ] Rodar `scripts/ci.sh` completo com pin de Codex por SHA; revisar resultados/skips e manter logs vinculados ao SHA final.
- [ ] Revisão independente da branch, com execução focada declarada e validação integrada pelo Root. Corrigir achados relevantes por RED→GREEN.
- [ ] Publicar branch/PR concretos e conferir CI/Sonar do SHA efetivo. Apresentar resultado antes da decisão de merge; não reaproveitar autorização do PR #126.
