# CLI skills adopt/unadopt — plano de implementação A2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir administração local explícita e revogável de uma instalação legada, preservando arquivos e impedindo que restauração ressuscite autoridade revogada.

**Architecture:** Acrescentar uma família append-only de adoção/revogação ao SQLite canônico, com ordenação global e projeção única de instalação, linhagem e vigência. Executar escritores sob o lock comum, compartilhando captura por descritores, snapshots e recuperação existentes; liberar a CLI apenas após integrar unadopt, remove/restore e recuperação.

**Tech Stack:** Python 3.11, SQLite, unittest/pytest, descritores POSIX e Docker; sem dependências novas.

**Spec:** [Fatia A](../specs/2026-10-10-proveniencia-skills-design.md), somente A2; D-CLI.SKILL.PROVENANCE em [decisões](../../decisoes.md). A [fatia B](../specs/2026-10-11-politica-publicacao-skills-design.md) permanece fora deste plano.

**Status:** plano aprovado para implementação nesta sessão pelo usuário, após a revisão e os ajustes documentados. Merge e operação em homes de produção não foram autorizados por essa aprovação.

## Gate de entrada e correções do resumo

A1 passou pelo gate: implementação e revisões entregues no PR #102, squash `33b6e7705aed7a6d847e55251dc8ba4d0e74ac41`, suíte integral local e CI verdes. O registro está em `docs/decisoes.md`, seção D-CLI.SKILL.PROVENANCE, status A1. Base documental deste plano: main `bdd95025b50ab4a8391f1ef0a8f9eb31140e273e`. Antes da execução, conferir alterações posteriores e adaptar os pontos afetados, sem presumir evidência de teste para código A2 ainda inexistente.

A instrução final da spec original de aprovar somente A1 descrevia o gate de entrada daquela entrega. Esse gate foi cumprido; agora se propõe exclusivamente A2, com aprovação e revisão próprias. Aprovar A2 não aprova B1/B2 nem seus planos.

Correção de B1, apenas para delimitar escopo: **fonte configurada ausente/incompatível recusa; configuração explicitamente sem política é permitida, com proteção de namespace/evidências disponíveis e cobertura limitada declarada**. Erro de leitura não significa ausência de organização. Essa política não será implementada em A2.

Superfície completa de A2:

```bash
kairos skills adopt NOME --yes --json
kairos skills unadopt NOME --yes --json
kairos skills unadopt NOME --expect-adoption ID --yes --json
```

`--json` seleciona saída estruturada; `--yes` é confirmação obrigatória. `--expect-adoption` compara a adoção fundadora corrente sob lock: esperando A quando a corrente é B, recusar preservando B. A variante sem comparação também captura e revalida a mesma autoridade, sem escolher outra durante a operação.

**Mapeamento explícito:** mismatch de `--expect-adoption` ou mudança de I/A/raiz entre resolução e commit é `SkillMutationError(kind="conflict")`, exit **1**, sem evento U nem revogação. É falha da precondição de comparação, não veto de autorização. Exit **77** continua reservado a `kind="denied"` (confirmação, ator ou veto conhecido negados); ID/nome malformado é `kind="input"`, exit **2**. Isso especifica a interpretação de conflito da spec A e acompanha o mapeamento existente em `kairos_cli/skill_mutations.py`; T2 testa o kind e T4 testa o exit em subprocesso.

## Global Constraints

