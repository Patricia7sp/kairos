# Autoria de skills com proveniência e rollback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar uma skill pela CLI, registrar sua origem e permitir reversão comprovada sem descartar trabalho posterior do usuário.

**Architecture:** Leitor/publicador por descritores e lock por home; ledger SQLite append-only; serviço que coordena preparação, publicação, confirmação e recuperação. Rollback retira a criação para área privada preservada. CLI só valida opções e apresenta metadata; sync compartilha o lock sem mudar sua política.

**Tech Stack:** Python 3.11, dataclasses, SQLite, descritores POSIX, flock e publicação Linux sem substituição; unittest/pytest e uv. Sem dependências novas.

**Spec:** `docs/superpowers/specs/2026-10-08-autoria-skills-design.md`, aprovada pela usuária em 2026-10-08.

## Global Constraints

- Base main `0463a5c04aac368bd6aa06f4368fa4645111c257`; branch `feat/autoria-skills`, worktree `.worktrees/skills_authoring`. Checkout original preservado.
- Criar somente um `SKILL.md`, sem anexos, URL, stdin, replace/force ou inferência de autor. Fonte regular UTF-8 sem links, até 64 KiB, bytes preservados.
- Nome kebab-case até 64; descrição até 60 e terminada em ponto; versão semver; corpo não vazio. Parser estrito existente: sem aliases, no máximo 64 níveis/2.048 nós.
- Ator explicitamente `Actor.USER_FOREGROUND`; origem `Provenance.USER` fornecida pelo serviço. Ator autônomo recusado; arquivo/CLI/contexto/ambiente não elegem origem.
- Ausência de proveniência em skill antiga significa USER para política de propriedade, sem migrar/reescrever o arquivo.
- Fonte/home/skills/área privada por descritores O_NOFOLLOW; arquivos regulares com um link, read limitado e fstat antes/depois; nenhuma escrita fora dos diretórios autorizados.
- Publicação e movimentos por rename atômico sem substituir destino. Ausência de recurso, filesystem distinto ou mount incompatível: código 69, sem fallback inseguro.
- Área `<home>/.skill-mutations/`, fora do catálogo; arquivos 0600, diretórios privados 0700. Lock `<home>/.skills-write.lock`, exclusivo, espera máxima cinco segundos. Não varrer/remover desconhecidos.
- Journal durável antes do efeito; só sucesso após efeito verificado e evento committed. Sem transação única SQLite/filesystem; pendência com ID é erro, nunca sucesso simulado.
- Rollback só da criação comprovada, mesma identidade e somente SKILL original. Edição/anexo/substituição/ausência externa recusa; pós-movimento divergente conserva diretório inteiro.
- Ledger e conteúdo são canônicos; eventos append-only, rollback gera operação nova. Migração/reparo/backup e abertura por pacote anterior preservam os dados.
- Catálogos existentes não mudam com autoria/rollback; cache e transcript permanecem. `--skill` lê arquivo atual e catálogo de sessão nova observa a instalação.
- CLI: add --file; history --name opcional, --limit 1–100 padrão 20; rollback ID. JSON metadata sem corpo/autor/path absoluto. Códigos 0/2/1/69 conforme a spec; stdin intacto.
- Não alterar credenciais, permissões de tools/workspace, Web, Agent Runtime ou curador automático. Nenhuma chamada real/paga ou runtime_live nos testes/smoke.
- TDD comportamental, sem ler fonte Python em teste. Gate: scripts/ci.sh completo + nove verificações remotas; squash/diff pós-merge e dez jobs main/deploy, smoke instalado com fixtures.

## Review Focus

1. Editor altera o diretório já retirado pelo rollback e outro processo ocupa o nome original: preservar ambas as versões e reportar conflito — Task 4.
2. Processo morre após rename/fsync mas antes do commit, ou banco fica ocupado nessa janela: reconhecer só a identidade própria, reconciliar uma vez e nunca ocultar pendência — Tasks 3/4.
3. Staging desconhecido ou uma cópia byte-idêntica com inode diferente: não promover, limpar ou reverter por igualdade de texto — Tasks 1/3/4.
4. Backup do banco restaurado sobre filesystem atual divergente e conteúdo/hash/evento adulterado: recusar escrita e não reconstruir origem por suposição — Tasks 2/4.
5. Sync simultâneo, lock trocado por link, timeout e libc/kernel sem rename seguro: manter conteúdo anterior e emitir erro privado, sem fallback — Tasks 1/5.

