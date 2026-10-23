# B2 — plano de implementação da publicação coordenada de skills

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para a execução nativa proposta, tarefa por tarefa. superpowers:subagent-driven-development exige escolha posterior compatível com o gate de orquestração. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Atualizar skills por sync e promoção sem perder versões, com prova da publicação, resultado parcial explícito e recuperação observacional após interrupções.

**Architecture:** Um coordenador por item compartilha o lock do home, prepara staging privado e journal antes dos movimentos, conserva a versão anterior e conclui a instalação numa transação SQLite. Manifesto e limpeza têm confirmações posteriores; pendências bloqueiam todos os escritores do nome. Aquisição, publicação e adoção continuam sendo provas distintas.

**Tech Stack:** Python 3.11, SQLite, unittest/pytest existentes, descritores POSIX e `renameat2(RENAME_NOREPLACE)`, Docker; nenhuma dependência nova.

**Spec:** [desenho B2 aprovado em 22/10/2026](../specs/2026-10-21-publicacao-skills-b2-design.md), conteúdo de `7fd4a126a0ac09a897a4a9cccd171a14d8e059cc`.

**Estado:** execução nativa autorizada pela usuária em 22/10/2026, após revisão de `02fb85e`. Base de execução: `main` em `44d890ca632b40b78a21b62c5e05622200a5b23c`. A correção do PR #111 foi integrada em `74fba82`; o PR #113 integrou a continuidade documental. Não reaplicar essas entregas. T1 está em `ff23200`; T2 está em `1103f0c`; T3–T6 foram validadas localmente em 23/10 na branch `feat/publicacao-skills-b2`. T7 foi validada pelo CI local completo; a revisão independente foi concluída e seus seis achados foram corrigidos, com validação integrada local pós-revisão aprovada. O schema padrão agora é v11 somente na branch; execução não autoriza merge/deploy de B2.

**Execução:** nativa, sequencial, pelo Root, com revisão independente ao final. As sete tarefas compartilham contratos, timeline e guards; não se justifica distribuir implementações concorrentes. A skill de orquestração governa qualquer exceção posterior.

## Global Constraints

- “Não prometer atomicidade entre filesystem e SQLite”; não há atomicidade de lote.
- “Publicação nova tem identidade instalada nova”; não herda adoção USER pelo nome, conteúdo ou inode reaproveitado.
- “B2 não cria exceção ao veto organizacional, `force` ou permissão administrativa nova.”
- “Quarentenas legadas sem recibo são recusadas sem efeitos.” Não certificar pastas arbitrárias.
- “Leitores reconhecem homes anteriores sem migrar nem gravar.” Migração pertence à composição dos escritores, após observar a política.
- “Não há transação SQLite aberta durante cópia, scan ou movimento de árvore.”
- “Os registros canônicos A1/A2/B1 são imutáveis.” Não renumerar nem reescrever eventos antigos.
- “COMMITTED com projeção ou limpeza pendente continua bloqueando escritores do nome.”
- “Um restore somente do banco nunca é recuperação integral de publicação.”
- Sem download, busca/update de hub, OAuth, `/learn`, curadoria, configuração de política em produção ou proteção contra adulteração pelo mesmo UID. #109 permanece recorte próprio.
- Reutilizar os limites de árvores de skills de `removal_contract.py`: 64 MiB/árvore, 16 MiB/arquivo, 64 KiB para SKILL.md, 4096 entradas, profundidade 32, componente de 255 bytes. Não usar os limites genéricos mais largos de `publication_io.py` para contornar esse contrato.
- Testar comportamento por APIs, arquivos, SQLite e subprocessos reais; nenhum teste lê `.py` nem congela catálogos/contagens. Falhas esperadas têm categoria e ID, com logging estruturado sanitizado; falha inesperada propaga.

## Review Focus

1. Capability antiga ainda escreve numa família esquecida após migração/reparo — T1/T7 verificam clientes e todas as famílias participantes.
2. Recibo com bytes iguais mas raiz trocada, scan obsoleto ou emissor arbitrário concede autoridade — T2/T3 exercitam equivalência e identidades separadamente.
3. Confirmação de manifesto/limpeza reaplica a instalação ou ressuscita adoção — T1/T3/T5 exercitam a ordem dos eventos e idempotência.
4. Morte depois do rename/fsync, inclusive antes de gravar a confirmação, aparenta sucesso ou apaga uma versão — T4/T7 cobrem cada fronteira real.
5. Backup restaura bytes corretos em novos inodes e transfere autoridade, ou ignora staging ausente — T6/T7 exigem diagnóstico e bloqueio conservadores.

## Mapa de arquivos e dependências

| Tarefa | Responsabilidade principal | Dependências |
|---|---|---|
| T1 | Contratos, schema v11, recibos e journal | main `44d890c` |
| T2 | Fontes observadas, emissão do recibo e I/O privado | T1 |
| T3 | Timeline comum, origem e bloqueio de escritores | T1 |
| T4 | Coordenador e recuperação por ID | T1–T3 |
| T5 | Sync, promoção, manifesto e saídas reais | T4 |
| T6 | Backup, reparo e diagnóstico de restauração | T3–T5 |
| T7 | SIGKILL, clientes antigos, imagem e entrega | T1–T6 |

Arquivos novos de produto: `kairos_skills/publication_contract.py` (DTOs e validação), `publication_sources.py` (observações confiáveis), `publication_session.py` (composição e lifetime do lock/banco), `acquisitions.py` (quarentena com recibo), `publication_files.py` (I/O), `publications.py` (coordenação), `publication_recovery.py` (retomada), `publication_backup.py` (inventário de backup/restore), `kairos_state/skill_publications_schema.py` (DDL/validação) e `kairos_state/repositories/skill_publications.py` (journal).

Modificar arquivos existentes somente nas tarefas indicadas abaixo. Os nomes curtos de módulos de skills pertencem a `kairos_skills/`; `skill_writer.py`, `skill_adoptions_schema.py` e `skill_org_schema.py` pertencem a `kairos_state/`; os caminhos `repositories/` pertencem a `kairos_state/repositories/`. Não refatorar genericamente os serviços A1/A2/B1. `tests/skill_publication_fixtures.py` mantém fixtures de arquivos/banco reais e construtores de DTOs; não substitui serviço, guard ou operação de daemon.

### Task 1: contratos e persistência aditiva v11

**Files:** criar `publication_contract.py`, `skill_publications_schema.py`, `repositories/skill_publications.py`, `tests/skill_publication_fixtures.py`, `tests/test_skill_publication_state.py`; modificar `kairos_state/migrations.py`, `skill_writer.py`, `skill_adoptions_schema.py`, `skill_org_schema.py`. Arquivos sem prefixo seguem o mapa acima.

**Interfaces — produz:**

