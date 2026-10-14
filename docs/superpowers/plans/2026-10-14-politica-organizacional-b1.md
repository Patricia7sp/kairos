# Política organizacional B1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para execução nativa ou superpowers:subagent-driven-development se o usuário escolher delegação. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir remoção e substituição destrutiva de skills protegidas pela política local ou por registros duráveis, nos serviços participantes.

**Architecture:** Uma sessão de política mantém a fonte observada aberta e revalida sua identidade antes de efeitos. O schema aditivo liga recibos de política ao journal existente e registra proteção na mesma transação da conclusão da instalação. Serviços, recuperação, CLI, sync e promoção recebem essa sessão explicitamente.

**Tech Stack:** Python 3.11, SQLite, unittest/pytest, descritores POSIX, Ruff; sem dependência nova.

**Spec:** [B1 aprovada em 2026-10-14](../specs/2026-10-14-politica-organizacional-b1-design.md). Base inspecionada: `7dbed40`, sobre A2 `d730822`. [B1/B2](../specs/2026-10-11-politica-publicacao-skills-design.md) continua como limite geral.

## Global Constraints

- “A garantia é continuidade entre operações cooperativas.” Não prometer resistência ao mesmo UID, autenticação de organização ou isolamento de runtime.
- “Não haverá parâmetro `force`, seleção de emissor ou override por ferramenta.”
- JSON UTF-8 regular de até 1 MiB; `version` inteiro 1; sem symlink na cadeia ou hardlinks; `organization_id`: `[a-z0-9][a-z0-9-]*`, 1–64 caracteres.
- `KAIROS_SKILL_ORG_POLICY`: ausência seleciona `none`; vazio/relativo é `input`; home e `--yes` não redefinem política.
- “Leitores não migram nem gravam observações.” Registros A1/A2 permanecem imutáveis.
- “Um veto durável é por home/nome e sobrevive à troca de inode e à ausência posterior de política, inclusive modo `none`.”
- “Não há `unprotect` nesta fase.” Binding inativo continua vetando.
- “Hoje não existe operação pública de skills `remove_file` no Kairos.” Testar contrato, sem criar ferramenta; `edit`, `patch`, `write_file` in-place ficam fora do veto novo.
- “Não relatar sucesso de cópia/atualização se o guard recusou.” B2, instalação de política em produção e otimização do ledger não entram aqui.
- Testes comportamentais, sem ler `.py`; fixtures de permissões usam `chmod` quando precisam de modo exato, inclusive sob umask `0077`.

## Review Focus

1. Uma fonte especial (FIFO/socket/device) não pode bloquear o processo: abrir sem seguir links e com `O_NONBLOCK`, validar tipo antes de ler — tarefa 1.
2. Um home ausente em binding não deve ser criado nem desviar a política por um ancestral symlink — tarefa 1.
3. Queda entre preparo e publicação não pode gerar veto de instalação que nunca concluiu, nem instalação concluída sem veto — tarefas 2 e 3.
4. Uma atualização de bundle já copiada antes de outra entrada vetada não pode ser reportada como lote integralmente concluído — tarefa 4.
5. Recuperação de operação v9 pendente sob política B1 nova não pode usar ausência de recibo como autorização — tarefas 2 e 3.

---

## Estrutura e decisões de integração

Usar o worktree existente `removal_design`, branch `chore/fechamento-a2-planejamento-b1`, sem criar outro. Revalidar status e base antes de executar; preservar alterações de terceiros. Execução nativa é recomendada: as quatro entregas dependem dos mesmos contratos e do journal. Revisão independente do conjunto permanece obrigatória antes do merge.

| Unidade | Responsabilidade |
|---|---|
| `kairos_skills/org_policy_contract.py` (novo) | Tipos imutáveis, validação de nomes/ações e recibos sanitizados |
| `kairos_skills/org_policy.py` (novo) | Seleção de contexto, leitura por descritores, sessão e revalidação da fonte |
| `kairos_state/skill_org_schema.py` (novo) | Migração, triggers e validação da família de proteção |
| `kairos_state/repositories/skill_org_protections.py` (novo) | Intenções, eventos e consulta de proteção por home/nome |
| `kairos_skills/org_policy_guard.py` (novo) | Decisão combinando sessão, namespace e registros; comparação na recuperação |
| Serviços/recuperações existentes | Chamar guard antes de efeitos; passar recibo ao journal |
| Composições CLI existentes | Abrir sessão antes de conexão mutante/migração e propagá-la até o encerramento |
| `sync.py`, `hub.py` | Guard em chamadas públicas e compensações, sem implementar journal de publicação B2 |

