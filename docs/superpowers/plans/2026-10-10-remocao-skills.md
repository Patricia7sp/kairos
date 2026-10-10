# Remoção reversível de skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remover skills com snapshot completo, restaurar por ID e impedir que sync reinstale exclusões deliberadas.

**Architecture:** Migração aditiva conserva o journal create/rollback e acrescenta remove/restore com BLOBs, eventos e timeline comum. Um coordenador projeta instalação/origem, compartilhando o lock entre todos os escritores. O filesystem usa as primitivas do plano de plugins e reconcilia somente IDs conhecidos com provas completas.

**Tech Stack:** Python 3.11, SQLite, biblioteca padrão, unittest/pytest/uv, Linux com descritores, mount ID e rename sem substituição.

**Spec:** `docs/superpowers/specs/2026-10-10-remocao-segura-design.md`, aprovada em 2026-10-10.

## Global Constraints

- Actor.USER_FOREGROUND antes de I/O; remoção sem `--yes` recusa antes de lock/banco: exit 77. Nome kebab-case existente, até 64 caracteres.
- Snapshot: 64 MiB por árvore, 16 MiB por arquivo auxiliar, 64 KiB para SKILL.md, 4096 entradas, profundidade 32; SKILL.md regular na raiz, sem exigir semver de criação nova.
- Snapshot preserva bytes, ocultos, diretórios vazios e modos ordinários; arquivos privados 0600/diretórios 0700; recusar links, arquivos especiais, mounts, bits especiais e ACL/xattrs não preserváveis.
- Origem manual exige instalação corrente e dev/inode correspondentes; bundled exige pacote confiável, entrada única, manifesto v2 e inventário SHA-256 completo correspondente. Origem desconhecida/organizacional recusada, sem bypass.
- Todas as tabelas/eventos/IDs create|rollback antigos preservados; ordem global por sequência inteira, nunca timestamps.
- Tombstone dura até restore committed, inclusive após desaparecimento/reaparecimento no bundle. Snapshot não tem coleta automática.
- Restore publica cópia nova sem substituir; rollback antigo não apaga restore/reinstalação; nenhum escritor ignora pendências da outra família.
- Exit 0 efeito/idempotência provada, 2 uso, 77 confirmação/ator, 69 garantia ausente, 1 conflito/corrupção/I/O; diagnósticos sem conteúdo ou backend bruto.
- Sem testes que leem código-fonte ou congelam catálogos/versões. Sem runtime_live sem credenciais; skips não provam caminhos.

## Review Focus

- SKILL.md legado sem semver e assets binários: remover/restaurar sem aplicar parser de criação; tarefas 1 e 3.
- Inode idêntico no nome, mas operação de restore mais recente: rollback antigo não remove a instalação corrente; tarefa 2.
- Bundle muda ou desaparece enquanto a exclusão está ativa: não reinstalar e não perder snapshot; tarefa 4.
- Cliente antigo permanece vivo após migração: sua escrita falha no SQLite sem alterar histórico; tarefa 1.
- Edição por descritor aberto após rename: preservar conteúdo e registrar conflito, em vez de confirmar exclusão; tarefa 3.

## Organização dos arquivos e execução

Depende da tarefa 1 de `2026-10-10-remocao-plugins.md`, que produz `kairos_filesystem` e seus contratos. O journal da tarefa 1 abaixo pode ser desenvolvido em paralelo após aprovação das assinaturas; tarefas 2 e 3 dependem dele, tarefa 4 depende de todas. Agente A mantém filesystem/plugins; agente B mantém schema/repositórios; coordenador mantém serviços/CLI/sync e integra. Rever contrato e qualidade com agentes independentes a cada tarefa. Um arquivo tem somente um escritor por vez. Não mergear etapas incompletas de skills; entregar um PR coeso após gates. Método multiagentes já escolhido; execução aguarda revisão dos planos.

### Task 1: Contratos de remoção e journal aditivo