- `PublicationWriter`: `bundled_sync` e `quarantine_promote`; `SkillPublicationAction.PUBLISH = "publish"`.
- `AcquisitionReceipt`: `format_version: int` (formato inicial 1), `acquisition_id/home_id/name/issuer/source_key/source_category/inventory_sha256/scan_sha256: str`, `caller_actor: Actor`, `source_proof: TreeCapture`, `quarantine_identity: tuple[int, int, int]` (device/inode/mount da raiz de quarentena). IDs de operação/aquisição e source_key de quarentena são UUID v4; home_id e hashes são SHA-256. Inventário inclui caminhos/modos/tamanhos/hashes; identidade device/inode/mount permanece prova separada, sem conteúdo no JSON. A identidade adicional da quarentena foi explicitada durante T2: a prova da árvore filha, sozinha, não recusa seu movimento para outra raiz com o mesmo source_key. Emissor local fixo `kairos_quarantine_v1`, categoria `unverified`; não aceitar esses valores do bundle. Scan é vinculado ao inventário por hash do JSON canônico `{inventory_sha256, findings}` e só pode autorizar recibo sem findings. `publication_inventory_hash(capture: TreeCapture) -> str` calcula o digest de conteúdo/modos sem device/inode/mount, para permitir comparação após cópia sem confundi-la com identidade.
- `ManifestDelta(name: str, before: str | None, after: str | None)`; `None` significa entrada ausente. Hash MD5 legado do manifesto é apenas compatibilidade/eligibilidade; nunca prova de autoridade. SHA-256 do inventário é independente.
- `PublicationDraft`: `operation_id/home_id/name/source_ref/source_key: str`, `writer: PublicationWriter`, `actor: Actor | None` (None para bundled, USER_FOREGROUND para promoção), `source_proof/staged_tree: TreeCapture`, `previous_installation: InstallationProof | None`, `previous_tree: TreeCapture | None`, `policy_receipt: OrgPolicyReceipt`, `manifest_delta: ManifestDelta | None`, `created_at: float`. Provas sem `contents`; refs privadas derivadas exclusivamente do ID. `source_ref` é lógico: nome para bundled ou `<acquisition_id>/<name>` para quarentena, nunca caminho absoluto ou URL; a sessão resolve a localização privada. Árvore legada pode ter previous_tree sem previous_installation; isso não lhe inventa autoria. Actor é derivado do contexto e não concede proveniência USER à instalação nova.
- `SkillPublicationRecord`: `draft: PublicationDraft`, `state: SkillMutationState`, `manifest_complete/cleanup_complete: bool`, `projection_delta: ManifestDelta | None` (delta original ou última manutenção preparada); propriedades comuns `operation_id`, `name`, `action`, `home_id`, `actor` e `provenance: Provenance | None` (BUNDLED somente quando provado). `pending` inclui PREPARED/CONFLICT e COMMITTED com confirmação aplicável ausente.
- `PublicationResult`: `operation_id`, `name`, `state`, `complete: bool`, `error_kind: str | None`, `manifest_complete`, `cleanup_complete`. `complete` exige COMMITTED e as duas confirmações concluídas/não aplicáveis.
- `SkillPublicationRepository(connection, *, home_id: str)`: `issue(receipt, *, guard) -> AcquisitionReceipt`, `receipt(acquisition_id) -> AcquisitionReceipt | None`, `prepare(draft, *, guard) -> SkillPublicationRecord`, `get(operation_id) -> SkillPublicationRecord | None`, `pending(name) -> tuple[SkillPublicationRecord, ...]`, `finish(operation_id, state: SkillMutationState, *, guard) -> SkillPublicationRecord`, `prepare_manifest(operation_id, delta: ManifestDelta, *, guard)`, `confirm_manifest(operation_id, *, guard)` e `confirm_cleanup(operation_id, *, guard)` retornando o record atualizado. Todo guard é `Callable[[], None]` obrigatório, chamado dentro da transação imediatamente antes do commit; falha desfaz a escrita e conserva sua categoria, sem convertê-la em corrupção. Nenhum método de repositório adquire lock de filesystem.

- [ ] **RED:** criar testes `test_upgrade_preserves_canonical_prefix`, `test_phase_confirmation_does_not_create_installation`, `test_old_writer_cannot_mutate_any_family_after_repair` e `test_partial_schema_refuses_read_write_and_repair`. Asserções centrais:

  ```python
  before = canonical_rows(connection, old_tables)
  migrate(connection, target=11)
  self.assertEqual(canonical_rows(connection, old_tables), before)
  self.assertEqual(publications.get(op).operation_id, op)
  self.assertEqual(len(installation_commits(connection, op)), 1)
  ```

  `canonical_rows` consulta tabelas capturadas antes da migração; `installation_commits` consulta eventos reais. Construir operações pelas APIs, sem ler fonte SQL em testes.
- [ ] **Verificar RED:** `uv run pytest -q tests/test_skill_publication_state.py`; falha na capacidade nova ausente. Não aceitar erro de fixture/import alheio ao contrato como evidência suficiente; durante implementação, separar testes de contrato e migração conforme os símbolos surgirem.
- [ ] **Implementar:** `migrate_publications(connection) -> None`, `validate_publication_schema(connection) -> None`, `repair_publication_objects(connection) -> None`. Novas tabelas append-only: `skill_acquisition_receipts`, `skill_publication_operations`, `skill_publication_events`, `skill_publication_migration` e `skill_publication_timeline`. Esta última copia o prefixo de `skill_authority_timeline` preservando sequências e acrescenta `publication_event`; congela a timeline anterior. Todos os eventos posteriores das quatro famílias entram na nova timeline, com exatamente uma referência por linha.
- [ ] **Implementar eventos:** `prepared/committed/aborted/conflict/manifest_prepared/manifest_applied/source_cleaned`. Confirmações têm registros próprios, são idempotentes, só seguem COMMITTED e não representam nova instalação. `manifest_prepared` prepara uma manutenção posterior do delta de uma publicação já concluída, conservando sua instalação e tornando apenas a projeção pendente. Ações N/A são fixadas no draft, não inventadas durante retomada. Recibo de aquisição não gera evento de instalação.
- [ ] **Implementar compatibilidade:** registrar capability 11 apenas após banco compatível; triggers exigem 11 em todas as tabelas mutantes v11, inclusive antigas e organizacionais. Atualizar validadores/reparo por versão, preservando comportamento v8/v9/v10 e prefixos grandfathered. Recibo de política acompanha PREPARED e o commit de publicação; triggers de B1 reconhecem o novo pai sem permitir publicação protegida. Acrescentar as tabelas à proteção de canônicas no reparo, sem reparar/remover suas linhas.
- [ ] **Testar fronteira transacional:** `test_policy_change_before_commit_rolls_back_confirmation` troca a fonte de política antes do callback real e exige erro de política, evento ausente e estado ainda pendente. O callback valida política, identidade/cadeia dos descritores e observação do banco; cópia, captura integral de conteúdo e scan ficam antes de BEGIN. A política não ganha cache global nem deixa de ser revalidada nas fronteiras.
- [ ] **Manter ativação controlada:** T1 acrescenta migração explicitamente alcançável por `migrate(connection, target=11)`, mas mantém o schema padrão v10 até T7. Fixtures B2 migram explicitamente; fixtures legadas preservam seu contrato. Só ativar o padrão v11 quando todos os leitores, escritores, backup e recuperação estiverem integrados. Não liberar/mesclar tarefas intermediárias como B2 pronta.
- [ ] **GREEN:** repetir o comando e rodar `uv run pytest -q tests/test_skill_org_state.py tests/test_skill_adoption_state.py tests/test_state.py`; todos os testes selecionados passam. A migração explícita v10→v11 é o contrato; não criar teste congelando o valor global de `SCHEMA_VERSION`.
- [ ] **Commit:** `feat(skills): registra aquisições e publicações no journal v11`.

### Task 2: contexto confiável, aquisição e árvores privadas

**Files:** criar `publication_sources.py`, `publication_session.py`, `acquisitions.py`, `publication_files.py`, `tests/test_skill_publication_sources.py`, `tests/test_skill_publication_files.py`; modificar `hub.py` para reusar a validação de bundle e recusar colisões da API legada, sem promover; consumir helpers de `kairos_filesystem` sem afrouxar limites.