Não fazer refatoração geral dos serviços. Conexões, lock comum e captura de árvores continuam usando os módulos atuais. Reutilizar `kairos_filesystem.descriptors` e a validação por montagem já disponível onde se observa uma cadeia de descritores.

## Tarefa 1: contexto, parser e observação estável da política

**Files:** criar os dois primeiros módulos da tabela; criar `tests/test_skill_org_policy.py`.

**Interfaces produzidas:**

```python
# org_policy_contract.py — dataclasses frozen
OrgPolicyContext(mode: Literal["none", "file"], source: Path | None)
OrgBinding(home: str, name: str, organization_id: str, active: bool)
OrgProtectionBinding(organization_id: str, active: bool)
OrgPolicyReceipt(home_id: str, name: str, observation_id: str,
                 source_digest: str | None,
                 bindings: tuple[OrgProtectionBinding, ...])
OrgPolicyAction  # StrEnum: CREATE, RESTORE, REMOVE, REMOVE_FILE,
                 # ROLLBACK_CREATE, ADOPT, UNADOPT, SYNC, PROMOTE

# org_policy.py
def org_policy_context(environ: Mapping[str, str]) -> OrgPolicyContext: ...
def parse_org_policy(raw: bytes) -> tuple[OrgBinding, ...]: ...
def observe_org_policy(context: OrgPolicyContext, home: Path
                       ) -> ContextManager[OrgPolicySession]: ...

class OrgPolicySession:
    context: OrgPolicyContext  # imutável; fonte não vai para diagnósticos
    home_id: str
    def revalidate(self) -> None: ...
    def receipt(self, name: str) -> OrgPolicyReceipt: ...
```

`OrgPolicyReceipt` contém apenas rótulos/estado dos bindings do home/nome solicitado, convertidos em `OrgProtectionBinding`; o próprio recibo não inclui caminhos de homes nem fonte e pode ser reconstruído do banco. `observation_id` é UUID4 hex; `source_digest` é SHA-256 dos bytes, `None` só em modo `none`. Uma sessão só atende o home observado, tem duração explícita via context manager e recusa uso depois de fechada. Construção de contexto valida modo/path; construção de sessão valida a fonte antes de qualquer chamada mutante.

- [ ] **1. Escrever testes de parser e seleção.** Casos: `version=true`, 0, 2; objetos com chaves repetidas ou desconhecidas em ambos os níveis; UTF-8/JSON inválido; bindings sem `active` ou com tipo incorreto; nome inválido; ID de organização vazio, Unicode, 65 caracteres; par home/nome duplicado mesmo com organização diferente. Assertar `SkillMutationError.kind == "input"`. Ausência da variável produz `none`; vazio/relativo recusa; nenhum diretório é criado.

  Exemplo obrigatório em `unittest.TestCase`, importando a função pública:

  ```python
  def test_rejects_boolean_version(self):
      with self.assertRaises(SkillMutationError) as caught:
          parse_org_policy(b'{"version":true,"bindings":[]}')
      self.assertEqual(caught.exception.kind, "input")
  ```