**Files:** Create `kairos_skills/removal_contract.py`, `kairos_state/skill_removals_schema.py`, `kairos_state/skill_writer.py`, `kairos_state/repositories/skill_removals.py`, `tests/test_skill_removal_state.py`; Modify `kairos_state/schema.py`, `kairos_state/migrations.py`, `kairos_state/connection.py`, `kairos_cli/skill_mutations.py`; regressão `tests/test_skill_mutation_state.py`, `tests/test_state.py`.

**Interfaces:** Consumir `TreeCapture/TreeEntry` da tarefa compartilhada. Produzir `SkillTreeAction` com REMOVE/RESTORE; `SkillTreeDraft(operation_id: str, home_id: str, action: SkillTreeAction, name: str, actor: Actor, created_at: float, provenance: Provenance, proof: TreeCapture, snapshot_id: str, reverts: str | None = None)`; `SkillTreeRecord(draft: SkillTreeDraft, state: SkillMutationState)` com propriedades operation_id/name/action/provenance/reverts; `InstallationProof(operation_id: str, provenance: Provenance, device: int, inode: int)`; alias `CommonRecord = SkillMutationRecord | SkillTreeRecord`. Produzir `SkillRemovalRepository(connection: sqlite3.Connection, *, home_id: str)` com `get(id: str) -> SkillTreeRecord | None`, `snapshot(id: str) -> TreeCapture`, `prepare_remove(draft: SkillTreeDraft, snapshot: TreeCapture) -> SkillTreeRecord`, `prepare_restore(draft: SkillTreeDraft) -> SkillTreeRecord`, `finish(id: str, state: SkillMutationState) -> SkillTreeRecord`. `snapshot_id` de remove é seu próprio ID; restore referencia o remove; proof de restore é a cópia nova, snapshot continua sendo os bytes da remoção.

- [ ] Escrever testes `test_migration_preserves_legacy_ids_content_and_pending`, `test_timeline_import_and_new_events_have_one_order`, `test_prepare_snapshot_is_atomic`, `test_binary_snapshot_roundtrip_and_corruption_refused`. Criar banco v7 por `migrate(target=7)`, operações reais e pendentes; migrar; exigir mesmas evidências antigas, bytes binários/dirs vazios preservados e rollback integral de preparação incompleta.
- [ ] Escrever `test_duplicate_or_missing_timeline_reference_refused`, `test_snapshot_limits_checked_before_reconstruction`, `test_legacy_connection_cannot_append_operation_or_event`, `test_backup_and_repair_preserve_snapshot`. Conexão sqlite sem capability deve falhar ao inserir nas tabelas antigas/novas; backup restaurado deve produzir o mesmo snapshot. Cobrir manifesto com caminho absoluto/.., duplicatas, hash/tamanho incorretos e blobs ausentes sem publicação.

  Após preparar remoção pela API real e abrir o backup restaurado:

  ```python
  self.assertEqual(repo.snapshot(remove_id).contents, restored_repo.snapshot(remove_id).contents)
  self.assertEqual(repo.snapshot(remove_id).contents["scripts/run.bin"], b"\x00\xff")
  self.assertEqual(legacy_before, legacy_after)
  ```

  `legacy_before/after` são os IDs, conteúdos e eventos das operações reais criadas antes da migração, consultados pelas APIs legadas. Acrescentar `test_future_schema_refused` usando banco de versão superior à suportada.