- “Foreground e confirmação são verificados antes de abrir home/banco/criar lock.” Os mesmos guards pertencem ao serviço para chamadas diretas.
- “Uma concessão USER/BUNDLED independente e vigente não muda por adopt: devolver a prova existente, sem novo ativador.” Unadopt recusa concessão independente, sem revogá-la.
- “Revogação altera somente autoridade: conserva bytes, modos, snapshots, IDs anteriores e tombstones.”
- “Rollback de A/U recusa e indica unadopt/adopt; não reverte arquivos.”
- Adoção não inventa create, autoria histórica, SEDIMENT ou semver de conteúdo legado. Não aceitar origem, ator ou force escolhidos pelo modelo.
- Edição interna conserva a base de adoção; troca externa de raiz, mesmo byte-idêntica, invalida a prova. Novo ato confirmado é necessário para nova adoção.
- Resolver linhagem e vigência antes de autorizar: RESTORE committed não é concessão independente e nunca contorna U.
- Recusar namespace organizacional, proteção histórica comprovada, pendência impeditiva, árvore insegura e cadeia incompleta. A2 não lê política externa nem anuncia guard organizacional completo.
- Migração aditiva, preservando schema de dados das famílias antigas, IDs, sequências, metadata e BLOBs; capability nova deve barrar clientes antigos antes de efeitos em arquivos.
- “Não retroagir sobre remoções USER antigas nem apagar exclusão por adoção, revogação ou nova criação.” Só restore committed encerra a exclusão vinculada.
- Não alterar publicação de sync/quarentena, hub, `/learn`, edição por modelo, administração de organizações, assets ou catálogos de turnos iniciados; nenhuma tool nova.
- Códigos: 2 uso/nome inválido; 77 confirmação/ator/veto conhecido negados; 69 primitiva atestável indisponível; 1 ausência/conflito/corrupção/falha. Sucesso exige efeito committed real ou devolução explícita de prova vigente preexistente em adopt.
- Testes exercitam comportamento real, sem ler fontes nem congelar contagens. Commits em português conventional; suíte integral local, revisão independente e CI verdes antes de merge.

## Review Focus

- Troca A→B entre resolução e commit: unadopt não pode revogar B, com ou sem comparação explícita (T2).
- Restore antigo ou múltiplos restores depois de U: nenhuma publicação descendente pode reativar A (T3).
- Cliente antigo ou backup parcial: recusar antes de retirar/publicar árvore, sem degradar linhagem em concessão independente (T1/T4).
- Raiz trocada byte-idêntica versus edição interna: invalidar a primeira e preservar a segunda; diagnóstico não confunde autoria com administração (T2).
- Bundle futuro oferecendo nome removido de adotada: tombstone bloqueia reaparição, inclusive depois de U/B/create; só restore vinculado committed encerra a exclusão (T3).

## Estrutura, propriedade e execução multiagente

Execução multiagente prevista pela spec A, com implementador responsável e revisões independentes de spec/qualidade por tarefa, mais revisão integrada final. T1→T2→T3→T4 são sequenciais: não despachar implementadores que alterem simultaneamente arquivos comuns. Os nomes abaixo são papéis futuros, não agentes já criados. Root integra e verifica evidências; revisores não implementam correções no mesmo despacho.

| Tarefa / responsável | Propriedade exclusiva durante a tarefa |
|---|---|
| T1 / persistência | Novos `kairos_skills/adoption_contract.py`, `kairos_state/skill_adoptions_schema.py`, `kairos_state/repositories/skill_adoptions.py`; existentes `kairos_state/schema.py`, `kairos_state/migrations.py`, `kairos_state/skill_writer.py`, `kairos_state/repositories/skill_mutations.py`, `kairos_state/repositories/skill_removals.py`, `kairos_skills/removal_contract.py`, `kairos_skills/removal_state.py`, `kairos_skills/identification_contract.py`, `tests/test_skill_removal_state.py`, `tests/test_skill_identification.py` (somente regressão de ledger futuro/parcial), `tests/test_skill_removal_projection.py` (somente referências ausentes/operação órfã na timeline canônica), `tests/test_skill_removal_sync.py` (somente ledger futuro/parcial em fixtures independentes), `scripts/ci.sh`, `.github/workflows/ci.yml`, `tests/test_interaction_turn_ownership.py` (somente intervalo de polling dos dois testes de grafos SQLite concorrentes, mantendo asserts e relógios controlados); novos `tests/test_skill_adoption_state.py`, `scripts/prepare-skill-v8-client.sh` |
| T2 / serviço | Novos `kairos_skills/adoptions.py`, `kairos_skills/adoption_recovery.py`, `tests/test_skill_adoption_service.py`; existentes `kairos_skills/identification.py`, `kairos_skills/identification_contract.py`, `kairos_skills/removal_origin.py`; transferido de T1 `kairos_state/repositories/skill_adoptions.py` (callback transacional mínimo de revalidação); `tests/test_cli_skill_identification.py` (somente duas expectativas de JSON para A/vigência ausentes) |
| T3 / integração | Existentes `kairos_skills/mutations.py`, `kairos_skills/mutation_recovery.py`, `kairos_skills/removal_recovery.py`; novos `tests/test_skill_adoption_lineage.py`, `tests/test_skill_adoption_recovery.py`, `tests/test_skill_adoption_sync.py`; transferidos de T1 `kairos_state/repositories/skill_mutations.py`, `kairos_skills/removal_contract.py`; de T2 `kairos_skills/adoption_recovery.py` |
| T4 / CLI e aceite | Novos `kairos_cli/skill_adoptions.py`, `tests/test_cli_skill_adoptions.py`, `tests/test_skill_adoption_backup.py`; existentes `kairos_cli/main.py`, `kairos_cli/commands.py`, `kairos_cli/backup.py` (entradas de diretório/modos e, após revisão final I-01, snapshot SQLite consistente no backup público), `kairos_cli/handlers.py` (somente dispatch adopt/unadopt), `kairos_skills/identification.py` (somente fingerprint da cópia privada antes de consultas SQL), `kairos_cli/skill_mutations.py`, `tests/test_container.py`, `tests/test_cli_skill_identification.py` (somente expectativa de help que excluía adopt), `docs/guia-cli-comandos.md`, `docs/decisoes.md` |