---

## Arquivos e contratos comuns

| Arquivo | Responsabilidade |
|---|---|
| `kairos_skills/mutation_contract.py` | Records imutáveis, enums, limites, validação de criação e erro público |
| `kairos_skills/mutation_lock.py` | Lock compartilhado seguro por home, sem SQLite |
| `kairos_skills/mutation_io.py` | Fonte, staging, inspeção, rename sem substituição e fsync |
| `kairos_state/skill_mutations_schema.py` | SQL aditivo do ledger/eventos/conteúdo |
| `kairos_state/repositories/skill_mutations.py` | Transações, validação do ledger e metadata |
| `kairos_skills/mutation_recovery.py` | Reconciliação observacional de create/rollback sob lock |
| `kairos_skills/mutations.py` | Serviço de criação, rollback, histórico e propriedade |
| `kairos_cli/skill_mutations.py` | Composição e apresentação da CLI, sem lógica de filesystem |

Records frozen em `mutation_contract.py`:

Todos validam os campos na construção, inclusive criação direta dos records;
não depender apenas de o chamador ter usado uma factory. Valores vindos do
banco têm erro corrupt; entradas novas inválidas têm erro input.

- `SkillCreation(name: str, text: str, sha256: str, size_bytes: int)`; construído por `validate_skill_creation(text: str) -> SkillCreation`, valida hash/tamanho/formatos.
- `SkillDirectoryIdentity(directory_dev: int, directory_ino: int, skill_dev: int, skill_ino: int)`; inteiros exatos não negativos, nunca bool.
- `SkillFilesystemEntry(identity: SkillDirectoryIdentity, creation: SkillCreation)`; inspeção só produz entry se inventário for exatamente SKILL.md.
- `SkillMutationDraft(operation_id: str, home_id: str, action: SkillMutationAction, name: str, actor: Actor, created_at: float, identity: SkillDirectoryIdentity, sha256: str, size_bytes: int, reverts: str | None = None)`.
- `SkillMutationRecord(draft: SkillMutationDraft, sequence: int, state: SkillMutationState)`; propriedades readonly `operation_id`, `name`, `action`, `actor`, `provenance`, `sha256`, `reverts` encaminham draft; provenance sempre USER após validar ator.
- `SkillMutationAction`: CREATE=`create`, ROLLBACK=`rollback`; `SkillMutationState`: PREPARED=`prepared`, COMMITTED=`committed`, ABORTED=`aborted`, CONFLICT=`conflict`.
- `SkillMutationError(kind: str, message: str, *, operation_id: str | None = None)`; kind fechado `input|conflict|io|corrupt|unavailable`; mensagens próprias sem repr de entradas ou exceção backend.

ID de operação: UUID4 em 32 caracteres hex minúsculos. home_id reutiliza SHA-256 do home absoluto validado (`catalog_home_id`), sem enviar path ao usuário. Tempo é número finito não negativo. JSON de identity canônico, limite 1 KiB antes de parse; conteúdo de no máximo 64 KiB antes de leitura do banco. Não usar records para escolher a origem a partir de parâmetros públicos.

## Task 1: Validação, lock e filesystem contido

**Files:** Create `kairos_skills/mutation_contract.py`, `mutation_lock.py`, `mutation_io.py`; Test `tests/test_skill_mutation_io.py`, `tests/skill_mutation_fixtures.py`. Reusar parser e leitores por descritores existentes sem alterar sua compatibilidade.