**Interfaces — consome:** DTOs/repositório T1, `OrgPolicySession`, `TreeCapture`, `SkillBundle` e descritores existentes.

**Interfaces — produz:**

- `PublicationContext` é uma observação viva: `home_id: str`, `writer: PublicationWriter`, `source_key: str`, `root: Path` privado, `root_identity: tuple[int, int, int]` (device/inode/mount), `actor: Actor | None`, `replacement: TreeCapture | None`; `revalidate() -> None`, `resolve(name: str, *, acquisition_id: str | None = None) -> tuple[Path, TreeCapture]` e `contains(name: str) -> bool`. Bundled recusa acquisition_id; quarentena o exige e deriva `<id>/<name>`, reconferindo o recibo no serviço. Somente os context managers abaixo produzem instâncias válidas; nenhuma factory aceita metadados de autoridade desserializados.
- `observe_bundled_source(home: Path, *, policy) -> ContextManager[PublicationContext]`: a raiz vem de `trusted_bundle_root()`, nunca do argumento de uma ferramenta. Source_key estável `package_bundle`; a prova de raiz/inventário continua obrigatória em cada sessão, inclusive retomada após reinício.
- `observe_quarantine_source(home: Path, quarantine_root: Path, *, source_key: str, actor: Actor, policy, replacement: TreeCapture | None = None) -> ContextManager[PublicationContext]`: exige USER_FOREGROUND para intenção de promoção; `replacement` fixa a árvore autorizada a substituir, sem `force`. Recibo, categoria ou nome não substituem essa intenção. O contexto vivo mantém descritores, home, escritor e prova da fonte; não é um DTO desserializável do modelo.
- `publication_session(home: Path, *, policy, context: PublicationContext) -> ContextManager[PublicationSession]`: valida fonte/política, adquire o lock uma vez, abre banco por descritor e migra explicitamente para v11 na composição. A sessão expõe `home`, `connection`, `publications: SkillPublicationRepository`, `files: PublicationFiles`, `policy`, `context`, `revalidate() -> None` e lifetime atestado. Não aceitar booleano `already_locked` ou token fornecido pelo modelo. Após saída, qualquer efeito recusa.
- `SkillAcquisitionService(session: PublicationSession).acquire(bundle: SkillBundle) -> AcquisitionReceipt`: cria entrada exclusiva `<quarantine_root>/<acquisition_id>/<name>`, escaneia bytes atestados, sincroniza árvore/pais, depois emite recibo T1. Não executa download nem certifica pasta já existente. Aquisição não usa `context.resolve(name)` antes de criar a entrada: a raiz atestada é a de quarentena; a prova do filho nasce da criação e é revalidada na promoção.
- `PublicationFiles(home: Path)` como context manager: `stage(operation_id: str, source: Path, expected: TreeCapture, *, guard: Callable[[], None]) -> TreeCapture`; `capture_installed(name: str)`, `capture_staged(operation_id: str)` e `capture_previous(operation_id: str)` retornam `TreeCapture | None`. Efeitos `retire(name: str, operation_id: str, expected: TreeCapture, *, guard: Callable[[], None]) -> None`, `publish(operation_id: str, name: str, expected: TreeCapture, *, guard: Callable[[], None]) -> None`, `return_previous(operation_id: str, name: str, expected: TreeCapture, *, guard: Callable[[], None]) -> None` e `sync_parents(*, guard: Callable[[], None]) -> None` exigem guard por keyword. `check() -> None` revalida o lifetime; capturas não concedem autoridade.

- [x] **RED:** testes `test_receipt_requires_real_acquisition`, `test_identical_bytes_replaced_root_refuses`, `test_stale_scan_refuses`, `test_arbitrary_bundle_root_has_no_package_authority`, `test_stage_from_other_filesystem_preserves_source`. Verificar recibo consultado no banco, raiz/inventário e scan vinculados; recusa conserva inode/bytes e histórico; staging entre filesystems copia e mantém a fonte. Não testar EXDEV apenas lançando uma exceção simulada.

  ```python
  self.assertNotEqual(staged.entries[0].device, source.entries[0].device)
  self.assertEqual(publication_inventory_hash(staged), receipt.inventory_sha256)
  self.assertEqual(capture_source().entries, source.entries)
  self.assertEqual(publications.receipt(receipt.acquisition_id), receipt)
  ```
- [x] **RED — colisão na quarentena:** forçar colisão no ID de aquisição com árvore e recibo já existentes. Conferir identidade, bytes e recibo anteriores preservados, sem exclusão nem emissão de autoridade sobre a entrada ocupada; uma nova aquisição só pode usar outra entrada exclusiva. Este caso cobre a exclusão sem atestação apontada na revisão do PR #111.
- [x] **RED — API legada de aquisição:** `test_legacy_quarantine_collision_preserves_existing_acquisition` chama `quarantine_bundle` sobre entrada ocupada, inclusive um diretório de aquisição com recibo. Exigir recusa explícita, mesmas identidades/bytes e recibo inalterado. Uma gravação legada nova não emite recibo nem passa a ser promovível por B2.
- [x] **Verificar RED:** `uv run pytest -q tests/test_skill_publication_sources.py tests/test_skill_publication_files.py`; falhas pelos contratos ausentes, depois por comportamento não implementado.
- [x] **Implementar:** staging e versão anterior em `.skill-publications/<operation_id>/{staged,previous}`, diretórios 0700 no filesystem de `skills/`; ID validado antes da composição de referências. Reusar abertura NOFOLLOW, cadeia/device/inode/mount, escrita exclusiva, fsync e rename sem substituição. Recusar symlinks, hardlinks, especiais, atributos não preserváveis e excesso de limites. Fonte externa é copiada por descritores; nunca usar `shutil.move` entre filesystems.
- [x] **Implementar emissão/verificação:** origem não autenticada continua desconhecida, mesmo com recibo válido. Recusar recibo de outra raiz/home, emissor não permitido, bytes alterados ou ID fornecido sem registro. Na falha antes do journal, preservar resíduos desconhecidos; limpar apenas entrada criada e ainda comprovada com política corrente. Retirar a exclusão prévia de `quarantine_bundle`: destino ocupado recusa sem efeitos; aquisição B2 cria entrada exclusiva e nunca usa a API legada para substituir conteúdo.
- [x] **GREEN:** repetir os dois arquivos, com capturas reais antes/depois; executar caso cross-filesystem em tmpfs Docker no aceite T7 quando não houver dois devices locais, registrando o skip local explicitamente.
- [x] **Commit:** `feat(skills): atesta aquisição e prepara árvores de publicação`.

### Task 3: projeção de instalação e bloqueio comum de pendências

**Files:** modificar `repositories/skill_mutations.py`, `repositories/skill_adoptions.py`, `repositories/skill_removals.py`, `removal_contract.py`, `identification_contract.py`, `identification.py`, `removal_origin.py`, `removal_state.py`, `org_policy_guard.py`, `mutations.py`, `adoptions.py`, `mutation_recovery.py`, `kairos_cli/skill_mutations.py`; criar `tests/test_skill_publication_authority.py` e `tests/test_skill_publication_pending.py`.

**Interfaces — consome:** T1. **Produz:** `CommonRecord` inclui `SkillPublicationRecord`; `get/history/pending/installation_evidence/current_installation` reconhecem a quarta família. `pending(name)` inclui confirmações incompletas. `repository_guard` consulta o mesmo estado antes dos efeitos de todas as famílias.

