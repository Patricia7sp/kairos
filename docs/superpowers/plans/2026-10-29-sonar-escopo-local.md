# Escopo explícito do Sonar local — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que o CI local publique uma análise sem identificar seu destino.

**Architecture:** Estender `scripts/ci.sh`, mantendo o scanner remoto do workflow intacto. O comando padrão pula a exportação local; opções explícitas selecionam PR ou main. Git real atesta branch, commit e ausência de alterações antes de iniciar o scanner.

**Tech Stack:** Bash, Git e unittest/Python 3.11; nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-10-14-sonar-cobertura-advisory-design.md`, decisão 6; `docs/decisoes.md`, D-CI.SONAR. O ajuste de segurança acrescenta seleção explícita ao contrato local; token/binário continuam pré-requisitos e skips continuam explícitos.

## Global Constraints

- Análise advisory: não mudar o gate, exclusões, workflow ou classificações de alertas.
- Nenhuma exportação Sonar local nesta implementação. Scanner de teste somente simulado; a análise real será a do CI remoto existente.
- `--fast` conserva seu comportamento e pode ser combinado com o destino Sonar.
- Token permanece no ambiente; não imprimi-lo nem passá-lo como argumento.
- Reutilizar a worktree isolada; implementação pelo Root e revisão independente ao final.

## Review Focus

- Token herdado sem seleção de destino: nenhum scanner iniciado, com skip explícito.
- PR ausente/malformado ou opções incompatíveis: erro antes de qualquer exportação.
- Main pedida a partir de uma feature, PR pedido da main ou HEAD destacado: recusar sem scanner.
- Arquivo modificado, staged ou não rastreado: recusar para não atribuir bytes diferentes ao commit informado.
- Falha do scanner: o CI local deve terminar em falha, sem mensagem de análise aprovada.

### Task 1: Seleção de destino e comprovação do checkout

**Files:**
- Modify: `scripts/ci.sh`.
- Create: `tests/test_ci_sonar_local.py`.
- Modify: `tests/test_ci_sonar_config.py` (substituir a inspeção textual do scan pela cobertura comportamental nova).
- Modify: spec acima e D-CI.SONAR para documentar o contrato local.

**Interfaces:**
- `scripts/ci.sh [--fast] [--sonar-pr N | --sonar-main]`.
- Sem destino ou sem binário/token: skip explícito e nenhum scanner.
- PR: `sonar.pullrequest.key`, branch Git observada, base `main` e `sonar.scm.revision` do HEAD limpo.
- Main: somente branch `main`, com `sonar.branch.name=main` e revisão do HEAD limpo.
- Entrada CLI inválida: exit 2. Recusa do checkout ou falha do scanner: falha agregada do CI (exit 1).

- [x] Escrever regressão que executa o CI real em repositório Git temporário, com dependências pesadas e scanner externos substituídos na fronteira. PATH isolado impede acessar scanner real.
- [x] Demonstrar RED: mesmo com token e scanner disponíveis, a chamada padrão antiga inicia o scanner sem destino.
- [x] Implementar opções, atestação Git e argumentos explícitos no script existente.
- [x] Cobrir PR, main, pré-requisitos ausentes, entradas inválidas, HEAD destacado, árvores sujas e erro do scanner; observar GREEN.
- [x] Atualizar os contratos documentais e executar ruff, shellcheck e checks de diff.
- [ ] Executar `scripts/ci.sh` completo sem selecionar exportação local; revisar independentemente o delta.
- [ ] Publicar PR em rascunho e conferir CI/Sonar do SHA final. Merge/deploy exigem instrução específica posterior.

## Referência dos parâmetros

[Parâmetros de análise SonarQube Cloud](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/analysis-parameters/parameters-not-settable-in-ui).