**Interfaces:**
- Produces records/funções comuns acima; `read_skill_creation(source: Path) -> SkillCreation`.
- Fixture compartilhada em `tests/skill_mutation_fixtures.py`: `creation_text(name: str = "revisar-docs", body: str = "Procedimento fictício.", *, description: str = "Revise documentos.", version: str = "0.1.0") -> str`.
- Produces `skill_mutation_lock(home: Path, *, timeout: float = 5.0) -> ContextManager[None]`; thread + flock coordenados, prazo único monotônico, timeout erro conflict. Lock regular sem links; não usar guard de cofre que aceita plataforma sem flock.
- Produces `SkillMutationFiles(home: Path)` contextmanager com FDs fechados na saída; métodos `stage(operation_id: str, creation: SkillCreation) -> SkillFilesystemEntry`, `inspect_installed(name: str) -> SkillFilesystemEntry | None`, `inspect_private(operation_id: str, *, retired: bool = False) -> SkillFilesystemEntry | None`, `publish(operation_id: str, name: str) -> None`, `retire(name: str, operation_id: str) -> None`, `restore(operation_id: str, name: str) -> None`, `discard_staging(operation_id: str, expected: SkillFilesystemEntry) -> None`, `assert_name_available(name: str) -> None`.
- Private layout: `.skill-mutations/staging/ID` e `.skill-mutations/retired/ID`. discard somente staging comprovado e intacto: unlink SKILL + rmdir, nunca rmtree de inventário desconhecido. Nenhuma limpeza automática dos diretórios retired.
- assert_name_available recusa qualquer entrada ou nome no manifesto bundled, inclusive tombstone; ler manifesto sob lock, limitado a 1 MiB, regular sem links; manifesto ausente é vazio, ilegível/malformado é conflito, não ausência.
- Fonte/destino/checks usam FD rooted; rename Linux libc `renameat2(oldfd, oldname, newfd, newname, RENAME_NOREPLACE=1)` com ctypes stdlib, errno traduzido. ENOSYS/ausência de símbolo/EOPNOTSUPP/EINVAL de flags/EXDEV ⇒ unavailable. Nunca os.replace como fallback.

- [ ] **Step 1: Escrever testes RED e fixtures.** `creation_text(name="revisar-docs", body="Procedimento fictício.")` gera frontmatter semver 0.1.0, descrição `Revise documentos.`; não reusar a versão legada `antiga` para criação. `test_creation_preserva_bytes` afirma `entry.creation.text == text`, sha256 real e tamanho UTF-8. `test_creation_rejeita_formatos` parametriza descrição 61/ponto ausente, versão antiga, YAML tipos/aliases, vazio/UTF-8/64KiB+1. `test_actor_e_identidade_nao_aceitam_coercao` cobre bool/int/enum ausente.

```python
creation = read_skill_creation(source)
assert creation.text.encode("utf-8") == source.read_bytes()
assert creation.size_bytes == len(source.read_bytes())
with pytest.raises(SkillMutationError):
    validate_skill_creation(creation_text(description="x" * 61))
```
- [ ] **Step 2: Run RED.** `uv run pytest -q tests/test_skill_mutation_io.py`; esperado falhas por módulos/contratos ausentes, não fixture malformada.
- [ ] **Step 3: Implementar contratos, leitor e lock.** O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC, regular nlink1, read com teto+1 antes de decode, fstat size/mtime/ctime e revalidação de entrada no parent FD. Não normalizar `..` ou symlink. Um lock ocupado usa LOCK_NB + prazo monotônico; código segura fechamento/release em erro. Sem dependências/decoradores que silenciem falha de plataforma.
- [ ] **Step 4: Escrever RED de publicação/segurança.** `test_publish_nao_substitui` publica e compara inode/bytes; segundo destino arquivo/vazio/link recusa e preserva marcador. `test_links_e_fifo` cobre cada componente, hardlink e FIFO em subprocesso com timeout 2s. `test_read_replacement` troca fonte após open e exige recusa sem texto externo. `test_private_desconhecido_preservado` põe marker em ID não reconhecido e comprova intacto; `test_copia_identica_nao_tem_identidade_original` mostra entry com mesmo hash e identity distinta. `test_platform_recusa_sem_fallback` força indisponibilidade somente na fronteira libc, sem substituir filesystem guard. `test_lock_link_timeout_e_release` usa processos reais, timeout curto controlado, release após exceção e deadline de cinco segundos configurado no contrato.
- [ ] **Step 5: Implementar stage/inspect/movimentos.** fsync arquivo/diretório e ambos os pais nos movimentos, todos no mesmo device. stage mkdir exclusivo, SKILL O_EXCL; registrar identity real. Inspeção devolve None só para ausência comprovada: EACCES/I/O não equivale a ausência. Movimento revalida entry no serviço; helpers nunca removem destino conflitante. Decodificar manifesto de modo conservador sem mudar parser global de sync.
- [ ] **Step 6: Run GREEN e lint.** `uv run pytest -q tests/test_skill_mutation_io.py tests/test_skill_runtime.py tests/test_skill_catalog_io.py`; esperado zero falhas. Ruff check/format desses arquivos. Commit `feat(skills): valida criação e publica sem sobrescrita`.