Arquivos fora dessa tabela só mudam após Root registrar motivo e transferência de propriedade no plano. `kairos_skills/sync.py` é regressão consumidora dos tombstones, não propriedade de implementação: se o contrato existente não consumir a nova projeção, tratar como divergência de escopo antes de alterar publicação.

`kairos_skills/removal_io.py` é dependência consumida, **sem alteração prevista**. T2 usa `SkillRemovalFiles.capture_installed`, `check` e `sync_parents` existentes; o snapshot de adopt é persistido duravelmente no SQLite por T1, sem staging/publicação/retirada de árvore. T3 usa as primitivas existentes de restore. Root confere o diff por tarefa e final: nenhuma alteração fora da propriedade declarada pode passar sem atualização prévia do plano. Necessidade de nova primitiva de I/O exige registrar justificativa, responsável e testes antes de modificar esse arquivo.

## Decisão de migração: uma única timeline ativa

O CHECK v8 é `(legacy_event IS NULL) <> (tree_event IS NULL)`: adicionar apenas uma coluna `adoption_event` não permite uma linha exclusiva de adoção, pois ambos os campos antigos seriam NULL e o CHECK a recusaria. Permitir a terceira família nessa tabela exigiria substituir o CHECK e reconstruir a tabela legada, contrariando a restrição desta proposta de manter seu schema de dados e suas linhas intactos. Uma tabela dedicada de eventos A/U é necessária, mas sozinha não fornece ordem total entre famílias; mesclar por timestamp, ID ou sequências independentes não preserva a causalidade.

A solução revisada é uma **nova timeline canônica única**, `skill_authority_timeline`, com referências exclusivas a legacy_event, tree_event ou adoption_event e CHECK de exatamente uma referência não NULL. O espelhamento contínuo da versão anterior do plano foi retirado. Na migração transacional, copiar o prefixo v8 conservando sequence e vínculos, validar igualdade integral e configurar os triggers das três famílias para inserir **diretamente e somente** na nova timeline. A timeline v8 fica congelada, sem receber eventos futuros; os triggers append-only continuam protegendo suas linhas e um guard nega novas inserções. Nenhum writer atual usa duas timelines, nem há dupla ordenação ativa. A cópia inicial é histórico preservado, não outra fonte de ordenação corrente.

A fronteira do prefixo importado é gravada em metadata canônica singleton imutável, na mesma transação. Leitura e migração comparam bidirecionalmente sequence e referências do prefixo novo até essa fronteira com a timeline v8 completa; ausência de metadata, lacunas, perda da última linha ou perda integral recusam. O prefixo vazio também é atestado. Não usar sqlite_sequence como prova histórica.