- [ ] Rodar `uv run pytest -q tests/test_skill_removal_state.py`; exigir falha do contrato antes da implementação.
- [ ] Implementar schema v8 aditivo: `skill_tree_operations`, `skill_tree_events`, `skill_tree_snapshots`, `skill_tree_blobs`, `skill_mutation_timeline`. Operações/eventos/snapshots/blobs são append-only; snapshots possuem manifesto determinístico, blobs por SHA-256 e tamanho, incluindo diretórios. Timeline usa sequência global e duas referências exclusivas/únicas a eventos antigos ou novos, com FKs. Importar antigos por sequence; triggers acrescentam novos na mesma transação. Preparação grava provas, snapshot e evento juntos; restore não duplica/perde o snapshot.
- [ ] Implementar `register_skill_writer(connection: sqlite3.Connection) -> None` em `skill_writer.py`, registrando função SQL `kairos_skill_writer_version()` com valor 8. Triggers de INSERT nas famílias antigas/novas exigem capability; não reconstruir CHECK create|rollback antigo. Registrar em conexões escritoras novas antes da migração e nos repositórios suportados. Leitura não registra nem migra. Migrador novo recusa schema futuro; repair recria objetos derivados/triggers sem tocar dados canônicos. Adicionar as cinco tabelas a CANONICAL_TABLES e validar backup/fingerprint sem congelar contagens. Capability distingue versões de cliente; não substitui a segurança contra processos com acesso arbitrário ao SQLite.
- [ ] Rodar `uv run pytest -q tests/test_skill_removal_state.py tests/test_skill_mutation_state.py tests/test_state.py`; exigir PASS, preservação do ledger antigo e recusa real da conexão sem capability.
- [ ] Commit: `feat(skills): persiste snapshots e eventos de remoção reversível`.

### Task 2: Projeção e histórico comuns

**Files:** Modify `kairos_state/repositories/skill_mutations.py`, `kairos_skills/mutations.py`, `kairos_cli/skill_mutations.py`; Create `tests/test_skill_removal_projection.py`; regressão `tests/test_skill_mutation_rollback.py`, `tests/test_cli_skill_mutations.py`.

**Interfaces:** Fachada existente `SkillMutationRepository` conserva `prepare(draft, text)`, `content(create_id)` e IDs antigos. Acrescentar `current_installation(name: str) -> InstallationProof | None`, `current_removal(name: str) -> SkillTreeRecord | None`, `snapshot(remove_id: str) -> TreeCapture`, `prepare_remove(draft, snapshot)`, `prepare_restore(draft)`, `bundled_tombstones() -> frozenset[str]`. `get/finish/history/pending` passam a suportar `CommonRecord`; `latest_restore(remove_id: str) -> SkillTreeRecord | None`. Histórico ordena operações pelo primeiro evento na timeline, estados pelo último; mesma política de limite/nome existente. CREATE/RESTORE committed ativam; REMOVE/ROLLBACK committed desativam somente a instalação referida; aborted não modifica projeção; pending/conflict bloqueia escrita.

- [ ] Escrever `test_remove_deactivates_create_and_restore_activates_new_identity`, `test_current_removal_changes_without_losing_older_snapshots`, `test_history_orders_families_by_global_sequence`, `test_pending_other_family_blocks_all_writers`, `test_restore_preserves_recorded_origin`. Preparar registros pelo repositório real e exigir projeção/IDs/origens coerentes com a sequência.

  Depois de concluir remove e depois de concluir restore, respectivamente:

  ```python
  self.assertIsNone(repo.current_installation(name))
  self.assertEqual(repo.current_removal(name).operation_id, remove_id)
  # Concluir restore pela API real antes das duas assertivas seguintes.
  self.assertEqual(repo.current_installation(name).operation_id, restore_id)
  self.assertIsNone(repo.current_removal(name))
  ```