## Task 2: Ledger append-only e migração aditiva

**Files:** Create `kairos_state/skill_mutations_schema.py`, `kairos_state/repositories/skill_mutations.py`; Modify `kairos_state/migrations.py`, `schema.py`, `repositories/__init__.py`; Test `tests/test_skill_mutation_state.py`.

**Interfaces:**
- Consumes records da Task 1.
- Produces `SkillMutationRepository(connection: sqlite3.Connection, *, home_id: str)` com property home_id; métodos `prepare(draft: SkillMutationDraft, text: str) -> SkillMutationRecord`, `get(operation_id: str) -> SkillMutationRecord | None`, `content(operation_id: str) -> SkillCreation`, `finish(operation_id: str, state: SkillMutationState) -> SkillMutationRecord`, `pending(name: str) -> tuple[SkillMutationRecord, ...]`, `latest_rollback(create_id: str) -> SkillMutationRecord | None`, `history(*, name: str | None = None, limit: int = 20) -> tuple[SkillMutationRecord, ...]`, `provenance(name: str) -> Provenance | None`.
- SQL: `skill_mutation_operations` (sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id UNIQUE, home_id, action/name/actor/created_at/identity_json/sha256/size_bytes/reverts FK); `skill_mutation_events` (sequence PK, operation_id FK, state); `skill_mutation_contents` (sha256 PK, text, size_bytes). No CASCADE delete. Todas canônicas; índices home/name/sequence e eventos por operação/sequence.
- prepare insere operação+conteúdo verificado+primeiro prepared atomicamente; draft de rollback aponta para create committed do mesmo nome/home/identity/hash. finish valida transições e acrescenta evento numa transação; evento terminal repetido igual retorna record sem duplicação, diferente recusa. UNIQUE/FK/checks e triggers de append-only nas três tabelas; aplicação valida dados mesmo se alguém adulterar o banco contornando triggers.
- Proveniência calculada pelo ledger, sem tabela mutável extra. Última operação committed de nome determina criação corrente; rollback remove projeção. None somente ausência de criação corrente válida; origem inválida não vira None. prepared/conflict bloqueiam prepare novo do mesmo nome, sem bloquear outro nome. Código não usa OR REPLACE.

- [ ] **Step 1: Escrever RED com SQLite real.** `test_prepare_e_finish_atomicos` observa prepared + blob e depois committed sem apagar primeiro evento; `test_rollback_vinculado_e_idempotente` exige parent create e nenhum segundo committed no finish repetido. `test_illegal_transitions` cobre prepared→terminal, conflict→terminal, terminal→novoestado recusado. `test_corrupt_record_recusa` adulterações em size/hash/content/identity/home/actor/evento/FK após desabilitar proteção apenas na fixture de corrupção; nenhum get/content/provenance autoriza. `test_append_only` UPDATE/DELETE reais falham e rows permanecem.

```python
record = repository.prepare(draft, creation.text)
assert record.state is SkillMutationState.PREPARED
repository.finish(record.operation_id, SkillMutationState.COMMITTED)
assert repository.content(record.operation_id).text == creation.text
assert [
    row[0]
    for row in connection.execute(
        "SELECT state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence",
        (record.operation_id,),
    )
] == ["prepared", "committed"]
```
- [ ] **Step 2: Run RED.** `uv run pytest -q tests/test_skill_mutation_state.py`; esperado contrato ausente.
- [ ] **Step 3: Implementar schema/repositório/migração.** Acrescentar v7, atualizar SCHEMA_VERSION e registrar três tabelas canônicas; não congelar versão literal em teste. Reusar write_with_retry(Budget.TRANSCRIPT) só em transações SQLite, nunca repetir rename dentro do retry. history desc por sequence, limite validado por tipo exato, sem conteúdo nos records; conteúdo validado por length(CAST(... AS BLOB)) antes do SELECT completo. Append-only não significa autenticidade criptográfica contra dono que reescreve o banco.
- [ ] **Step 4: Escrever testes de reopen/migração/backup/reparo.** `test_reopen_preserva_origem_e_eventos`, `test_migration_twice_preserva_transcript_catalogo_selecao`, `test_repair_preserva_ledger`, `test_sqlite_backup_preserva_conteudo`; comparar linhas/bytes/relacionamentos reais, não número congelado de tabelas. `test_other_home_recusa` abre banco com home_id distinto; `test_unknown_provenance_nao_se_torna_autonoma` distingue ausência de corrupção.
- [ ] **Step 5: Run GREEN.** `uv run pytest -q tests/test_skill_mutation_state.py tests/test_skill_catalog_state.py tests/test_schema.py tests/test_state.py`; Ruff check/format; esperado zero falhas. Exercitar pacote base real em checkout/arquivo temporário contra banco migrado, sem mudar HEAD: seleção/transcript intactos, novas tabelas preservadas. Commit `feat(state): persiste ledger reversível de autoria de skills`.