A transação inclui schema novo, importação do prefixo, troca dos triggers de roteamento e guards de capability; falha ou interrupção antes do commit deixa o estado v8 completo. Depois do commit, leitores/escritores novos usam exclusivamente a timeline nova, e clientes antigos são recusados pelos guards antes de efeitos. Próxima sequence supera o prefixo importado; evento e posição global são inseridos atomicamente pelo trigger da família. Reexecutar a migração não copia nem renumera posições. Presença parcial/incompatível recusa; backup/reparo preservam também a timeline v8 congelada.

Aceite específico de T1: eventos intercalados de todas as famílias aparecem uma vez na ordem global; novos eventos não acrescentam linhas à v8; fingerprints do prefixo v8 permanecem idênticos; interrupção da migração reabre em v8 íntegra ou versão nova íntegra, nunca em composição parcial; falha no trigger de ordenação aborta também o evento de origem. São contratos comportamentais com SQLite real, sem leitura de fonte em teste. Essa escolha evita reconstrução de dados antigos e elimina a divergência de espelhamento, ao custo explícito de conservar uma cópia histórica imutável do prefixo.

**Downgrade — aceite obrigatório de `test_old_writer_refused`:** preparar antes da implementação um artefato executável da revisão `bdd95025b50ab4a8391f1ef0a8f9eb31140e273e` (cliente v8), instalado em ambiente isolado com Python 3.11. Registrar revisão, hash do artefato e comando; o subprocesso não pode importar módulos do checkout A2. Contra fixtures cujo DB já foi migrado pelo cliente novo, executar os caminhos públicos antigos de add, remove confirmado e rollback/restore, com alvos/fontes válidos que permitiriam a operação no DB v8. Para cada caminho, exigir saída não zero com o diagnóstico sanitizado público do cliente antigo e comparar inventário completo, bytes, modos, identidades das árvores e conteúdo lógico canônico do DB antes/depois: nenhum movimento/publicação, novo evento ou alteração de registro. Comprovar também a causa por subprocesso separado do mesmo artefato v8, executando abertura/migrate reais contra o DB migrado e observando a recusa de schema/capability: a CLI antiga sanitiza esse erro em mensagem genérica. Executar controles positivos dos mesmos comandos sobre DB v8, sem instrumentar ou substituir o comportamento antigo. Não basta mockar a capability, afirmar somente um kind ou simular o writer antigo por SQL. Cobrir também conexões com capability v8/ausente nos triggers como teste complementar, sem substituir o subprocesso real. Artefato indisponível ou subprocesso usando código novo impede o aceite; não converter em skip/sucesso. O revisor independente T1 confirma a proveniência do executável e as evidências sobre DB já migrado, pois os triggers de roteamento foram substituídos.

**Provisionamento reproduzível:** T1 fornece `scripts/prepare-skill-v8-client.sh` para construir a revisão pinada em fonte isolada via git archive e instalar wheel não editable com Python 3.11/dependências pinadas, registrando SHA/revisão/interpreter em `.superpowers/test-fixtures/skill-client-v8`. Fontes e ambientes gerados ficam sob um componente `.venv`, já excluído pelos scanners de dependências locais; não alterar a auditoria do produto. CI unit e `scripts/ci.sh` chamam o provisionador antes da suíte. Ausência ou falha da fixture impede o gate; não pular o teste nem versionar wheel binário. Pytest direto consome fixture preparada ou caminho explícito de artefato real. Testes não leem código-fonte para afirmar comportamento.

## Tarefa 1: família persistente e projeção de autoridade