- [ ] **2. Escrever testes de observação e falhas.** Arquivo ausente/ilegível → `unavailable`; arquivo acima de 1 MiB, symlink/hardlink/FIFO → `input` sem bloqueio; trocar inode mantendo bytes, modificar fonte ou renomear ancestral depois da abertura → `conflict` em `revalidate()`. Usar subprocesso com timeout para FIFO; erros de permissão podem usar falha `EACCES` na operação de abertura. Assertar fonte sentinela ausente de mensagem/log.
- [ ] **3. Escrever testes de home/namespace.** Dois homes com mesmo nome recebem bindings distintos; home ausente no documento não é criado; home alvo por symlink ou com `..` recusa; ancestral existente de home ausente por symlink recusa. Validar grafia original antes de construir `Path`, que elimina alguns componentes lexicais. Preservar `catalog_home_id` dos homes válidos já existentes.
- [ ] **4. Rodar RED:** `.venv/bin/python -m pytest -q tests/test_skill_org_policy.py`. Falha esperada: módulos/interfaces ainda ausentes; não aceitar falha de infraestrutura como RED.
- [ ] **5. Implementar as interfaces acima.** Ler bytes por descritores sem seguir links; validar tamanho/tipo/nlink antes da leitura, EOF e stat depois; parser detecta duplicatas com `object_pairs_hook`. Validar cadeias existentes sem criar homes ausentes. Manter observação de identidade, montagem, cadeia e digest; revalidar antes de emitir recibo e em chamada explícita. `none` ainda valida o home observado e a vida da sessão.
- [ ] **6. Rodar GREEN e Ruff:** teste completo acima; `.venv/bin/ruff check kairos_skills/org_policy*.py tests/test_skill_org_policy.py` e format nos mesmos arquivos. Esperado: todos passam. Conferir erros sanitizados com `assertLogs`/captura estruturada; não usar `console.error` ou suprimir falhas.
- [ ] **7. Commit:** `feat(skills): observa política organizacional por descritores`.

## Tarefa 2: journal de proteção e compatibilidade do estado

**Files:** criar schema/repositório da tabela; modificar `kairos_state/{migrations,skill_writer,skill_adoptions_schema}.py`, `kairos_state/repositories/{skill_mutations,skill_removals}.py`, `kairos_skills/removal_state.py`, `kairos_cli/backup.py`; criar `tests/test_skill_org_state.py` e `tests/test_skill_org_backup.py`; adaptar fixtures afetadas de estado/backup. A ativação padrão em `kairos_state/schema.py` pertence à tarefa 3.

**Interfaces consumidas:** `OrgPolicyReceipt` da tarefa 1; `SkillMutationDraft`, `SkillTreeDraft`, `_write()` e timeline A2 existentes.

**Interfaces produzidas:**

```python
def migrate_org_protections(connection: sqlite3.Connection) -> None: ...
def validate_org_schema(connection: sqlite3.Connection) -> None: ...
def repair_org_triggers(connection: sqlite3.Connection) -> None: ...

class SkillOrgProtectionRepository:
    def __init__(self, connection: sqlite3.Connection, *, home_id: str): ...
    def protected_organizations(self, name: str) -> frozenset[str]: ...
    def prepared_receipt(self, operation_id: str) -> OrgPolicyReceipt | None: ...

# fachadas existentes: recibo explícito para novas CREATE/RESTORE
SkillMutationRepository.prepare(draft, text,
    *, policy_receipt: OrgPolicyReceipt | None = None)
SkillMutationRepository.prepare_restore(draft,
    *, policy_receipt: OrgPolicyReceipt | None = None)
```

`protected_organizations()` valida família, relações e projeção antes de retornar; corrupção nunca equivale a conjunto vazio. Métodos internos de insert não iniciam transação própria: entram em `_write()` do journal. Remoção, rollback destrutivo e A/U não criam intenções de instalação. Omissão de recibo só atende o contrato antigo em banco v7–v9; na v10 é `input` antes de qualquer insert, nunca recibo `none` sintetizado.

**Modelo de dados fixado para este plano:**