## Task 3: Criação e recuperação de publicação

**Files:** Create `kairos_skills/mutation_recovery.py`, `mutations.py`; Test `tests/test_skill_mutation_service.py`, `tests/test_skill_mutation_recovery.py`.

**Interfaces:**
- Consumes read_skill_creation, skill_mutation_lock, SkillMutationFiles e SkillMutationRepository definidos acima.
- Produces `SkillMutationService(home: Path, repository: SkillMutationRepository)`; `add(source: Path, *, actor: Actor) -> SkillMutationRecord`, `history(*, name: str | None = None, limit: int = 20) -> tuple[SkillMutationRecord, ...]`, `ownership(name: str) -> Provenance`. Task 4 acrescenta rollback.
- Produces `reconcile_pending(repository: SkillMutationRepository, files: SkillMutationFiles, *, name: str) -> tuple[SkillMutationRecord, ...]`; só sob lock do chamador, limitado a pendências do nome alvo; conflitos em outros nomes não impedem trabalho. Reconciliação aceita create e, após Task 4, rollback.
- history/propriedade não publicam nem reconciliam filesystem. ownership devolve USER para ausência real e recusa corrupção. Construtor não muda arquivos; checar home_id do repositório antes de escrever.

- [ ] **Step 1: Escrever RED da criação real.** `test_add_publica_registra_e_preserva_source`: source fora do home válido, destino bytes iguais, record committed/USER, arquivo original intocado, author ausente permanece ausente. `test_existing_name_tombstone_invalid_no_effect` cobre arquivo/vazio/link/skill/manifesto com nome apagado, source inválida e actor CURATOR/BACKGROUND_REVIEW/string; zero publicação e nenhuma operação de criação. `test_content_metadata_nao_escolhe_origem` usa metadata com origem autônoma e env/git/login fictícios: resultado USER.

```python
record = service.add(source, actor=Actor.USER_FOREGROUND)
assert (home / "skills" / record.name / "SKILL.md").read_bytes() == source.read_bytes()
assert record.state is SkillMutationState.COMMITTED
assert service.ownership(record.name) is Provenance.USER
```
- [ ] **Step 2: Run RED.** `uv run pytest -q tests/test_skill_mutation_service.py`; esperado serviço ausente.
- [ ] **Step 3: Implementar add/history/ownership.** Validar ator/fonte antes de I/O no destino; sob lock reconciliar nome, assert_name_available, stage, prepare, publish, inspecionar identity/creation, finish committed. Gerar UUID no serviço. Antes de publication, limpar só staging próprio comprovado e finalizar aborted quando possível. Após efeito, falha de DB/fsync/inspeção nunca apaga destino: retornar erro com ID e deixar journal recuperável. Nenhum bloqueio de catálogo/read_file novo.
- [ ] **Step 4: Escrever RED de recovery e concorrência.** `test_death_after_prepare` mata subprocesso controlado após prepared e antes de publish; novo serviço identifica staging próprio e aborta sem promover. `test_death_after_rename` mata após rename real e antes de confirmação; reopen verifica destino próprio, fsync e confirma uma vez. `test_fail_after_commit` injeta erro depois de commit real: get/recovery provam committed sem duplicação. `test_db_busy_after_publish` segura lock SQLite em outro processo: erro com ID, arquivo preservado; soltar banco e reconciliar. `test_identical_foreign_replacement` recria diretório com mesmos bytes/inodes distintos: conflict, não committed. `test_unknown_staging_no_promotion`, `test_pending_other_name_not_blocking`, `test_two_processes_one_winner` usam barriers/Pipes reais, sem sleeps como prova.
- [ ] **Step 5: Implementar reconciliação create.** Matriz: staging próprio intacto + instalado ausente ⇒ aborted e limpeza restrita; instalado próprio intacto + staging ausente ⇒ fsync e committed; ambos ausentes ⇒ aborted; qualquer versão divergente, identidade incoerente ou ambos presentes ⇒ conflict e preservação; leitura insegura/I/O ⇒ erro sem assumir ausência. Evento conflict repetido não cresce sem fato novo. Slots desconhecidos não entram na matriz. Testes interrompem fronteiras de stage/prepare/publish/finish com wrappers que executam a operação real antes de sinalizar/morrer.
- [ ] **Step 6: Run GREEN.** `uv run pytest -q tests/test_skill_mutation_service.py tests/test_skill_mutation_recovery.py tests/test_skill_mutation_io.py tests/test_skill_mutation_state.py`; Ruff check/format; esperado zero falhas. Commit `feat(skills): cria com proveniência e recupera publicação interrompida`.