**Interfaces:**
- Produzir enum `SkillAdoptionAction` com `ADOPT='adopt'`, `UNADOPT='unadopt'`; dataclasses frozen `SkillAdoptionDraft` e `SkillAdoptionRecord`, seguindo validação/estados existentes. Draft contém operation_id, home_id, action, name, actor, created_at, adoption_id, installation_id, device, inode, installation_source e snapshot_id. ADOPT usa seu próprio ID como adoption_id e, para legado sem publicação comprovada, installation_id; U referencia A e I capturados, sem novo snapshot.
- Produzir `AdoptionAuthority(adoption_id: str, installation_id: str, active: bool)`; acrescentar `adoption: AdoptionAuthority | None = None` a `InstallationEvidence` e `InstallationProof`, preservando construção e leitura dos registros antigos.
- Produzir dataclass frozen `SkillAdoptionResult(record: CommonRecord | None, identification: SkillIdentification)` para adopt: record é a operação existente/nova quando houver; prova independente de bundle sem evento permanece somente no relatório, sem inventar ID ou create. Referências de tipos não devem criar ciclo de imports em runtime.
- Produzir `SkillAdoptionRepository.prepare_adopt(draft: SkillAdoptionDraft, snapshot: TreeCapture) -> SkillAdoptionRecord` e `commit_unadopt(draft: SkillAdoptionDraft) -> SkillAdoptionRecord`; atomicidade entre evento e ordenação global.
- Integrar CommonRecord, get/history/pending/finish/snapshot e installation_evidence no repositório comum; não criar segundo algoritmo de autorização ou consultar history truncado para resolver autoridade.

- [x] **1. RED:** criar `tests/test_skill_adoption_state.py` com `test_migration_preserves_old_records_and_blobs`, `test_global_event_order`, `test_v8_timeline_frozen_after_migration`, `test_migration_interruption_is_atomic`, `test_event_and_global_position_atomic`, `test_old_writer_refused`, `test_adoption_revocation_projection`, `test_corrupt_or_missing_lineage_refused`, `test_all_family_pending_blocks_writer`. Usar SQLite/serviços reais; comparar fingerprints canônicos e BLOBs antigos antes/depois, não contagens fixas de schema.
- [x] **2. Executar:** `uv run pytest -q tests/test_skill_adoption_state.py`; confirmar falhas pelo contrato ausente.
- [x] **3. Implementar persistência aditiva.** Acrescentar tabelas de operações/eventos A/U e vínculos de adoção de remove/restore, reutilizando snapshots/BLOBs binários. Implementar `skill_authority_timeline` conforme a decisão de migração acima: prefixo importado imutável, v8 congelada, triggers das três famílias roteados somente à timeline nova e migração atômica/idempotente. Exigir um único evento referenciado por posição; não reimportar em migração repetida. Capability e guards de escrita passam a reconhecer a nova família; preservar triggers append-only e negar writer antigo em todas as famílias. Incluir novas tabelas no fingerprint/reparo canônico. Leitor privado aceita as versões antigas suportadas e a nova completa, recusando presença parcial/incompatível sem migrar.
- [x] **4. Implementar projeção única.** A committed ativa autoridade da instalação; U committed revoga A e descendentes, conservando instalação presente. Concessões independentes permanecem independentes. Vínculos incompletos/corruptos recusam; operações prepared não autorizam. `installation_evidence` expõe A/I/vigência mesmo depois de U. Snapshot de U é o de A referenciado, imutável, sem capturar ou substituir bytes.
- [x] **5. GREEN/regressão:** `uv run pytest -q tests/test_skill_adoption_state.py tests/test_skill_mutation_state.py tests/test_skill_removal_state.py tests/test_skill_identification_evidence.py tests/test_state.py`; todos passam. Repetir migração e verificar fingerprints preservados e recusas reais do cliente com capability antiga antes de efeitos.
- [x] **6. Commit/revisão:** `feat(skills): registra adoção e revogação com linhagem comum`. Revisor independente aprova contrato e migração antes de T2.

## Tarefa 2: vertical interna identify/adopt e revogação sob lock

**Interfaces:**
- Produzir `SkillAdoptionService(home: Path, repository: SkillMutationRepository)` com `adopt(name: str, *, actor: Actor, confirmed: bool = False) -> SkillAdoptionResult` e `unadopt(name: str, *, actor: Actor, confirmed: bool = False, expect_adoption: str | None = None) -> SkillAdoptionRecord`. Concessão independente é descrita como prova preexistente; resultado sem evento não recebe ID fictício.
- Produzir `reconcile_adoption_operation(repository: SkillMutationRepository, files: SkillRemovalFiles, record: SkillAdoptionRecord) -> SkillAdoptionRecord`, executado somente sob lock pelo reconciliador comum de T3.
- Acrescentar `adoption_id: str | None` e `adoption_active: bool | None` ao relatório identify; manter current_installation_id como I. Adoção válida usa `authorization_basis='user_adoption'`, ownership USER e conserva historical_creation_origin/source anteriores; após U, base none/removivel false, mantendo A/I e active false.

