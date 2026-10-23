# Backup síncrono — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para executar sequencialmente. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** alinhar a assinatura do backup ao seu consumidor CLI e resolver S7493 preservando os contratos de backup.
**Architecture:** mesma função e mesmo corpo síncrono; handler direto; testes e harness chamam a função sem event loop.
**Tech Stack:** Python 3.11, unittest/pytest, Docker e Sonar existentes; dependências inalteradas.
**Spec:** [desenho escolhido](../specs/2026-10-23-backup-sincrono-design.md).

## Global Constraints

Preservar o corpo/ordem do backup e todos os guards, locks, fsync, saídas e
códigos de erro. Sem threads, dependências, exclusões Sonar ou alteração de
limiar. Não modificar outros comandos async. Testar comportamento, não fonte.

### Task 1: contrato síncrono, consumidores e validação

**Files:** `kairos_cli/backup.py`, `kairos_cli/handlers.py`,
`tests/test_cli_backup_boundary.py`, `tests/test_skill_adoption_backup.py`,
`tests/test_skill_org_backup.py`, `tests/test_skill_publication_backup.py`,
`tests/test_skill_publication_sync_review.py`, `tests/skill_publication_image_acceptance.py`.

**Interfaces — produz:** `def run_backup(home: Path, args) -> int`, resultado
e efeitos completos na chamada direta; `cmd_backup(args) -> int` mantém o
contrato do executável. Consome a implementação de backup B2 sem mudar I/O.

- [ ] Baseline: pytest nos cinco arquivos de testes de backup e sync review; esperar zero falhas.
- [ ] RED: adaptar o helper em BackupBoundaryTests para chamada direta; executar `test_new_home_without_database_preserves_excludes_and_count`, exigindo retorno0 e tar/JSON reais. Esperar falha porque o async atual não executa o backup na chamada.
- [ ] GREEN: tornar somente run_backup síncrono, remover asyncio.run de cmd_backup e adaptar todos os consumidores de teste/harness, incluindo snippet de subprocesso.
- [ ] Validar: repetir regressões de backup/sync, Ruff check/format e diff-check; esperar zero falhas, saída limpa.
- [ ] Integração: scripts/ci.sh completo sem upload Sonar local, clientes anteriores reais, imagem reconstruída e testes offline. Reaproveitar verificações válidas de superfícies sem alteração; registrar comandos/resultados e skips.
- [ ] Revisão independente da branch; corrigir bloqueadores com RED→GREEN e repetir somente as verificações afetadas.
- [ ] Commit `fix(backup): executa backup de forma síncrona` e PR em rascunho com CI/Sonar sobre o SHA proposto. Main só pode ser declarada A após integração e análise dessa revisão.

## Review Focus

Chamador real CLI sem asyncio.run residual; nenhum backup real omitido por
fixture; subprocesso/harness importando API nova; retorno0 somente após
arquivo publicado; recusa antes da publicação preservando saída anterior e
descartando parcial. Erro de fsync do pai após replace recusa o atestado de
durabilidade e não garante rollback; esse limite preexistente não é alterado.

Auto-revisão: uma tarefa, interface única e consumidores enumerados. Escopo
exato escolhido no diálogo; execução nativa autorizada, sem nova decisão de
arquitetura ou aprovação repetida. Merge/deploy da correção exigem o aceite
aplicável à nova revisão, distinto do aceite já executado do PR114.