## Task 4: Rollback sem perda de edição posterior

**Files:** Modify `kairos_skills/mutations.py`, `mutation_recovery.py`; Test `tests/test_skill_mutation_rollback.py`.

**Interfaces:**
- Consumes SkillMutationFiles.retire/restore/inspect, repository get/content/latest_rollback/finish e reconciliação da Task 3.
- Produces `SkillMutationService.rollback(create_id: str, *, actor: Actor) -> SkillMutationRecord`.
- Repetição de rollback committed devolve o mesmo record mesmo quando o nome já recebeu outra criação: não tocar a nova entrada. Rollback prepared/conflict é reconciliado antes de decidir repetição; aborted permite nova tentativa com outro ID.

- [ ] **Step 1: Escrever RED de rollback intacto/idempotente.** `test_rollback_retires_and_links_evidence` exige destino ausente, texto/identity original em retired/rollbackID, create committed preservado, rollback committed reverts=createID, blob recuperável e propriedade projetada ausente. `test_repeat_does_not_touch_new_creation` reverte, cria outra skill do mesmo nome e repete ID antigo: record de rollback idêntico, nova skill intacta, events sem duplicação. `test_invalid_id_actor_parent_home_recusa` cobre ID hostil, rollback como parent, create não committed, outrohome e actor autônomo.

```python
rollback = service.rollback(created.operation_id, actor=Actor.USER_FOREGROUND)
assert rollback.reverts == created.operation_id
assert not (home / "skills" / created.name).exists()
assert repository.content(created.operation_id).text == original
assert service.rollback(created.operation_id, actor=Actor.USER_FOREGROUND) == rollback
```
- [ ] **Step 2: Run RED.** `uv run pytest -q tests/test_skill_mutation_rollback.py`; esperado rollback ausente.
- [ ] **Step 3: Implementar rollback.** Obter/revalidar create sob lock; reconciliar nome; latest rollback committed ⇒ devolver evidência sem filesystem effects. Inspecionar nome e exigir identity/text/inventário originais; prepare draft rollback com mesma identity/hash e reverts. Retire, revalidar retired, finish committed. Não apagar conteúdo retirado nem usar blob para sobrescrever arquivo atual.
- [ ] **Step 4: Escrever RED de editor/conflitos/restauração.** `test_edited_added_link_replaced_missing_preserved` cobre edição, arquivo extra, link, diretório refeito byte-idêntico e remoção externa. `test_editor_after_retire_restores_changed_version` altera SKILL em retired com FD real aberto e exige devolução da edição ao nome ausente, erro e operação aborted. `test_editor_and_occupied_original_preserves_both` ocupa nome com versão distinta antes de restore: retained intacto, instalado distinto intacto, conflict e erro com ID. `test_restore_db_over_changed_files_recusa` backup do SQLite + mudança real no filesystem, restauração do banco não reverte versão atual. `test_corrupted_hash_parent_event_recusa` valida erro antes de retire.
- [ ] **Step 5: Escrever RED de morte/recuperação rollback.** `test_death_before_retire_aborts_intact` conserva instalação e aborta prepared sem retired; `test_death_after_retire_commits_once` reabre com retired próprio original e instalado ausente ⇒ committed único. `test_death_during_restore_preserves_changed_data` verifica versão modificada em instalado ou retained, nunca exclusão. `test_retired_replaced_or_name_occupied_conflicts` evita interpretar cópia idêntica ou duas versões como reversão concluída. Recovery não remove/repõe versão externa. Conflito resolvido só transiciona com fatos verificados; retorno idempotente committed dispensa revalidar conteúdo pós-conclusão, que o usuário pode ter editado depois.
- [ ] **Step 6: Implementar matrix rollback e Run GREEN.** Originais intactos instalado-only ⇒ aborted; retired-only original + instalado ausente ⇒ fsync e committed; demais ⇒ conflito/preservação, tentando restore sem substituição somente do diretório que o rollback acabou de retirar. Se restore devolveu versão editada com mesma identity ⇒ aborted, não committed. `uv run pytest -q tests/test_skill_mutation_rollback.py tests/test_skill_mutation_recovery.py tests/test_skill_mutation_service.py tests/test_skill_mutation_state.py`; Ruff check/format; esperado zero falhas. Commit `feat(skills): reverte criação sem apagar alterações do usuário`.