- [x] **1. RED:** testar no serviço real `test_denied_before_home_database_lock`, `test_adopt_preserves_bytes_modes_and_unknown_creation`, `test_repeat_returns_A_without_new_activation`, `test_independent_USER_BUNDLED_unchanged`, `test_direct_calls_enforce_guards`, `test_internal_edit_vs_replaced_root`, `test_unadopt_preserves_every_file_and_snapshot`, `test_expected_A_cannot_revoke_B`, `test_change_before_commit_refused_without_expectation`. Guard tests usam homes ausentes e atores negados; filesystem real para inventários/modos/identidades; injetar a corrida na fronteira existente de persistência, não substituir a resolução por mock permissivo.
- [x] **2. Executar:** `uv run pytest -q tests/test_skill_adoption_service.py`; confirmar RED.
- [x] **3. Implementar adopt.** Validar ator/confirmação/nome antes de I/O; tomar lock comum, verificar pendências/vetos e capturar por descritores. Devolver prova independente ou A vigente sem ativador novo; caso contrário preparar prova e snapshot duráveis, revalidar raiz/inventário/guards e committed. Não exigir semver legado nem inferir origem a partir do frontmatter. Raiz substituída exige nova adoção confirmada, sem reutilizar A.
- [x] **4. Implementar unadopt.** Validar ID esperado antes de I/O. Dentro do lock, resolver uma vez e capturar home/nome/I/A/device/inode/linhagem; comparar expect_adoption e revalidar o mesmo conjunto antes da transação U. Mismatch ou mudança durante a operação levanta `SkillMutationError(kind="conflict")`; os testes de comparação e corrida afirmam esse kind. Nunca trocar para autoridade recém-observada. Ausência, concessão independente, A revogada, raiz trocada, linhagem incompleta ou pendência recusam sem evento U nem movimento de arquivos. Sem instalação presente, não revogar por esta interface.
- [x] **5. Implementar recuperação A e identify.** Íntegra/autorizada conclui A; instalação ausente aborta sem reconstrução; divergência registra conflito preservando provas; veto novo aborta sem alterar arquivos; atestação indisponível mantém prepared. U é uma transação de journal atômica. identify continua somente leitura, com diagnóstico sanitizado e cobertura limitada.
- [x] **6. GREEN e marco interno:** `uv run pytest -q tests/test_skill_adoption_service.py tests/test_skill_identification.py tests/test_skill_identification_evidence.py`; todos passam. Revisar independentemente a vertical identify+adopt em fixtures isoladas. **Não registrar comandos CLI nem liberar adopt em produção neste marco**; nenhuma autoridade dormente em home real para ativação posterior.
- [x] **7. Commit/revisão:** `feat(skills): adota e revoga administração local sob lock`. Só iniciar T3 com contratos aprovados.

## Tarefa 3: remove/restore, exclusões de sync e recuperação cruzada

**Interfaces:** consumir A/I/vigência e serviço T2. `SkillMutationService.remove`, `rollback` e restauração continuam com suas assinaturas públicas; registros novos de remove/restore carregam vínculo imutável a A/source e exclusão local. `bundled_tombstones()` passa a retornar também exclusões de adotadas comprovadas, conservando a interface consumida pelo sync; não reinterpretar remoções USER antigas.