- Próxima migração é v10 na base atual. Se outra migração entrar antes da execução, rebasear e ajustar a numeração, sem teste que congele a versão global.
- Nesta tarefa, registrar a migração mas conservar `SCHEMA_VERSION=9` como teto padrão; os testes novos chamam o migrador real com `target=10`. Na tarefa 3, elevar o teto para 10 junto à propagação obrigatória da sessão nos serviços. Não publicar a etapa intermediária como B1 implementada.
- `skill_org_migration(singleton, prefix_last_authority_sequence)` captura a fronteira v9; registros anteriores não são reescritos nem recebem proteção inferida.
- `skill_org_intents(operation_id PRIMARY KEY, home_id, name, observation_id, policy_digest)` guarda um recibo preparado, inclusive sem bindings. `skill_org_intent_bindings(operation_id, organization_id, active)` guarda apenas rótulos e tipos validados; PK no par, sem path privado.
- `skill_org_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id, installation_id, home_id, name, organization_id, policy_digest, observation_id)` contém proteção concluída, única por `(operation_id, organization_id)`. Para CREATE/RESTORE, `installation_id == operation_id`.
- `skill_org_protections` é view derivada `DISTINCT home_id,name,organization_id` desses eventos. Não depende do inode instalado, estado atual da adoção ou presença da fonte.
- Intento e bindings entram na mesma transação do preparo CREATE/RESTORE. Eventos entram na mesma transação do respectivo `committed`, antes de confirmar a transação. `aborted`/`conflict` sem commit não gera proteção; retomada de conflito só pode gerar eventos na conclusão válida.
- Triggers imutáveis impedem editar/apagar as quatro tabelas canônicas. Triggers de conclusão validam operação, nome/home e recibo e produzem eventos para cada binding preparado. Recibo com zero bindings é obrigatório em novas instalações e produz zero eventos, sem fingir vínculo organizacional.
- Para operações preparadas antes da fronteira v10, ausência de recibo é histórica e não concede autorização de recuperação. Novos preparos sem recibo não podem concluir. Capability de escritor v10 é exigida nas mutações das famílias existentes e novas; cliente v9 não grava em v10. Isso não é fronteira contra código hostil com mesmo UID.

- [ ] **1. Escrever testes de transação.** Preparar CREATE com binding e conferir intenção sem proteção; concluir e conferir proteção; abortar e conferir ausência. Injetar falha de insert de evento para verificar rollback de toda a conclusão. Repetir para RESTORE. Recibo home/nome divergente, ID de observação inválido e conclusão nova sem recibo recusam.

  A fixture `setUp` abre `self.connection` em banco temporário real e aplica o degrau novo via `migrate(connection, target=10)`. Exemplo de recusa de estado parcial, além dos testes de transação:

  ```python
  def test_missing_event_family_is_not_an_empty_policy(self):
      self.connection.execute("DROP TABLE skill_org_events")
      with self.assertRaises(sqlite3.DatabaseError):
          validate_org_schema(self.connection)
  ```
- [ ] **2. Escrever testes de continuidade/compatibilidade.** Migração v9 preserva todas as linhas canônicas A2; repetir `migrate(target=10)` é idempotente; pendência antiga segue identificável; leitor de home antigo não migra. Cliente SQLite sem capability ou registrando versão 9 é recusado na v10. Remover tabela, trigger, binding preparado ou evento obrigatório torna consulta/escrita `corrupt`, inclusive família parcial com zero registros de proteção. Dois eventos com organizações diferentes preservam a união.
- [ ] **3. Escrever testes de backup/reparo.** Criar instalação protegida por API do repositório; `_archive_home` deve preservar DB/WAL por snapshot e validar família antes de reportar sucesso. Restaurar backup no mesmo home após retirar a fonte mantém proteção por nome. Backup para home diferente não transfere bindings; relocar autoridade não é recurso novo. Restauro parcial recusa. `repair_derived_objects` pode recriar view/triggers sem alterar nenhuma linha canônica, comprovado com consulta de linhas, não apenas contagens. Depois do reparo v10, repetir escrita real com clientes sem capability, 8 e 9 (todos recusados) e com capability 10/recibo válido (aceita), cobrindo famílias antigas e nova; não testar contagem ou texto de triggers.
- [ ] **4. Rodar RED:** `.venv/bin/python -m pytest -q tests/test_skill_org_state.py tests/test_skill_org_backup.py`. Esperado: contratos de proteção ausentes.
- [ ] **5. Implementar schema, repositório e integração de transações.** Registrar v10 sem elevar o teto padrão nesta tarefa; `migrate` distingue teto padrão de maior versão suportada registrada, permitindo alvo explícito e recusando estado/alvo acima do suportado, inclusive em repetição. Atualizar leitores/validação de timeline para reconhecer família completa e continuar lendo v7–v9. `validate_adoption_schema()` mantém assinatura e verifica triggers apropriados à versão observada; não enfraquecer igualdade de SQL esperada. `register_skill_writer()` anuncia capability que o código entende e volta a registrá-la após migração. Validação final de `migrate` e seleção de reparo usam a versão realmente observada, distinguindo família v9/v10. Acrescentar tabelas à lista canônica de reparo; view derivada é validada/reparada separadamente. Backup valida snapshot antes de arquivar como sucesso, sem gravar no banco fonte.
  Na migração v10, recriar explicitamente todos os triggers `*_writer` das famílias mutation/tree/adoption/authority existentes com `kairos_skill_writer_version() IS NOT 10`, além dos novos. Não reutilizar `IF NOT EXISTS` que preserve SQL v8/v9. `repair_derived_objects` e os validadores devem selecionar o conjunto SQL v10 quando o banco observado for v10; o reparo nunca reinstala guards `IS NOT 8`/`IS NOT 9` nesse banco. Manter os conjuntos v8/v9 somente para seus schemas históricos.