- [x] **RED:** `test_publication_clears_old_adoption`, `test_unproven_acquisition_never_becomes_user`, `test_confirmation_does_not_reapply_publication`, `test_every_writer_refuses_committed_cleanup_pending`, `test_legacy_reader_has_no_home_effects`. Exemplos de invariantes:

  ```python
  self.assertNotEqual(after.current.operation_id, before.current.operation_id)
  self.assertIsNone(after.adoption)
  self.assertIn(op, after.pending_operation_ids)
  self.assertEqual(bytes_and_identities(home), observed_before_refusal)
  ```

  Parametrizar add/remove/rollback-restore/adopt/unadopt/sync/promote e recuperação antiga; verificar o ID bloqueador e zero efeitos na recusa.
- [x] **Testar sessões existentes:** `test_session_catalog_remains_immutable_after_publication` cria catálogo/assets reais, publica outra versão e exige os mesmos bytes/hashes no snapshot da sessão anterior; somente uma nova sessão pode observar o catálogo atualizado. Não alterar tabelas de catálogo para fazer a nova instalação parecer antiga.
- [x] **Verificar RED:** `uv run pytest -q tests/test_skill_publication_authority.py tests/test_skill_publication_pending.py`.
- [x] **Implementar:** ampliar `InstallationProof.provenance` para `Provenance | None`, aceitando ausência somente na projeção de publicação comprovada; adoção exige USER como antes. Bundled confiável produz BUNDLED para a árvore nova. Aquisição produz prova de instalação com origem de aquisição verificável e proveniência/autorização destrutiva ausentes; não criar enum USER/SEDIMENT por conveniência. Nome, byte-idêntico e adoção anterior não autorizam a árvore nova.
- [x] **Implementar leitores:** `installation_source="verified_acquisition"` somente com recibo válido; sem provar o fornecedor remoto. Para publicação com proveniência ausente, `ownership="unknown"`, `authorization_basis="none"`, `removivel=False`. Preservar a convenção legada de exibição fora desse caso. Em `SkillMutationService.ownership`, recusar publicação sem autoridade em vez de usar `or Provenance.USER`; `resolve_removal_origin` não cai no manifesto para contornar uma publicação desconhecida.
- [x] **Implementar timeline:** aplicar cada commit de instalação uma única vez, na ordem canônica; confirmações não reinstalam nem recuperam adoção. Bloqueio de pendência precisa atingir também early returns/idempotência de operações antigas; recuperação A1/A2/B1 não despacha um record B2 a um adaptador legado. History/JSON mostram tipo, estado e pendência; rollback de ID de publicação recusa explicitamente, sem tentar restaurar como CREATE/RESTORE.
- [x] **Implementar metadados:** em `mutation_metadata`, tratar `SkillPublicationRecord` antes do ramo que usa `record.provenance.value` e `record.sha256`. Emitir origin nulo para origem não provada, writer, ID, estado, confirmação de manifesto/limpeza e pending; não inventar `reverts`, criação manual ou actor para bundled. `test_history_serializes_unknown_publication_without_user_fallback` percorre a CLI real.
- [x] **GREEN:** repetir os dois arquivos e `uv run pytest -q tests/test_skill_identification.py tests/test_skill_adoption_sync.py tests/test_skill_org_services.py`; preservar resultados A1/A2/B1 e leitura v8–v10 sem migração/sidecars no home original.
- [x] **Commit:** `feat(skills): integra publicações à autoridade e aos bloqueios comuns`.

### Task 4: coordenador e recuperação observacional

**Files:** criar `publications.py`, `publication_recovery.py`, `tests/test_skill_publication_service.py`, `tests/test_skill_publication_recovery.py`; integrar guard T3 e I/O T2 sem duplicar seu lock.

**Interfaces — consome:** T1–T3. **Produz:** `SkillPublicationService(session: PublicationSession)` com `publish(name: str, *, acquisition_id: str | None = None, manifest_delta: ManifestDelta | None = None) -> PublicationResult` e `recover(operation_id: str) -> PublicationResult`. Usa a conexão/arquivos/contexto da sessão viva T2, compondo `SkillMutationRepository` na mesma conexão; não abre outro banco nem adquire outro lock.

- [ ] **RED:** `test_prepared_precedes_any_installed_move`, `test_failed_publish_preserves_previous`, `test_post_rename_policy_refusal_remains_pending`, `test_unknown_private_tree_is_never_cleaned`, `test_recovery_table_and_repeat`. Conferir que sucesso implica árvore nova atestada, registro COMMITTED único e confirmações completas; todo erro após PREPARED conserva ID/pendência e versões, sem mensagem de sucesso.

  ```python
  self.assertFalse(result.complete)
  self.assertIn(result.operation_id, {r.operation_id for r in publications.pending(name)})
  self.assertEqual(capture_previous().entries, original.entries)
  self.assertEqual(capture_installed().entries, prepared.staged_tree.entries)
  ```

  As duas últimas asserções pertencem ao caso de recusa pós-rename em substituição; a tabela da spec determina os demais layouts, sem impor esse layout ao caso de falha anterior ao movimento.
- [ ] **Verificar RED:** `uv run pytest -q tests/test_skill_publication_service.py tests/test_skill_publication_recovery.py`.
- [ ] **Implementar protocolo:** exigir sessão T2 viva antes de efeitos; não migrar dentro do serviço nem admitir composição sobre repositório arbitrário sem a sessão. Verificar pendências; preparar staging/fsync; transação PREPARED; mover original para previous e staged para destino; fsync; revalidar; transação COMMITTED com identidade, recibo e proteção aplicável juntos. A intenção de substituição de promoção deve continuar correspondendo à árvore observada; sync usa sua elegibilidade T5. Nenhuma compensação apaga uma árvore recém-protegida.
- [ ] **Implementar erros:** antes de PREPARED, lançar `SkillMutationError`; depois, falhas operacionais esperadas retornam `PublicationResult(complete=False, error_kind=...)` ou propagam erro explícito com operation_id quando a gravação de estado falhar. Registrar `action/writer/phase/kind/operation_id/reason` sanitizados. BaseException inesperada propaga, conservando o journal. Não converter falha de persistência em resultado concluído.
- [ ] **Implementar** `reconcile_publication(session: PublicationSession, record: SkillPublicationRecord) -> PublicationResult`, sob o lock já detido. Aplicar cada linha da tabela da spec: original ainda instalado → abortar; original retirado e staged conhecido → repor/abortar se autorizado; novo instalado → concluir apenas com provas/política; duplicação, veto ou fonte indisponível → preservar e bloquear. Guard após rename continua obrigatório. COMMITTED nunca repete movimentos de instalação; retoma só confirmações. Repetição de ID terminal completo devolve o mesmo resultado comprovado.
- [ ] **GREEN:** repetir os arquivos; incluir falha após cada rename/fsync/commit real via seams estreitos e verificar bytes, identidades, eventos e tentativas de escritores concorrentes. T7 acrescenta morte não capturável do processo.
- [ ] **Commit:** `feat(skills): coordena publicação e recuperação sem perder versões`.

### Task 5: sync, promoção, manifesto e CLI

**Files:** modificar `sync.py`, `hub.py`, `kairos_cli/handlers.py`, `kairos_cli/skill_mutations.py`; criar `tests/test_skill_publication_writers.py`, `tests/test_skill_publication_manifest.py`, `tests/test_cli_skill_publications.py`; ajustar testes existentes de sync/promoção para compor o contexto real T2.

**Interfaces — consome:** T4 e `ManifestDelta`. **Produz:**