- [ ] Escrever `test_old_rollback_cannot_remove_restored_or_reinstalled_tree`, `test_add_after_remove_uses_new_id`, `test_legacy_metadata_unchanged`. Repetição de rollback histórico retorna evidência sem tocar versões posteriores; rollback novo de create fora da projeção corrente recusa. Remove ainda corrente mantém tombstone mesmo após novo add até restore committed; não confundir origem com presença.
- [ ] Rodar `uv run pytest -q tests/test_skill_removal_projection.py`; exigir falha de comportamento antes da implementação.
- [ ] Implementar fachada sobre ambas as famílias, validação de integridade da timeline e bloqueio comum de pendências. Substituir `_current_create` como autoridade da instalação por `current_installation`; manter o método interno compatível quando necessário ao código antigo. Add pode usar nome desocupado após remove, respeitando reservas bundled. Rollback antigo exige ID corrente e provas escalares antigas; não interpretar ações desconhecidas como rollback.
- [ ] Atualizar `mutation_metadata(record: CommonRecord)` mantendo metadata antiga intacta e expondo novas ações sem converter snapshot binário em texto. Histórico/read-only não cria banco ou migra; ledger moderno parcial/incompatível gera erro sanitizado.
- [ ] Rodar `uv run pytest -q tests/test_skill_removal_projection.py tests/test_skill_removal_state.py tests/test_skill_mutation_rollback.py tests/test_cli_skill_mutations.py`; exigir PASS.
- [ ] Commit: `feat(skills): coordena instalação e histórico entre os journals`.

### Task 3: Retirada, restauração e recuperação

**Files:** Create `kairos_skills/removal_io.py`, `kairos_skills/removal_origin.py`, `kairos_skills/removal_recovery.py`, `tests/test_skill_removal_io.py`, `tests/test_skill_removal_service.py`, `tests/test_skill_removal_recovery.py`; Modify `kairos_skills/mutations.py`, `kairos_skills/mutation_recovery.py`.

**Interfaces:** Produzir context manager `SkillRemovalFiles(home: Path)` com `capture_installed(name: str) -> TreeCapture | None`, `capture_private(id: str, *, retired: bool) -> TreeCapture | None`, `retire(name: str, id: str) -> None`, `return_retired(id: str, name: str) -> None`, `stage(id: str, snapshot: TreeCapture) -> TreeCapture`, `publish(id: str, name: str) -> None`, `sync_parents() -> None`. Usar raízes privadas existentes de staging/retired por ID; não sobrescrever semântica escalar de SkillMutationFiles. Produzir `prove_removal_origin(home: Path, name: str, current: InstallationProof | None, captured: TreeCapture) -> Provenance`. Produzir `reconcile_tree_operation(repository: SkillMutationRepository, files: SkillRemovalFiles, record: SkillTreeRecord) -> SkillTreeRecord`. Serviço: `remove(name: str, *, actor: Actor, confirmed: bool = False) -> SkillTreeRecord`; `rollback(id: str, *, actor: Actor) -> CommonRecord`, despachando create antigo/remove concluído; IDs restore recusados.

- [ ] Escrever testes de I/O: `test_tree_roundtrip_has_new_identity`, `test_snapshot_is_separate_from_retired_tree`, `test_publish_collision_preserves_both`, `test_private_orphans_untouched`. Assertivas com binários, modos e diretórios vazios reais; publicação no nome ocupado nunca muda a árvore existente.
- [ ] Escrever serviços `test_remove_restore_preserves_legacy_and_assets`, `test_edited_manual_directory_can_be_removed`, `test_identical_replaced_directory_refused`, `test_bundled_proof_requires_full_tree_and_v2`, `test_unknown_origin_refused`, `test_repeat_remove_requires_current_durable_proof`, `test_stale_remove_cannot_restore_over_newer_remove`, `test_repeat_restore_does_not_touch_new_installation`. Bundle confiável é derivado do pacote instalado, sem parâmetro CLI/config; testes substituem a resolução do bundle por fixture controlada, sem publicar bypass de produção. Validar manifesto estrito, nomes únicos e inventário SHA-256 completo; MD5 não prova origem.

  Depois de criar uma instalação manual comprovada e incluir o asset binário:

  ```python
  removed = service.remove(name, actor=Actor.USER_FOREGROUND, confirmed=True)
  self.assertFalse((home / "skills" / name).exists())
  restored = service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
  self.assertEqual(restored.reverts, removed.operation_id)
  self.assertEqual((home / "skills" / name / "scripts/run.bin").read_bytes(), b"\x00\xff")
  ```