## Task 5: CLI real, lock do sync, docs e entrega

**Files:** Create `kairos_cli/skill_mutations.py`; Modify `kairos_cli/main.py`, `commands.py`, `handlers.py`, `kairos_skills/sync.py`, `docs/guia-cli-comandos.md`, `docs/decisoes.md`, `docs/cli-auth-progress.md`, `kairos.egg-info/SOURCES.txt`; Test `tests/test_cli_skill_mutations.py`, `tests/test_skill_mutation_sync.py`; preservar suites de CLI/catalog/sync/credenciais.

**Interfaces:**
- Consumes service/repository/lock e records anteriores.
- Produces `run_skill_mutation(home: Path, args: argparse.Namespace) -> int` e helper `mutation_metadata(record: SkillMutationRecord) -> dict[str, object]` no módulo CLI. Metadata exata: id, name, action, state, origin, sha256, created_at, reverts; origin=user, não author.
- Compor SQLite/migrate/repository/service por comando com fechamento determinístico. Validar opções/nome/ID antes da composição; validar home e entrada state.db sem links/hardlinks antes de connect padrão. history não inicia reconciliação nem cria skills/área privada; banco sem ledger conhecido produz histórico vazio por leitura controlada, sem false-success de mutação.
- Add requer --file (não `-`), history --name/--limit, rollback positional ID; status de folhas add/history/rollback IMPLEMENTED; não anunciar install/remove/tap como implementados por este recorte. args JSON segue formato da CLI: add/rollback objeto metadata, history lista; avisos safe em stderr.
- Sync adquire skill_mutation_lock(user_dir.parent) somente na fase de mutação, após snapshot da fonte e retornos sem efeitos como opt-out/bundle ausente. Mesma chave home para instalações reais `<home>/skills`; reusar `_sync_discovered` para não relockar. Nenhuma operação SQL dentro do sync.

- [ ] **Step 1: Escrever RED da CLI + sync.** `test_cli_add_history_rollback_real` parametriza humano/JSON com main() real, source e SQLite reais; asserts bytes/ID/parent/origem e stdin.tell()==0. `test_options_exit_codes_privacy` valida arquivo/ID/nome/limite inválidos (2), conflito/I/O/corrupção (1), recurso indisponível (69), sem corpo/autor/path absoluto; limites 1/100/0/101 e vazio[]. `test_history_does_not_reconcile` deixa prepared e comprova eventos/filesystem iguais após comando. `test_sync_waits_for_mutation_and_preserves_local` usa processo segurando lock + marker de início da cópia real: sem mutação até release, criação local preservada, manifesto sem origem falsa. `test_sync_timeout_private_error_and_opt_out` cobre timeout, lock link, marcador opt-out e os três resultados atuais de sync.