- [x] **1. RED de linhagem:** `test_A_M_R_U_remove_denied`, `test_multiple_restores_do_not_escape_U`, `test_B_after_U_requires_confirmation`, `test_expected_A_preserves_B`, `test_independent_grant_not_revoked`, `test_rollback_A_U_points_to_inverse_without_file_changes`. A→M→R comprova nova raiz I1 mantendo A; U de I1 impede remove e restore de qualquer snapshot descendente de A. Readoção B não altera A/U antigos; restore derivado revogado não bloqueia B por falsa concessão independente.
- [x] **2. RED de tombstones:** `test_adopted_removal_blocks_future_bundle`, `test_U_B_create_do_not_clear_local_exclusion`, `test_only_linked_committed_restore_clears_exclusion`, `test_old_USER_removals_unchanged`. Executar sync existente contra bundle temporário real contendo posteriormente esse nome; confirmar ausência de reaparição.
- [x] **3. Executar:** `uv run pytest -q tests/test_skill_adoption_lineage.py tests/test_skill_adoption_sync.py`; confirmar RED.
- [x] **4. Implementar integração.** Resolver vigência antes de autorização e gravar A/source no snapshot/vínculo novo. Restore transporta A para nova identidade, não cria USER independente; verificar revogação e vetos também para prepared e recuperação. Rollback de A/U recusa com orientação unadopt/adopt. Todos os escritores consultam pendências comuns; reconciliar somente IDs conhecidos e autorizados, sem limpar órfãos desconhecidos nem contornar conflito.
- [x] **5. RED de recuperação real:** `test_SIGKILL_adoption_durable_boundaries`, `test_SIGKILL_restore_preserves_adoption_link`, `test_fsync_failure_never_reports_success`, `test_cross_family_pending_blocks_writers`, `test_revoked_or_protected_incomplete_restore_preserves_versions`. Subprocessos param antes/depois do snapshot durável, prepared, revalidação, publicação restore e commit; SIGKILL real; confirmar estados/IDs/hashes/modos após reabertura. Falhas fsync geram erro explícito/pending conforme fronteira, nunca sucesso. Restaurar backup de estado prepared para exercer U/veto antes da conclusão, sem adulterar linhas append-only.
- [x] **6. Completar reconciliador comum.** Despachar família A/U para recuperação T2; restore inconcluso com U/veto não conclui autoridade nem retira automaticamente árvore divergente/protegida. Fonte/primitiva indisponível conserva pendência. Comparar estado observado com provas e conservar versões em conflito.
- [x] **7. GREEN/regressão:** `uv run pytest -q tests/test_skill_adoption_lineage.py tests/test_skill_adoption_sync.py tests/test_skill_adoption_recovery.py tests/test_skill_removal_service.py tests/test_skill_removal_recovery.py tests/test_skill_mutation_rollback.py tests/test_skills.py`; todos passam, sem skips nos casos de SIGKILL que o ambiente deve suportar.
- [x] **8. Commit/revisão:** `feat(skills): preserva revogação em remoção restauração e recuperação`. Revisão independente de linhagem/kill tests antes de liberar CLI.

## Tarefa 4: CLI completa, backup e aceite integrado

**Interfaces:** produzir `run_skill_adoption(home: Path, args: argparse.Namespace) -> int`, consumindo serviço T2 e conexão canônica. Parser registra adopt/unadopt com NOME, --yes, --json; somente unadopt aceita --expect-adoption ID. Metadata inclui IDs de operação, instalação e adoção, ação/estado, linhagem/vigência e três dimensões; não imprimir snapshot, conteúdo, credenciais ou caminhos privados. history/get/pending expõem todas as famílias pela ordenação global; history não altera vigência histórica dos eventos.