- [ ] Escrever `test_open_descriptor_edit_after_retire_preserves_conflict` e subprocessos mortos após prepared, após rename, antes/depois committed para remove e restore. Reconciliação deve cumprir todas as linhas da tabela da spec; ambas presentes/ausentes ou provas divergentes conservam evidências. Testar fsync/rename com falhas reais na fronteira, sem mock do serviço inteiro.
- [ ] Rodar `uv run pytest -q tests/test_skill_removal_io.py tests/test_skill_removal_service.py tests/test_skill_removal_recovery.py`; exigir falhas do contrato antes da implementação.
- [ ] Implementar adaptador de árvore com limites exatos e SKILL.md regular, sem validar semver; tratar captura imutável separadamente das identidades da cópia original. Prova manual usa projeção e identidade da raiz/pais; arquivos editados no mesmo diretório são permitidos. Capturar/modos/bytes atuais, preparar duravelmente antes de retire, validar depois e concluir. Divergência tenta devolver somente ao nome ausente; conflito preserva versões/ID.
- [ ] Implementar rollback de remove corrente: validar snapshot integral, criar cópia privada nova, fsync, registrar prepare_restore com identidades novas, publicar sem substituir, validar e concluir. Snapshot permanece no SQLite; tombstone termina só em committed. Repetição de restore concluído é leitura de evidência. Reconciliação comum despacha explicitamente CREATE/ROLLBACK/REMOVE/RESTORE sob um lock, usando somente IDs preparados conhecidos; nunca adota/remove órfãos. Garantir ausência de lock aninhado ao delegar rollback para restore.
- [ ] Rodar `uv run pytest -q tests/test_skill_removal_io.py tests/test_skill_removal_service.py tests/test_skill_removal_recovery.py tests/test_skill_mutation_service.py tests/test_skill_mutation_rollback.py tests/test_skill_mutation_recovery.py`; exigir PASS inclusive subprocessos e preservação de todos os conflitos.
- [ ] Commit: `feat(skills): remove e restaura árvores com recuperação durável`.

### Task 4: CLI, tombstones de sync e catálogo

**Files:** Modify `kairos_cli/main.py`, `kairos_cli/handlers.py`, `kairos_cli/skill_mutations.py`, `kairos_skills/sync.py`, `docs/decisoes.md`; Create `kairos_skills/removal_state.py`, `tests/test_cli_skill_removal.py`, `tests/test_skill_removal_sync.py`; regressão `tests/test_cli_skills.py`, `tests/test_cli_skill_catalog.py`, `tests/test_skill_mutation_sync.py`, `tests/test_interaction_skill_catalog.py`.

**Interfaces:** Produzir `read_skill_tombstones(home: Path) -> frozenset[str]` em removal_state, sem criar/migrar banco; usa abertura segura por descritor extraída da composição CLI para este módulo e compartilhada com CLI, mantendo adaptador `_database_connection` existente. Consulta executada sob skill_mutation_lock pelo sync; validar ambas as famílias/projeção. Home/banco antigo sem ledger de remoção pode retornar vazio; schema moderno incompleto ou versão incompatível recusa. `sync_bundled_skills(bundled_dir, user_dir)` conserva assinatura pública. A lista de tombstones entra no caminho `_sync_items` antes de qualquer cópia.