- [ ] **6. Rodar GREEN:** `.venv/bin/python -m pytest -q tests/test_skill_org_state.py tests/test_skill_org_backup.py tests/test_skill_adoption_state.py tests/test_skill_adoption_backup.py tests/test_skill_mutation_state.py`. Adaptar chamadas de `prepare` para recibo explícito `none` nas fixtures da base nova. Usar banco v9 real para legado, sem reduzir a validação da v10 para manter teste antigo verde.
- [ ] **7. Commit:** `feat(state): registra proteção organizacional junto à instalação`.
  Os commits das tarefas 2 e 3 devem entrar no mesmo merge: a tarefa 2 isolada é preparação interna, sem migração alcançável pelo caminho padrão; não fazer release, merge ou deploy dela separadamente.

## Tarefa 3: serviços, diagnóstico e recuperação com guard obrigatório

**Files:** criar `kairos_skills/org_policy_guard.py`; modificar `kairos_state/schema.py`, `kairos_skills/{mutations,adoptions,identification,identification_contract,mutation_recovery,removal_recovery,adoption_recovery}.py`, `kairos_cli/{skill_mutations,skill_adoptions,skill_identification}.py`; adaptar chamadores e testes de serviços; criar `tests/test_skill_org_services.py`, `tests/test_skill_org_recovery.py`, `tests/test_cli_skill_org_policy.py`.

**Interfaces consumidas:** sessão/recibos da tarefa 1 e registros da tarefa 2.

**Interfaces produzidas:**

```python
class SkillOrgGuard:
    def __init__(self, policy: OrgPolicySession,
                 protections: SkillOrgProtectionRepository | None): ...
    def check(self, name: str, action: OrgPolicyAction) -> OrgPolicyReceipt: ...
    def check_recovery(self, record: CommonRecord) -> OrgPolicyReceipt: ...

SkillMutationService(home, repository, *, policy: OrgPolicySession)
SkillAdoptionService(home, repository, *, policy: OrgPolicySession)
identify_skill(home: Path, name: str, *, policy: OrgPolicySession) -> SkillIdentification
reconcile_pending(repository, files, *, name: str, policy: OrgPolicySession)
```

Todas as recuperações chamadas por `reconcile_pending`, inclusive chamadas diretas de handlers de recuperação, recebem `policy` keyword obrigatório. Nenhum default escolhe `none`. `None` em `protections` representa somente home legado sem família, previamente validado pelo reader, e não erro de leitura convertido em ausência.

**Matriz de `check`:** sempre revalidar sessão e namespace primeiro. REMOVE, REMOVE_FILE, ROLLBACK_CREATE, ADOPT, SYNC e PROMOTE recusam binding ou registro; CREATE/RESTORE podem concluir destino ausente com autoridade própria e recibo; UNADOPT continua permitido sem alterar veto. Não retirar a autoridade USER existente por efeito lateral de uma consulta. A reserva `_org` recusa mutações; chamadas de arquivo verificam todos os componentes antes de validar nome comum. Não transformar edit/patch/write_file em ações vetadas por inferência.