- `sync_bundled_skills(bundled_dir: Path, user_dir: Path, *, policy, context: PublicationContext | None = None) -> SyncResult`; `bundled_dir` deve coincidir com a raiz atestada da composição. Não promover raiz arbitrária a BUNDLED. O default existe apenas para diagnosticar chamadas antigas: contexto ausente recusa por `SkillMutationError(kind="input")` antes de efeitos, sem fallback legado.
- `install_from_quarantine(quarantine_path: Path, quarantine_dir: Path, skills_dir: Path, *, policy, context: PublicationContext | None = None, acquisition_id: str | None = None) -> Path`: delega ao coordenador; retorna caminho só em conclusão integral, senão erro explícito com ID. Chamadas antigas sem contexto/recibo recusam por `SkillMutationError(kind="input")` antes de efeitos. O serviço interno T4 continua exigindo sessão viva; os defaults da API pública não concedem autoridade.
- `SyncResult` conserva campos atuais e acrescenta `publications: list[PublicationResult]` e `pending: list[str]`. `reconcile_manifest(home: Path, record: SkillPublicationRecord, *, guard) -> None` faz compare-and-apply por entrada, preservando demais chaves e confirmando o journal depois do fsync do arquivo e pai.

- [ ] **RED:** `test_direct_legacy_promote_cannot_bypass_journal`, `test_sync_preserves_edits_deletions_optout_and_tombstones`, `test_committed_manifest_gap_does_not_republish`, `test_manifest_conflict_preserves_other_items`, `test_cli_partial_is_nonzero_and_has_publication_id`. A interrupção depois do manifesto e antes de sua confirmação deve reconhecer o valor já esperado, sem criar outro ID/inode.

  Nos testes de ausência de contexto/recibo, exigir `SkillMutationError.kind == "input"`, preservação das árvores e ausência de escrita no banco/manifesto. `TypeError` por argumento obrigatório ausente não cobre a recusa da API; incluir também sync sem contexto em `test_direct_legacy_sync_cannot_bypass_journal`.

  ```python
  self.assertNotEqual(process.returncode, 0)
  self.assertTrue(payload["failed"])
  self.assertIn(op, payload["pendentes"])
  self.assertEqual(recovered.operation_id, op)
  self.assertEqual(capture_installed().entries, installed_before_resume.entries)
  ```
- [ ] **Verificar RED:** `uv run pytest -q tests/test_skill_publication_writers.py tests/test_skill_publication_manifest.py tests/test_cli_skill_publications.py`.
- [ ] **Implementar escritores:** remover caminhos diretos de substituição/compensação que contornem T4; compor `publication_session` em cada operação real e chamar o serviço com ela, sem aninhar `publication_guard`/outro lock. Sync permanece por item: mantém regras de edição local, exclusão, opt-out e tombstone; entradas legadas são critérios conservadores de elegibilidade, não autoria retroativa. Toda troca efetiva de árvore cria versão/ID novos a partir da fonte confiável. Para publicação já canônica, verificar a identidade/inventário além do MD5 legado antes de substituir; adoção anterior nunca atravessa a troca. Reabrir/recompor conexões de fixtures após upgrade, sem atualizar artificialmente a capability de um cliente antigo.
- [ ] **Testar intenção concorrente:** `test_optout_after_staging_refuses_publication` e `test_changed_tombstone_before_effect_refuses_publication` exercitam mudanças antes do próximo efeito. O guard do sync reconfere opt-out/tombstones e elegibilidade além da política; conserva a preparação pendente se não puder abortar com segurança. Não mover/copiar itens adicionais só porque o lote começou antes da mudança.
- [ ] **Implementar manifesto:** guardar delta do item em PREPARED; COMMITTED passa a ser a fonte das entradas novas. Reabrir e atestar manifesto corrente sob lock, aplicar apenas o item se seu valor ainda é `before` ou já é `after`; divergência no item é conflito. Preservar mudanças de outros itens. Arquivo malformado, link ou troca entre leitura/escrita recusa. Não usar o leitor permissivo legado para decidir uma escrita B2. Confirmar projeção e limpeza por eventos independentes, sem novo commit de instalação.
- [ ] **Preservar limpeza de catálogo:** `retire_manifest_entry(name: str, *, session: PublicationSession) -> SkillPublicationRecord | None` verifica ausência no inventário do pacote e guard corrente. Para entrada B2, acrescenta `manifest_prepared` com delta para ausência ao ID existente e confirma a projeção; não move árvore nem renova instalação. Recuperação dessa manutenção usa a prova de ausência no pacote atual, sem declarar a fonte antiga disponível. Para entrada apenas legada, mantém a limpeza atômica existente sob lock/guard, sem fabricar publicação. Acrescentar `test_package_removal_cleans_manifest_without_installation_event`; ID/inode/adoção não mudam, e interrupção após preparação bloqueia o nome.
- [ ] **Implementar saídas:** listas copied/updated só recebem itens completos. JSON de sync conserva chaves existentes e inclui `publicacoes` e `pendentes`; `failed=True` em falha parcial. Exit 77 para veto, 69 para indisponibilidade da plataforma, 2 para entrada inválida, 1 para demais falhas/pendências, 0 só para resultado integral. Opt-out continua uma decisão explícita de não semear. `history/identify` expõem estado verdadeiro sem caminhos privados. Nenhum comando novo de hub/download/restore genérico.
- [ ] **GREEN:** repetir os três arquivos e `uv run pytest -q tests/test_skills.py tests/test_skill_sync_categories.py tests/test_skill_removal_sync.py tests/test_skill_adoption_sync.py tests/test_skill_org_publication.py`; testes novos de CLI usam executável real com home descartável, sem provider.
- [ ] **Commit:** `feat(skills): publica sync e quarentena pelo coordenador comum`.

### Task 6: backup integral, reparo e restauração diagnóstica

**Files:** criar `publication_backup.py`, `tests/test_skill_publication_backup.py`; modificar `kairos_cli/backup.py`, `kairos_state/migrations.py` e `kairos_state/skill_publications_schema.py` para integrar validação/reparo à rotina de backup.

**Interfaces — consome:** T1/T3/T4. **Produz:** `publication_backup_inventory(home: Path, repository: SkillPublicationRepository) -> tuple[PublicationBackupEntry, ...]`, com `PublicationBackupEntry(relative_path: str, proof: TreeCapture)` validado contra refs derivadas dos IDs; `validate_restored_publication(home: Path, record: SkillPublicationRecord, *, policy) -> RestorationObservation`. Definir `RestorationObservation` com `operation_id/observed_home_id: str`, `present_artifacts: tuple[str, ...]`, `inventory_valid/identity_valid: bool` e `reasons: tuple[str, ...]`. Uma observação não altera o journal nem concede autoridade.

- [x] **RED:** `test_backup_covers_all_referenced_artifacts`, `test_missing_artifact_preserves_previous_archive`, `test_restore_new_inodes_has_no_new_authority`, `test_database_only_restore_blocks_recovery`, `test_repair_does_not_complete_pending_publication`. Comparar canônicas antes/depois; restaurar arquivo real em home isolado e verificar bytes/modos de staged/previous/instalado, ausência de binding transferido e recusa mutante.

  ```python
  self.assertTrue(observation.inventory_valid)
  self.assertFalse(observation.identity_valid)
  self.assertEqual(canonical_rows(restored, tables), canonical_before_backup)
  self.assertFalse(identify_restored().removivel)
  ```