```python
assert main(["skills", "add", "--file", str(source), "--json"]) == 0
created = json.loads(capsys.readouterr().out)
assert created["origin"] == "user" and created["state"] == "committed"
assert main(["skills", "rollback", created["id"], "--json"]) == 0
assert stdin.tell() == 0
```
- [ ] **Step 2: Run RED.** `uv run pytest -q tests/test_cli_skill_mutations.py tests/test_skill_mutation_sync.py`; esperado CLI/lock ainda ausentes, não adapter sem configuração.
- [ ] **Step 3: Implementar parser/handlers/sync.** Handler skills existente delega só folhas novas ao módulo específico; Error.kind→code traduzido sem backend repr. Lock shared participa do protocolo sem mudar schema/model_config/credenciais; close SQLite em finally. Não reutilizar conexão serviço async da CLI de Chat para mutações síncronas.
- [ ] **Step 4: Escrever RED e implementar consumo real.** `test_created_skill_reaches_adapter` usa composição/SQLite/lease real e adapter local fictício no boundary provider: criar CLI, executar --skill, ler por skill_view em catálogo de sessão nova, comparar texto literal. `test_existing_catalog_and_cached_page_survive_rollback` fixa sessão A/cache, add segunda skill, sessão B anuncia as duas, rollback segunda, A mantém índice/página original, sessão C não anuncia retirada; arquivo/ledger reais. `test_no_inherited_autonomous_origin` chama add em ContextVar/env de origem autônoma e confirma USER; não mockar guard de ownership/workspace para esconder o caminho.
- [ ] **Step 5: Docs e GREEN.** Documentar comandos, fronteira manual, fonte única, versão semver na criação versus leitura legada, conflitos/IDs e diretórios privados relativos, retenção integral no SQLite, diferença de backup de banco versus arquivos (o tar backup atual não é snapshot atômico), limitação de editores externos e mounts distintos. Registrar divergência ledger SQLite, status das entregas sem prometer `/learn`. Atualizar SOURCES sem mudar pins/deps nem reordenar entradas existentes. `uv run pytest -q tests/test_cli_skill_mutations.py tests/test_skill_mutation_sync.py tests/test_cli_skill_catalog.py tests/test_cli_skills.py tests/test_skill_sync_categories.py tests/test_skills.py tests/test_active_credentials.py`; Ruff check/format e git diff --check; esperado zero falhas. Commit `feat(cli): cria skills e expõe histórico e rollback`.
- [ ] **Step 6: Revisão final independente.** Método nativo preservado: nenhuma delegação por tarefa; uma revisão fresh do branch completo no modelo mais capaz conforme executing-plans/requesting-code-review. Entregar spec/plan/diff, Review Focus literal e ledger de rulings. Graduar achados por efeito: Critical/Important fixados RED→GREEN, minors registrados. CI é evidência separada do relato do revisor.
- [ ] **Step 7: Gates completos e merge.** `scripts/ci.sh` inteiro com Codex pinado no PATH; construir stub/worker/broker e executar opt-ins Docker offline da workflow; logs/exit/skip/deselect registrados. Nenhum runtime_live. Push/PR título português conventional; nove verificações remotas success no exato HEAD antes do squash. Fetch/diff pós-merge: árvore corresponde à revisada, sem remoções acidentais. Acompanhar dez jobs main, incluindo Komodo autorizado.
- [ ] **Step 8: Smoke instalado e evidências.** Na imagem e container atualizado: home/source/banco temporários, CLI add/history/rollback reais e adapter fictício comprovando texto consumido; reinício/ledger, original modificado recusado, conflito preservado, catálogo cacheado e nova sessão. HTTP health e hashes de módulos/squash reais. Nenhuma credencial/conversa real acessada. Registrar PR, commits, runs, logs e smoke; preservar checkout original e relatar todas as rulings/custos.

## Auto-revisão e handoff

Cobertura: formato/containment/lock/publicação → Task 1; ledger/origem/migração/
backup/reparo → Task 2; criação/recovery/concorrência → Task 3; rollback/editor/
recovery/restauração divergente → Task 4; CLI/sync/consumo/docs/gates/deploy →
Task 5. Cada Review Focus tem teste explícito na tarefa responsável.

Interdependências: Task 1 records e Files → Task 2 drafts/repository; Task 1/2
→ Task 3 service/recovery; Task 3 → Task 4 rollback; todas → Task 5 CLI/sync.
Nenhuma ferramenta de modelo ou provider novo. As matrizes distinguem ausência
de I/O, versão própria de cópia idêntica, confirmação de estado de suposição.

Status: especificação e plano aprovados pela usuária; execução nativa autorizada
em 2026-10-09. Método preservado das etapas anteriores, com
revisão independente única no final. A aprovação deste plano autoriza iniciar
as cinco tarefas pelo método preservado; não requer aprovações entre tarefas.