**Recuperação fixada:** para CREATE/RESTORE pendente com recibo, bindings preparados continuam vinculados mesmo se ausentes na fonte atual. Binding corrente novo, não contido no recibo preparado, impede conclusão; não acrescentar organização para legitimar retroativamente uma publicação. Em operação pré-v10 sem recibo, tratar conjunto preparado como vazio: binding corrente impede concluir. Para operações destrutivas, todo veto corrente/registrado impede efeitos e conclusão. Recibo histórico não substitui revalidação da fonte corrente em uma nova tentativa.

- [ ] **1. Escrever testes diretos de serviços.** Remover ou retirar por rollback alvo ligado/protegido retorna `denied`, mantendo inventário/inode e sem nova instalação. CREATE com binding registra proteção; depois retirar binding, trocar inode ou usar modo `none` não permite remover. Adoção USER/`--yes` não afastam veto; `unadopt` não remove proteção já registrada. Binding observado apenas por recusa/identify não persiste. Restauração mantém a linhagem exigida por A2 e registra binding aplicável.
  Acrescentar testes de contrato para `SkillMutationService(home, repository)` e `SkillAdoptionService(home, repository)` sem `policy`: ambos devem lançar `TypeError` antes de efeitos. Testar também sessão fechada e sessão pertencente a outro home; nenhuma pode selecionar modo `none` implicitamente.

  A fixture cria `procedimento-interno` pessoal, abre sessão com binding para esse alvo e compõe `self.service`. `inventory()` captura paths relativos, bytes, modos e identidades; `self.before` guarda o inventário antes da chamada:

  ```python
  def test_confirmation_does_not_override_org_binding(self):
      with self.assertRaises(SkillMutationError) as caught:
          self.service.remove("procedimento-interno",
                              actor=Actor.USER_FOREGROUND, confirmed=True)
      self.assertEqual(caught.exception.kind, "denied")
      self.assertEqual(self.inventory(), self.before)
  ```
- [ ] **2. Escrever testes de interrupção.** Subprocesso sinaliza depois de preparar recibo, depois de publicar/retirar e antes do commit; encerrar com SIGKILL e retomar por API real. Fonte ausente mantém pendência e todas as versões. Veto novo após movimento impede conclusão e compensação destrutiva; limpeza só remove staging privado comprovado. Conferir caso v9 pendente sem recibo e caso de binding preparado retirado antes da retomada. Fonte trocada na mesma sessão é `conflict`, sem nova tentativa implícita.
- [ ] **3. Escrever testes CLI/diagnóstico.** Variável inválida ou fonte inacessível recusa antes de criar/migrar DB; mesmo nome em outro home não recebe veto errado. `identify` usa duas observações, revalida fonte no fim e preserva home byte a byte, sem sidecars/migração. Aproveitar campos existentes: `known_protection` inclui `organization_policy`/`organization_recorded`; `protection_coverage` distingue `policy_and_recorded` de `namespace_and_recorded_only` (grafia pública existente, preservada); alvo vetado nunca é `removivel`. `reasons` explica limitação de observação não persistida sem caminho, conteúdo ou rótulos privados. Capturar stdout/stderr/logs com sentinelas.
- [ ] **4. Rodar RED:** `.venv/bin/python -m pytest -q tests/test_skill_org_services.py tests/test_skill_org_recovery.py tests/test_cli_skill_org_policy.py`. Falha esperada: serviços ainda não recebem sessão nem aplicam veto.
- [ ] **5. Integrar guard e composição e ativar v10.** Abrir `observe_org_policy(org_policy_context(os.environ), home)` no adaptador CLI antes do banco mutante e mantê-lo aberto até saída; serviços recebem a mesma sessão. Revalidar antes de preparo, rename, exclusão, fsync/conclusão autorizadora e callbacks de compensação. Encaminhar recibos de CREATE/RESTORE ao repositório. Elevar `SCHEMA_VERSION` para 10 somente com essa integração no mesmo commit. Recuperação continua sob lock do chamador; falha de fonte propaga categoria e preserva pendência, sem captura que retorne sucesso.
- [ ] **6. Atualizar chamadores internos e fixtures.** Cada teste pessoal abre uma sessão explícita `none`, sem variável herdada por acidente. Usar `rg` para localizar construções e chamadas de recuperação/identify; a busca auxilia integração, testes não leem código. Atualizar `scripts/benchmark-skill-ledger.py` para a assinatura nova, sem sobrescrever dados históricos nem repetir benchmark como gate de desempenho.
- [ ] **7. Rodar GREEN:** `umask 0022 && .venv/bin/python -m pytest -q tests/test_skill_org_services.py tests/test_skill_org_recovery.py tests/test_cli_skill_org_policy.py tests/test_skill_adoption_*.py tests/test_skill_identification*.py tests/test_skill_mutation_*.py tests/test_cli_skill_*.py`. Confirmar caminhos reais de recuperação e ausência de skips inesperados; executar também `umask 0077 && .venv/bin/python -m pytest -q tests/test_skill_org_*.py tests/test_cli_skill_org_policy.py` para os testes novos, que não dependem da umask.
- [ ] **8. Commit:** `feat(skills): aplica veto organizacional em serviços e recuperação`.