- [x] **Verificar RED:** `uv run pytest -q tests/test_skill_publication_backup.py`.
- [x] **Implementar backup:** para home B2, adquirir o lock comum antes da observação do journal, snapshot SQLite e leitura das árvores; atestar inventários antes/depois. Incluir `.skill-publications`, recibos, journal, manifesto e árvore instalada, com todos os snapshots preservados e pendências. Exigir os artefatos esperados pelo estado/layout observado na tabela de recuperação: staged ausente depois de rename para o destino não é, por si só, corrupção. Quarentena externa não entra implicitamente no arquivo. Artefato obrigatório ausente, links, escape ou alteração recusam o novo backup e preservam saída anterior. Homes legados mantêm o contrato existente, sem migração implícita.
- [x] **Implementar restore/reparo:** inventários íntegros comprovam os bytes restaurados, não os inodes anteriores. Diagnóstico não renova recibo, instalação, adoção ou binding. Reparo recria somente derivados/triggers v11 e não exclui pendências. Retomada mutante ainda exige o contexto e as identidades compatíveis de T4; observação de restore por hash não satisfaz esse requisito. Em outro home, histórico continua histórico e vínculos não se transferem; no mesmo home, o veto durável de B1 permanece. Fonte externa ausente impede concluir limpeza, sem escolher outra pasta pelo nome. Não criar comando de importação/reatribuição de autoridade para remover esse bloqueio.
- [x] **GREEN:** repetir o arquivo e `uv run pytest -q tests/test_cli_backup_boundary.py tests/test_skill_adoption_backup.py tests/test_skill_org_backup.py`; conferir `PRAGMA integrity_check`, fingerprints canônicos e falhas sanitizadas na API pública de backup.
- [x] **Commit:** `feat(backup): preserva artefatos e pendências de publicações`.

### Task 7: interrupções reais, clientes antigos e aceite na imagem

**Files:** criar `tests/test_skill_publication_processes.py`, `tests/fixtures/skill_publication_process.py`, `tests/skill_publication_image_acceptance.py`, `scripts/prepare-skill-v10-client.sh`; modificar `kairos_state/schema.py`, `tests/test_container.py`, `scripts/ci.sh`, `.github/workflows/ci.yml`, `docs/functional-progress.md`, `docs/cli-auth-progress.md`, `docs/decisoes.md`; criar `docs/superpowers/reports/2026-10-22-publicacao-skills-b2.md`.

**Interfaces — consome:** T1–T6. A fixture de subprocesso usa APIs de produto e Pipe para sinalizar fronteiras reais; seams chamam a operação real antes/depois da pausa. Não acrescentar flags de teste ao runtime de produção.

- [x] **Ativar release integrada:** somente após T1–T6, atualizar `SCHEMA_VERSION` padrão para 11 e verificar homes novos, upgrade de v10 e leitura legada. O teste exige presença/comportamento da capacidade solicitada, não congela a última versão do catálogo de migrações.
- [x] **RED:** `test_sigkill_boundaries_preserve_recoverable_versions`, `test_v10_client_refuses_v11_after_repair`, `test_cross_device_quarantine_in_real_image`, `RealImageTests.test_skill_publication_recovery_and_backup`. A execução deve falhar com a implementação sem retomada/proteção correspondente; registrar a mutação específica quando a capacidade já estiver implementada. Um teste que apenas encontra um programa já encerrado não prova SIGKILL.
- [x] **Implementar fixture antiga:** extrair/buildar o cliente v10 real da base implantada `44d890ca632b40b78a21b62c5e05622200a5b23c` em venv isolado, com dependências verificadas e wheel/digest/proveniência registrados, seguindo `prepare-skill-v8-client.sh`. Preparar cliente v10 tanto no CI local quanto remoto. Exercitar as famílias antigas com os clientes anteriores disponíveis, e capabilities 8/9/10 em testes comportamentais de transações reais. Nenhum teste muda a função do cliente para fazê-lo declarar capability 11.
- [x] **Implementar matriz de morte:** processo filho pausa antes/depois de PREPARED, de cada rename/fsync, de COMMITTED, da gravação do manifesto e das confirmações de projeção/limpeza. Pai exige sinal da fronteira, envia SIGKILL, confirma exitcode -9 e reabre serviço/SQLite reais. Cobrir publicação inicial e substituição, política que muda entre processos, fonte indisponível, árvore trocada byte-idêntica e órfão desconhecido. Comparar IDs, inodes, inventários e histórico; repetir retomada e tentar outro escritor do mesmo nome.

  ```python
  self.assertTrue(parent.poll(deadline_seconds))
  reached = parent.recv()
  self.assertEqual(reached["phase"], expected_phase)
  process.kill()
  process.join(deadline_seconds)
  self.assertEqual(process.exitcode, -9)
  self.assertEqual(recover_again.operation_id, recovered.operation_id)
  ```
- [x] **Implementar aceite na imagem:** o teste invoca o script de aceite montado somente leitura, importando os módulos instalados da imagem, sem montar fontes de produto. Usar UID sem privilégios, rede desativada e volumes descartáveis. Quarentena em tmpfs e destino em outro volume devem ter `st_dev` diferentes comprovados; se o requisito não foi exercitado, o aceite não passa silenciosamente. O script reutiliza a matriz de processos, inclui manifesto/limpeza pendentes e backup/restauração integral/banco isolado, sem provider ou credencial real.
- [x] **Verificar GREEN:** `uv run pytest -q tests/test_skill_publication_processes.py`; depois construir imagem real/stub/broker/worker e executar `uv run pytest -q tests/test_container.py::RealImageTests`, exigindo o teste novo sem skips. Regressões de família velha e corrupta permanecem ativas. O caminho live segue desabilitado.
- [ ] **Revisar e validar integração:** revisão independente do diff inteiro, correção dos bloqueadores com RED→GREEN, conferência integrada pelo Root; `scripts/ci.sh` completo com Codex fixado no PATH; integrações Docker offline e lint pinado do Dockerfile.stub conforme CI. Reutilizar evidência só se comando, revisão, arquivos, lockfile, configuração e ambiente ainda corresponderem. Não prometer análise Sonar quando o scanner não rodou; conferir jobs remotos e Quality Gate do commit efetivamente proposto.
- [x] **Registrar entrega:** comandos, códigos de saída, revisão, imagem/digests, skips/deselects e causas, resultados de recuperação, backup e restauração no relatório. Atualizar decisões como implementadas somente após aceite correspondente; sem marcar este plano concluído por ter sido escrito. Merge exige suíte local e jobs de verificação remotos verdes; deploy é pós-merge e depende da autorização aplicável, com backup/restore privado, smoke e rollback conforme o runbook.
- [x] **Commit:** `test(skills): valida publicação interrompida na imagem real`.

## Revisão do plano e passagem para execução

Este plano define schema, contratos e testes propostos; nenhum símbolo novo foi implementado nem check de produto executado por sua redação. Antes de implementar, a usuária revisa este documento. A execução nativa proposta respeita a preferência de agente único; a revisão independente permanece obrigatória no fechamento.

Auto-revisão do Root realizada em 22/10: objetivo/limites e autoridade cobertos em T2–T5; recibos em T1/T2; protocolo/estados/tabela de recuperação em T1/T4/T7; manifesto em T5; migração/clientes/leitores em T1/T3/T7; backup/reparo/restore em T6/T7; erros e aceite em T4/T5/T7. Os cinco focos acima têm casos próprios. Foram corrigidos durante a revisão: lifetime único do lock/conexão, source_key estável para retomada, staging entre devices sem exigir identidade igual, manutenção de manifesto sem instalar e ativação do schema somente no fechamento. Não foi delegada essa auto-revisão. Se a base avançar, resolver divergências antes do primeiro RED e registrar as decisões no ledger da execução.