- [ ] Escrever `test_cli_remove_confirmation_has_no_effect`, `test_cli_remove_restore_json_and_history`, `test_cli_exit_codes_and_redaction`, `test_unknown_restore_id_refused`. Executar `kairos_cli.main.main` com home temporário; código 77 sem criar banco/lock/diretórios; sucesso exige remoção efetiva, ID no histórico e `removido/restauravel`; rollback restaura bytes e informa seu ID e reverts.
- [ ] Escrever `test_tombstone_survives_bundle_disappearance_and_reappearance`, `test_sync_does_not_seed_during_pending_restore`, `test_corrupt_or_incompatible_ledger_refuses_seeding`, `test_old_home_read_does_not_create_or_migrate_database`, `test_sync_and_remove_share_lock`. Manifesto pode perder nome durante cleanup mas tombstone permanece; restore committed permite política normal de sync sem sobrescrever edições. Pendências/conflitos comuns bloqueiam sync; não basta ler tombstone se outra operação preparada pudesse ser desfeita pela cópia.

  Após remover e exercitar sync com o bundle sem a skill e depois com ela novamente:

  ```python
  self.assertIn(name, read_skill_tombstones(home))
  self.assertFalse((home / "skills" / name).exists())
  self.assertEqual(repo.snapshot(remove_id).contents, snapshot_before.contents)
  ```

- [ ] Escrever `test_catalog_reflects_remove_restore_without_changing_started_turn` usando catálogo/snapshot reais: novo catálogo reflete efeito e snapshot de turno existente conserva bytes/assets. Nenhum reset de catálogo ou contagem congelada.
- [ ] Rodar `uv run pytest -q tests/test_cli_skill_removal.py tests/test_skill_removal_sync.py`; exigir falhas de comportamento antes da implementação.
- [ ] Implementar parser `skills remove NOME --yes`, ajuda rollback para IDs de criação/remoção, dispatch antes de compor escrita e confirmação/ator validados antes de I/O. Registrar capability em conexão escritora segura; preservar leitura histórica sem migração. JSON remove tem `nome`, `operation_id`, `action`, `state`, `removido`, `restauravel`; só committed informa removido true. Erros com IDs conhecidos, stderr sem envelope de sucesso nem conteúdo.
- [ ] Implementar leitura segura do ledger e checagem de pendências sob lock antes da cópia de sync, inclusive quando entrada saiu do manifesto. Tombstones não alteram `.no-bundled-skills` e só terminam no restore committed. Documentar em decisões/ajuda a recusa de origem desconhecida, bundled editada sem prova e hub sem identificação anterior; não afirmar conclusão do guard organizacional geral.
- [ ] Rodar `uv run pytest -q tests/test_cli_skill_removal.py tests/test_skill_removal_sync.py tests/test_cli_skill_mutations.py tests/test_cli_skills.py tests/test_cli_skill_catalog.py tests/test_skill_mutation_sync.py tests/test_interaction_skill_catalog.py`; exigir PASS.
- [ ] Commit: `feat(cli): integra remoção reversível e exclusões persistentes no sync`.

## Gate final e entrega

- [ ] Coordenador executa self-review de cobertura: limites, origem, compatibilidade, projeção, todas as linhas de recuperação, CLI e tombstones têm testes de comportamento. Revisor independente examina diff completo e evidências; corrigir bloqueios antes do gate.
- [ ] `git diff --check`, `scripts/ci.sh` completo com suite inteira, opt-ins sem credenciais, imagem real/stub e frontends; confirmar Codex pinado exigido pela imagem/CI. Não reduzir suite ou alterar lint para ocultar falhas. Registrar testes reais de bind mount e mortes; indisponibilidade não vira sucesso.
- [ ] Criar PR em português e aguardar todos os jobs de verificação verdes. Conferir backup consistente da produção com restauração de banco/BLOBs/volume e hashes, saúde e operações pendentes. Sem merge até ambos os gates verdes.
- [ ] Squash merge, conferir `HEAD~1..HEAD` para remoções acidentais e acompanhar todos os jobs da main, inclusive Komodo deploy.
- [ ] Smoke na imagem instalada com home temporário: add → editar/assets → remove → history → rollback → comparar bytes/modos; tombstone após bundle sair/voltar; recusa sem confirmação e no nome ocupado. Conferir health HTTP, hashes/fontes instaladas e persistência após reinício. Não remover skills de produção no smoke. Registrar evidências locais de CI/deploy/smoke e reportar limitações reais.