## Tarefa 4: sync, promoção e aceite integrado

**Files:** modificar `kairos_skills/{sync,hub}.py`, `kairos_cli/handlers.py`, `docs/decisoes.md`; criar `tests/test_skill_org_publication.py`, `docs/superpowers/reports/2026-10-14-politica-organizacional-b1.md`; adaptar testes existentes de sync/hub e atualizar manual/metadata CLI pertinente encontrado nos adaptadores.

**Interfaces consumidas:** `OrgPolicySession`, `SkillOrgGuard`, reader de proteção e lock existentes.

**Interfaces produzidas:**

```python
sync_bundled_skills(bundled_dir: Path, user_dir: Path,
                   *, policy: OrgPolicySession) -> SyncResult
install_from_quarantine(quarantine_path: Path, quarantine_dir: Path,
                        skills_dir: Path, *, policy: OrgPolicySession) -> Path
```

Ambas validam que `policy.home_id` corresponde ao home pai de `user_dir`/`skills_dir`; destino por alias symlink recusa. Leitura da família de proteção é sem migração e ocorre sob lock antes da publicação. APIs não abrem sessão `none` por conta própria. Manter opt-out sem efeitos: pode retornar seu resultado explícito sem consultar uma fonte para operação que não publica.

`SyncResult` ganha `blocked: list[str]` e `failed: bool` para representar veto e progresso parcial. Processar cada alvo permitido preservando seus resultados reais; alvos vetados ficam em `blocked`, fora de `copied`/`updated`; `failed=True`. CLI retorna 77 se houver veto, emite campos também no JSON e não imprime conclusão integral. Erro de fonte é exceção, não conjunto vazio nem resultado parcial de sucesso. Não publicar manifesto de operação vetada como se tivesse ocorrido.

- [ ] **1. Escrever testes de chamada direta.** Sync inicial ou update e promoção para alvo protegido recusam, inclusive destino ausente. Confirmar inventário de destino/quarentena e manifesto preservados para o alvo vetado. Chamada sem contexto ou com sessão de outro home recusa. Manter testes pessoais de criação, edição local que vence, opt-out e tombstones. Não conceder origem BUNDLED por recibo de política.

  Fixture: `self.bundled` tem somente `procedimento-interno`, `self.home` é home real com destino ausente e `self.policy` é sessão com binding desse alvo:

  ```python
  def test_initial_sync_does_not_publish_bound_target(self):
      result = sync_bundled_skills(self.bundled, self.home / "skills",
                                   policy=self.policy)
      self.assertEqual(result.blocked, ["procedimento-interno"])
      self.assertTrue(result.failed)
      self.assertEqual(result.copied, [])
      self.assertFalse((self.home / "skills/procedimento-interno").exists())
  ```