Revisão de alinhamento à main em `44d890c`, em 22/10: os conflitos documentais foram resolvidos preservando os checkpoints e as evidências integradas pelo PR #113. O relatório da promoção permanece idêntico ao da main. O diff de B2 contém somente planejamento e status de aprovação; código, testes, dependências, configuração e workflow permanecem iguais à base. A revisão explicitou a recusa de colisões também em `quarantine_bundle`, os erros categorizados nas APIs públicas sem contexto/recibo, as referências lógicas sanitizadas, as assinaturas de I/O com guard obrigatório e a fixture v10 da base implantada. Os limites da spec aprovada e os cinco focos de revisão foram reconferidos, sem implementação ou aprovação presumida do plano.

Validação documental desta revisão: `git diff --check`, 31 links locais e sintaxe Python 3.11 dos sete exemplos aprovados. Os exemplos não foram executados como testes e nenhuma suíte de produto foi atribuída à redação do plano.


## Execução local — T1 e T2, 22/10/2026

Execução nativa retomada na branch `feat/publicacao-skills-b2`, preservando T1
em `ff23200`. T2 implementa fontes vivas da composição, sessão única de lock
e banco, aquisição exclusiva com scan e recibo canônico e árvores privadas
com staging, snapshot anterior e movimentos sem sobrescrita. A API legada de
quarentena passou a recusar colisões preservando árvore e recibo existentes.

A decisão adicional de T2 foi vincular o recibo também à identidade da raiz
de quarentena. A árvore filha poderia ser movida intacta para outra raiz;
`source_proof` e `source_key` sozinhos não detectariam esse caso. O campo
`quarantine_identity` integra o formato v11 ainda não publicado; nenhum
registro v10 é reescrito. Custo de revisão dessa decisão: ajustar DTO/fixtures
v11 antes da publicação, caso a composição passe a fornecer prova equivalente.

Validação: suíte integral com Codex 0.154.0, **3964 testes e 6424 subtestes
aprovados**, 50 skips e 1 deselect. Após os últimos reforços de guard e logging,
a rodada dos arquivos afetados passou com **106 testes e 39 subtestes, sem
skips**; inclui aquisição/recibo/staging reais entre `/dev/shm` e o home
temporário em outro device. Ruff check, format e diff-check passaram. Os
registros integrais ficam no workspace ignorado `.superpowers/sdd/` deste
plano. A validação entre devices no host não substitui o aceite na imagem
real de T7.

Ao concluir T2, a próxima tarefa era T3, projeção de instalação, identificação
e bloqueios comuns. Nesse checkpoint, T3–T7, revisão independente da branch,
aceite integrado, CI remoto, merge e deploy permaneciam pendentes; o avanço
posterior está registrado abaixo. O schema padrão continua v10 e nenhuma instalação
de produção foi migrada. Não liberar os commits intermediários como B2 pronta.


## Execução local — T3, 23/10/2026

A timeline comum reconhece a quarta família e suas confirmações sem reaplicar
a instalação. Publicação cria uma identidade canônica nova, sem transportar
a criação manual ou adoção anteriores, mesmo com bytes iguais. Aquisição
verificada continua com proveniência ausente, `ownership=unknown`, sem
autorização de remoção. Adoção foreground posterior é explícita e revogável;
nome e conteúdo não a substituem. Troca de raiz também retira o diagnóstico
de fonte verificada. Catálogos e assets de sessões anteriores são preservados.

O guard comum bloqueia escritores em PREPARED/CONFLICT e em COMMITTED com
manifesto ou limpeza pendentes, inclusive repetição de rollback/restore e
recuperação legada. O histórico JSON informa escritor, origem, confirmações e
pendência; ID de publicação não é aceito como criação/restauração antiga.
Recusas de autoridade e de recuperação geram logging estruturado sanitizado.

A execução ampliou pontualmente o mapa de arquivos de T3 para os repositórios
de adoção e remoção: suas consultas ainda liam a timeline v9/v10 congelada e
não conseguiam ler novos eventos em v11. O seletor usa somente nomes fixos de
timeline; nenhuma linha histórica foi reescrita. Custo de rever essa decisão:
revisar quatro consultas nesses dois repositórios. `InstallationProof` passou
a carregar `installation_source` opcional para restringir proveniência ausente
a uma publicação verificada; o formato v11 ainda não foi publicado.

Validação: suíte integral com Codex 0.154.0, **3980 testes e 6436 subtestes
aprovados**, 51 skips e 1 deselect. Após os ajustes finais de diagnóstico e
logging, os arquivos afetados e regressões T1/T2 passaram com **112 testes e
67 subtestes, sem skips**, incluindo cópia real entre devices. A rodada exigida
para T3 e identificação/adoção/política passou com **62 testes e 50 subtestes**.
Ruff check, format e diff-check passaram. Os testes exercitam arquivos, SQLite
e a CLI; os casos de projeção preparam publicações com journal/arquivos reais,
sem atribuir a T3 o coordenador de T4 ou o aceite na imagem de T7.

Próxima tarefa: T4, coordenação da publicação e recuperação observacional por
ID. T4–T7, revisão independente da branch e aceite integrado permanecem
pendentes. Schema padrão v10, sem migração de produção, merge ou deploy.


## Execução local — T4, 23/10/2026

O coordenador usa a sessão viva T2, sua conexão e seu lock. PREPARED é
durável antes do primeiro movimento instalado; COMMITTED só ocorre após
revalidar identidades, fonte, política e proteção organizacional. Capturas
e movimentos ficam fora da transação SQLite; o guard de commit verifica
descritores e metadados observados. Substituição exige a intenção foreground
sobre a árvore exata, e a versão anterior permanece no snapshot privado.

A recuperação observa os cinco layouts da tabela, sem adotar órfãos ou
apagar duplicatas desconhecidas. Um novo veto mantém as versões pendentes.
COMMITTED retoma somente confirmações; repetir ID completo comprova a árvore
instalada, o snapshot e a compatibilidade do contexto, sem renovar instalação.
Falhas operacionais carregam ID e resultado incompleto; interrupções inesperadas
propagam. Recusas geram logging estruturado sanitizado.

A integração ampliou três helpers previstos em T2/T3: guard permite somente
o próprio draft canônico pendente; resolução usa o repositório da sessão; I/O
observa árvores para commit e descarta apenas staging conhecido e permitido.
A observação imutável da fonte termina imediatamente antes de sua exclusão
verificada, mantendo raiz/contexto/política e instalação/snapshot atestados.
Se a fonte desapareceu sem confirmação durável de limpeza, a retomada conserva
a pendência: ausência não comprova a autoria da exclusão. Custo de rever essas
decisões: endurecer helpers ou especificar tratamento explícito dessa pendência,
sem inferir sucesso nem mudar linhas canônicas antigas.

Suíte integral: **4009 testes e 6441 subtestes**, com 51 skips e 1 deselect,
Codex 0.154.0. Ruff check, format e diff-check passaram. Validação dirigida
final: **134 testes e 57 subtestes**, sem skips, incluindo
as regressões T1–T4 e fonte em dispositivo realmente distinto. A projeção
efetiva do manifesto e a elegibilidade completa de sync são T5; delta de
manifesto ainda não confirmado impede sucesso integral em T4. T5–T7, revisão
independente e aceite integrado permanecem pendentes; schema padrão v10.


## Execução local — T5, 23/10/2026