- [x] **1. RED CLI:** `test_all_three_command_forms`, `test_missing_yes_invalid_name_or_id_before_IO`, `test_json_current_ids_lineage_and_validity`, `test_expected_A_B_exit_1_without_revocation`, `test_sanitized_errors_and_exit_codes`, `test_history_all_families_global_order`, `test_rollback_adopt_unadopt_refuses`. Subprocessos CLI reais; home ausente permanece ausente na recusa; segredo no conteúdo nunca aparece no stdout/stderr de diagnóstico. Testar também formato humano e rejeição de force/origem/ator arbitrários.
- [x] **2. Executar:** `uv run pytest -q tests/test_cli_skill_adoptions.py`; confirmar RED; implementar parser, dispatch e metadata. Documentar --expect-adoption como guard de comparação, a cobertura limitada e códigos de saída.
- [x] **3. RED/implementação de backup:** `test_backup_restore_preserves_A_U_lineage_and_binary_snapshots`, `test_repair_preserves_all_canonical_rows`, `test_partial_backup_refused_without_authority`. Fazer backup SQLite consistente e backup de home pelo caminho público existente; restaurar em fixture isolada conservando binding do home canônico e provar hashes, BLOBs, IDs, tombstones e U vigente. Restore para outro home não transplanta autoridade por nome. Incluir prepared/committed/conflict e exigir recusa conservadora quando DB e filesystem não correspondem; não inferir concessão para reparar divergência.
- [x] **4. Imagem real:** acrescentar a `RealImageTests` exercício CLI de A→M→R→U→remove recusado e B/comparação A, mais subprocesso interrompido nas fronteiras reais de adoção/restore. Construir `docker build -t kairos:test .` e executar `uv run pytest -q tests/test_container.py::RealImageTests`; nenhum skip na cobertura nova exigida.
- [x] **5. GREEN focal:** `uv run pytest -q tests/test_cli_skill_adoptions.py tests/test_skill_adoption_backup.py tests/test_cli_skill_mutations.py tests/test_cli_skill_removal.py tests/test_cli_skill_identification.py tests/test_state.py`; todos passam. Verificar sanitização, invariantes de backup e ausência de efeitos nas recusas.
- [x] **6. Aceite integrado Root:** executar `scripts/ci.sh` completo, sem --fast, com Docker e dependências disponíveis. Conferir que nenhuma etapa exigida foi pulada; registrar comando/ambiente/revisão/resultado. Rodar opt-ins pertinentes à superfície de recuperação na imagem; não acionar runtime_live sem credenciais. Reexecutar checks afetados por correções; reutilizar somente evidência vinculada ao estado final.
- [x] **7. Commit/revisão final:** `feat(cli): expõe adoção revogável de skills com diagnóstico`. Atualizar decisões para implementado somente quando houver evidência; antes disso, manter proposta. Revisor independente da branch confere matriz abaixo, segurança/qualidade e diff integrado. PR pode ser preparado; merge exige aprovação aplicável e todos os jobs de verificação CI verdes.

## Matriz rastreável de aceite A2

Referências são seções/linhas da spec A original; títulos e contratos prevalecem se linhas mudarem.

| Requisito da spec A | Tarefa / evidência exigida |
|---|---|
| Execução multiagente e vertical interna antes de produção (:24–30) | Propriedade sequencial; revisão T2; CLI só T4 |
| Três comandos, foreground/confirmação e guards diretos (:103–112) | T2 guard antes de I/O; T4 subprocessos e --json |
| A presente, idempotência, independentes e nova B (:114–127) | T1 projeção; T2 bytes/modos/raízes; T3 B após U |
| Lock, captura I/A, comparação e revalidação (:129–134) | T2 corrida com/sem expectativa; T4 comparação A/B |
| Conservar arquivos/histórico; recusas; rollback A/U (:136–140) | T2 inventário; T3 rollback; T4 saída orientadora |
| A→M→R→U; múltiplos restores e cadeia incompleta (:144–169) | T1 corrupção; T3 linhagem e recuperação |
| History/get/pending global e grants independentes (:173–179) | T1 timeline e get/pending; T3 escritores; T4 history |
| Tombstones de adotadas e encerramento vinculado (:184–187) | T3 sync com bundle futuro; sem retroação |
| Migração/capability, snapshots e backup/reparo (:191–194) | T1 writer antigo e fingerprints; T4 backup binário |
| Recuperação A/U, pendências cruzadas e restore inconcluso (:196–207) | T2 estados; T3 SIGKILL/fsync/veto/atestação |
| Aceitação A2 e códigos/JSON (:216–231) | T2/T3/T4; suite integral + imagem + revisão + CI |

## Revisão e autorização

Autorrevisão documental: os apontamentos recebidos estão incorporados na CLI, nos contratos, tarefas e matriz; B1 foi corrigido somente na descrição de escopo, sem incluí-lo na implementação A2. Os testes acima são critérios planejados, **não resultados já executados**. Não atribuir aprovação independente a este plano sem parecer específico.

Gate documental cumprido nesta sessão: usuário autorizou a implementação automática deste plano A2 após revisar os ajustes. A execução multiagente já está especificada pela spec; aprovação do plano não aprova B1/B2, implantação de política ou mutações em lote. Os gates de revisão e validação da implementação continuam exigidos.