- [ ] **2. Escrever testes de fonte/compensação e lote.** Trocar fonte depois de staging ou na fronteira de publicação; guard recusa sem retirar árvore protegida no callback. Primeira skill pessoal copiada e segunda vetada produz primeira em `copied`, segunda em `blocked`, `failed=True` e saída CLI 77. Nenhum caso vetado pode ter sucesso silencioso, nem remover quarentena. Não prometer atomicidade do lote. Injetar veto durante rollback existente e conferir versões preservadas com erro estruturado.
- [ ] **3. Rodar RED:** `.venv/bin/python -m pytest -q tests/test_skill_org_publication.py`. Esperado: ausência de contexto obrigatório/blocked ou efeito destrutivo existente no caminho explicitamente testado.
- [ ] **4. Integrar APIs e retornos.** Guard em cada publicação e compensação; callback recebe sessão e verifica identidades. Remoções recursivas participantes usam captura e `delete_verified_tree(..., guard=...)` para revalidar antes de cada unlink/rmdir. Promoção protegida recusa antes de qualquer exclusão; não implementar publicação protegida nem journal B2. No sync, substituir cleanup incondicional de `TemporaryDirectory` por limpeza explícita de staging comprovado: se compensação for vetada ou fonte indisponível, preservar `old/new/discard` e registrar erro com ID sanitizado. Testar que a saída do context manager e o garbage collector não apagam esses resíduos. Não prometer recuperação transacional de publicação pessoal antes de B2. CLI `skills sync` propaga contexto e resultados parciais. Para erro inesperado, logging estruturado sanitizado e exceção/resultado de erro; não converter falha em lista vazia. Atualizar decisão D-CLI.SKILL.ORG com garantia efetivamente testada, mantendo B2 e isolamento como pendentes.
- [ ] **5. Rodar GREEN e aceite local.** `.venv/bin/python -m pytest -q tests/test_skill_org_publication.py tests/test_skills.py tests/test_cli_skills.py tests/test_skill_*sync*.py`; depois `umask 0022 && scripts/ci.sh` integral, com Docker disponível, imagem `kairos:test`, stub e opt-ins aplicáveis conforme script. O modo `0022` acomoda as fixtures antigas identificadas no relatório A2; não dispensa o teste novo sob `0077`. Preservar comandos/logs, commit, hashes relevantes, versão de ferramentas e skips justificados no relatório. Runtime live só com credenciais autorizadas reais; não simular aceite. Corrigir falhas afetadas e repetir apenas checks invalidados; não atribuir sucesso da base à branch nova.
- [ ] **6. Obter revisão independente do conjunto.** Revisor capacitado verifica spec versus diff, segurança de descritores, continuidade dos eventos, recuperação e resultado parcial de publicação. Execução nativa não dispensa esse revisor. Root valida os achados e integra correções; revisão não aprovada impede aceite. Aplicar skill de revisão antes de despachar e respeitar gate/modelos disponíveis da orquestração.
- [ ] **7. Commit e PR em rascunho:** `feat(skills): protege publicações e conclui integração B1`. Descrição em português com limites, evidências e pendências; executar CI remoto e conferir todos os jobs aplicáveis verdes. Não fazer merge/deploy sem autorização aplicável à entrega B1; autorização histórica de A2 não se estende automaticamente.

## Autorrevisão e handoff

Antes de cada commit de implementação, Root revisa o diff da tarefa contra a spec e confere seus logs RED/GREEN; a revisão independente obrigatória é a do conjunto na tarefa 4. Não criar agentes automaticamente para cada checkbox.

Cobertura conferida: fonte/configuração, home, namespace e sanitização (1); continuidade, capability, backup e migração (2); ação×guard, diagnóstico e recuperação (3); escritores legados e gates locais/remotos (4). Os cinco itens de Review Focus têm casos explícitos nas tarefas indicadas. As interfaces produzidas são usadas com as mesmas assinaturas nas tarefas dependentes. O plano não implementa B2, não otimiza leitores e não muda a garantia de mesmo UID.

Execução recomendada: nativa, quatro tarefas sequenciais com commits verificáveis e uma revisão independente final. O usuário aprovou a spec; este plano aguarda revisão antes da implementação.

Revisão do usuário em 2026-10-14: sem impedimento de execução; incorporar migração/reparo explícitos dos writer triggers, grafia pública de cobertura, merge conjunto das tarefas 2/3 e testes de construção sem sessão. Na tarefa 4, registrar em `docs/decisoes.md` a incompatibilidade deliberada: clientes v8/v9 não escrevem em famílias antigas nem novas de banco v10; atualização exige todos os escritores participantes compatíveis.