Sync e promoção usam contexto T2 e coordenador T4; ausência de contexto ou
recibo é recusa `input` antes de efeitos. Promoção exige intenção sobre a
árvore exata para substituir. Sync conserva edição/exclusão local, opt-out
(inclusive marcador com alvo ausente), tombstones e elegibilidade legada.
Uma instalação B2 também precisa da identidade e do inventário canônicos: a
cópia byte-idêntica em outra raiz não herda elegibilidade. O MD5 legado é
calculado a partir de captura por descritores para decisões de escrita, sem
seguir links; sua ordenação histórica é preservada.

O manifesto usa leitura estrita e compare-and-apply de uma entrada; demais
entradas são preservadas, inclusive chaves surgidas durante a publicação.
Após fsync do arquivo/pai, a confirmação verifica a observação corrente no
commit. Lacuna depois da gravação retoma o mesmo ID/inode; limpeza de catálogo
usa `manifest_prepared` no ID existente, sem evento de instalação ou herança
de adoção. Ausência no pacote atual autoriza somente essa manutenção;
tombstones permanecem. Snapshot anterior continua preservado.

A CLI pública existente `kairos sync now` foi ativada em `commands.py`; o
handler `skills sync` não estava exposto pelo parser. JSON mantém campos
anteriores e acrescenta `publicacoes`/`pendentes`; itens incompletos não entram
em copiadas/atualizadas, e os códigos distinguem veto, indisponibilidade,
entrada inválida e pendência. Não há novo comando de hub/download/restore.

Decisões de integração: `publication_manifest.py` isola o compare-and-apply e
evita ciclo com o coordenador; fontes/revalidações fecham descritores
temporários por entrada e liberam observações só ao terminar a operação ou
decisão sem efeito, mantendo os guards. O preflight captura sem prender todas
as fontes; pendências conhecidas são reconciliadas antes de validar outros
itens do pacote. Um SKILL.md do pacote real excede 64 KiB: o sync sem pendência
recusa o preflight, em vez de aumentar o limite ou alterar conteúdo de
terceiros. Isso não oculta o ID de publicação já pendente. Rever essas
decisões custa adaptar helpers/roteamento ou tratar o conteúdo em recorte
próprio; não autoriza retirar vetos nem transformar manifesto em autoria.

Validação integral estável: **4027 testes e 6441 subtestes**, 51 skips e
1 deselect, Codex 0.154.0; fingerprint da árvore conferido antes/depois
(`4aac463f2ac671b8dac407219b50d6e1d507833f48d12e9defebf04a9ac0afcc`).
Ruff check/format globais e diff-check passaram. Rodada afetada anterior:
109 testes/38 subtestes; teste sob limite de 160 descritores passou também ao
repetir o lote. Mutação sem liberação recusou o lote, como esperado.

Uma tentativa integral anterior terminou em segfault após dump adicional
do faulthandler de 60s; outra rodada foi invalidada por edição de fixture
durante subprocessos. Nenhuma foi contada como verde. Os casos isolados de
catálogo/identificação passaram no host; a integral acima usou o comando
normal do CI e árvore estável, sem remover testes. A causa nativa do primeiro
segfault não foi atribuída ao produto sem evidência. T6–T7, revisão
independente e aceite integrado seguem pendentes; padrão v10, sem migração
de produção, merge ou deploy.


## Execução local — T6, 23/10/2026

Backup v11 agora segura o lock comum durante journal, snapshot SQLite e
inventários antes/depois. Preserva modos, versões e saída anterior diante
de ausência, alteração, links ou erro de fsync. A saída é publicada por
replace atômico após fsync do arquivo, seguido de fsync do diretório.
Homes legados mantêm o caminho sem migração implícita.

O diagnóstico separa inventário de identidade: restauração em novos inodes
não renova recibos, adoções, binding ou autoridade. Reparo relata os objetos
v11 recriados e não conclui pendências. Referências estrangeiras não podem
sumir pelo filtro do home; backup integral recusa esse transplante. Árvores
retiradas por REMOVE pendente são reconhecidas somente pelo ID canônico e
pela prova completa da remoção.

Validação: 12 testes novos passaram, incluindo disputa real do lock com
escritor de outra família; suíte integral congelada com Codex 0.154.0:
**4039 passed, 51 skipped, 1 deselected, 6441 subtests passed** (478,38 s).
Fingerprint de fontes antes/depois idêntico:
`4b2be91d78ac7234acae582eea267e96a7b36cfab0041a6e93926ffc583597ad`.
Ruff check/format globais e diff-check aprovados antes dessa suíte.
Schema padrão permanece v10 até T7; imagem e revisão independente pendentes.


## Execução local — T7, 23/10/2026

T1–T6 concluídas antes da ativação padrão de v11. Homes novos, upgrade v10 e
leitura legada foram exercitados sem congelar o catálogo de migrações.
Cliente v10 real da base `44d890c`, wheel reproduzido e dependências por hash,
é preparado tanto pelo CI local quanto pelo remoto. Clientes v8/v10 e
capabilities 8/9/10 são recusados em v11, inclusive após reparo.

A matriz usa Pipe e SIGKILL comprovado (-9) em 102 fronteiras: 50 iniciais e
52 substituições, com cada fsync descoberto pelo caminho de controle real,
renames, transações sem commit, COMMITTED e confirmações. Repetição preserva
ID, inodes e história; outro escritor é bloqueado enquanto há pendência.
A imagem executa os módulos instalados, com scripts de teste RO, UID10000,
rede none, caps removidas, rootfs RO e filesystems distintos comprovados.
Backup/restauração integral e banco isolado não transferem autoridade.

O primeiro CI encontrou quatro falhas unitárias e uma na imagem e não foi
contado como aprovação. Correções RED→GREEN cobrem backup v11 sem skills e
inicialização/upgrade concorrente: a parcial inicialmente legada é descartada
e recapturada sob lock. Fixtures de corrupção agora alteram a timeline ativa
e recompõem guards; os serviços são testados separadamente do bootstrap que
recusa schema corrompido. O teste sob160FDs manteve lote e inventários, com
deadline300s após probe com coverage concluir corretamente em139,21s.

Rodada definitiva de `scripts/ci.sh`: **exit0**, árvore congelada
`40d43040b1f9a1c6c1928e61cb152ed924bcc6c67699d076902ac432578f18d9`.
Python com coverage: **4050 passed,21 skipped,33 deselected,6495 subtests**
(1145,58s). Imagem: **32 passed,26 subtests,sem skips** (190,83s). Docker
offline: **26 passed,1 skipped** (59,90s), skip exige AppArmor/seccomp do host.
Frontends17/204/18 e tsc verdes; lint, lock, recall e quatro builds verdes.
Sonar scanner exit0 e processamento SUCCESS, mas Quality Gate ERROR por
confiabilidade; análise local não comprova o commit final remoto.

Revisão independente final concluída: seis achados corrigidos com regressões
RED→GREEN. Validação local pós-revisão:4064 testes/6502 subtestes, imagem32/26
subtestes sem skips e Docker offline26/1 skip. Um timeout de startup no teste
RPC foi reproduzido de forma controlada e corrigido por confirmação real de
prontidão, mantendo os200ms da rejeição. O módulo passou sob cobertura;
a suíte final completa passou no modo normal, sem cobertura. Não atribuir
a aprovação instrumentada histórica ao código corrigido. CI remoto é gate
antes de merge; nenhum merge, deploy ou migração de produção foi executado.
Detalhes, digests, decisões e limites no
[relatório](../reports/2026-10-22-publicacao-skills-b2.md).
