# CLI skills identify — plano de implementação A1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar `kairos skills identify NOME --json` com identificação real e somente leitura das evidências da instalação.

**Architecture:** Consultar o SQLite pela cópia privada DB+WAL existente, resolver a linhagem completa e capturar a árvore por descritores sem criar recursos de escritor. Extrair a resolução estruturada de origem preservando a recusa estrita do remove; compor serviço leitor e CLI em módulos próprios.

**Tech Stack:** Python 3.11, unittest/pytest, SQLite, descritores POSIX e primitivas kairos_filesystem; sem dependências novas.

**Spec:** `docs/superpowers/specs/2026-10-10-proveniencia-skills-design.md`, somente A1. D-CLI.SKILL.PROVENANCE/D-CLI.SKILL.ORG/D-09.GATES em `docs/decisoes.md`.

Base aprovada: main `404dc318eb4f54fc7c4e99e58775d89edbfa5bc1`, squash do PR #100.

## Global Constraints

- Somente A1: não implementar adopt/unadopt, policy, publicação, sync ou hub.
- “Fallback USER protege legado da curadoria, mas não prova autoria nem autoriza remoção.”
- “Não criar home, banco, lock, migração, registro ou sidecar.” Temporário externo privado é permitido.
- “Ledger corrupto/incompatível ou identidade impossível de atestar são erros.”
- Não ampliar nomes: aplicar `validate_name` antes de I/O; `_org` continua recusado pelo validador atual.
- Compatibilidade intencional: list pode mostrar diretórios legados fora de kebab-case; identify recusa esses nomes com exit 2 antes de I/O. Não exigir semver diz respeito ao frontmatter, não amplia nomes. Recusar `_org` como nome inválido não equivale a implementar reconhecimento organizacional.
- Identidade substituída byte-idêntica não renova prova; edições internas não invalidam prova da raiz.
- Não alterar schema/capability nem IDs/blobs/metadata antigos; sem TOOL nova/custo no prompt.
- Não ler fonte em testes; não congelar contagens de catálogo. Testar comportamento real.
- Commits em português conventional; suíte integral local e CI verdes antes de merge.
- `removivel` é diagnóstico condicionado ao instante observado e aos guards disponíveis; remove revalida. Não prometer transação conjunta entre DB e árvore nem suporte de publicação só porque leitura funciona.

## Review Focus

- Linhagem mais longa que `history(limit=100)`: origem original não pode desaparecer por truncamento (T1).
- Manifesto ausente/v1 versus moderno malformado: insuficiência de prova é diagnóstico; corrupção/árvore insegura não viram unknown (T1).
- Restore USER/BUNDLED editado versus raiz substituída: preservar prova da instalação legítima, recusar renovação por nome/bytes (T2).
- Commit em WAL durante consulta: recusar instabilidade e preservar DB/WAL/SHM; reader nunca reconcilia pendências (T2).
- CLI em home ausente ou conteúdo com segredo: não criar pais nem expor dados privados em erro/JSON (T3).

## Estrutura e execução multiagente

T1 possui contratos, consulta da evidência e resolução de origem. T2 possui composição leitora; T3 possui parser/CLI/documentação. T2/T3 dependem do contrato T1; começar T3 após T2 para validar o serviço real. Um implementador fresco e revisões independentes de spec/qualidade por tarefa, seguidos de revisão da branch. Não compartilhar propriedade de arquivos simultaneamente.

## Tarefa 1: evidências estruturadas sem mudar autorização de remove

**Files:**
- Create: `kairos_skills/identification_contract.py`.
- Modify: `kairos_skills/removal_origin.py`.
- Modify: `kairos_state/repositories/skill_mutations.py`.
- Test: `tests/test_skill_identification_evidence.py`.
- Existing regression: `tests/test_skill_removal_service.py`, `tests/test_skill_removal_projection.py`.

**Interfaces:**
- Consumes: `InstallationProof`, `TreeCapture`, registros e projeção validada de `SkillMutationRepository`; `prove_removal_origin(home, name, current, captured)` permanece compatível.
- Produces frozen dataclass `InstallationEvidence(current: InstallationProof | None, historical_creation_id: str | None, current_removal_id: str | None, pending_operation_ids: tuple[str, ...])`.
- Produces `SkillMutationRepository.installation_evidence(name: str) -> InstallationEvidence`, somente consultas, sequência integral sem limite de history.
- Produces helper compartilhado `_project_committed_records(records: tuple[CommonRecord, ...]) -> tuple[InstallationProof | None, SkillTreeRecord | None, bool, SkillMutationRecord | None]`: current, removed, bundled_removed, historical_creation. Este concentra o único loop de transições, com registros committed em ordem de evento.
- Produces carregador privado `SkillMutationRepository._projection_inputs(name: str) -> tuple[tuple[CommonRecord, ...], tuple[str, ...]]`: committed ordenados e IDs de pendências, derivados do mesmo conjunto integral validado; carregar timeline/records uma vez. Tanto `_projection` quanto `installation_evidence` usam esse carregador e o helper comum; nenhum segundo loop de projeção nem chamada a `pending(name)` dentro de installation_evidence. `_projection` preserva retorno compatível dos três primeiros valores.
- Produces frozen dataclass `OriginEvidence(provenance: Provenance | None, identity_valid: bool | None, authorization_basis: str, reasons: tuple[str, ...])`.
- Produces `resolve_removal_origin(home: Path, name: str, current: InstallationProof | None, captured: TreeCapture) -> OriginEvidence`.

- [ ] **1. Escrever testes falhando da evidência.** Fixture com SQLite migrado e serviços reais: create C → remove M → restore R retorna current R e criação C; ciclo BUNDLED sem create retorna criação None; pendências não ativam identidade. Mais de 100 registros legítimos mantém C. Snapshot/registro corrupto gera `SkillMutationError`, sem evidência permissiva.

```python
def test_restore_preserves_creation_without_history_truncation(self):
    # Fixture publica C e executa 51 ciclos reais remove/restore até R.
    evidence = self.repo.installation_evidence(self.name)
    self.assertEqual(evidence.current.operation_id, self.restored.operation_id)
    self.assertEqual(evidence.historical_creation_id, self.created.operation_id)
    self.assertEqual(evidence.pending_operation_ids, ())
```
- [ ] **2. Executar RED.** `uv run pytest -q tests/test_skill_identification_evidence.py`; falha por contrato/consulta ausentes.
- [ ] **3. Implementar dataclasses, carregador e helper comuns.** Extrair de `_projection` o único loop para `_project_committed_records`; não manter o loop antigo junto de um loop de evidência. CREATE atribui historical_creation ao registro novo; ROLLBACK limpa; REMOVE conserva; RESTORE conserva. BUNDLED sem CREATE mantém None. Não limpar histórico só porque current=None, nem reaproveitar criação anterior após rollback/recreate. `_projection` toma os três primeiros valores; installation_evidence transforma os quatro em IDs e usa as pendências do carregador. Não usar history limitado nem reler pending/timeline; conservar validações existentes. Nenhum PRAGMA/migrate/register_skill_writer.
- [ ] **4. Escrever testes falhando da resolução.** Usar bundle temporário real e patch somente `trusted_bundle_root`: manifest ausente/v1 válido/nome não inventariado/árvore divergente resultam sem autoridade; v2 malformado/duplicado, symlink/hardlink e corrida de captura geram erro. Current de raiz trocada retorna identity_valid=False e não tenta bundle; current correspondente retorna sua proveniência mesmo após edição interna.
- [ ] **5. Implementar `resolve_removal_origin`.** Aplicar exclusivamente a tabela canônica abaixo; negativos usam authorization_basis=none. Cada negativo nasce de branch explícita de observação estável. A descoberta segura de bundle retorna ausência estruturada quando o nome não está no inventário, sem converter falhas de abertura/captura em ausência. Não classificar por mensagem de exceção nem capturar genericamente conflict. Manter atestação/recaptura; `prove_removal_origin` chama resolver, devolve proveniência positiva, transforma somente negativos normais em conflict e propaga erros.
- [ ] **5b. Testar equivalência e compartilhamento por comportamento.** `test_resolve_and_prove_agree_for_every_normal_outcome` parametriza todas as respostas normais da tabela com filesystem/repositório reais e patch somente da raiz confiável. Em respostas normais, `resolve.provenance is not None` ⇔ prove retorna essa mesma proveniência; `resolve.provenance is None` ⇔ prove levanta conflict. `test_resolve_and_prove_preserve_attestation_errors` cobre os casos de erro: ambos levantam o kind correspondente, sem resposta normal do resolver. Não afirmar a recíproca global “prove conflict ⇒ resolve negativo”, pois erros também usam conflict. `test_projection_and_evidence_share_transition_results` verifica `_projection` e installation_evidence após cada transição create/remove/restore/rollback/recreate, inclusive histórico longo; não ler código-fonte nem testar chamadas internas como substituto do comportamento.
- [ ] **6. Executar GREEN e regressão.** `uv run pytest -q tests/test_skill_identification_evidence.py tests/test_skill_removal_service.py tests/test_skill_removal_projection.py`; todos passam, remove continua recusando os mesmos cenários e nenhuma autorização nova é concedida.
- [ ] **7. Commit.** `feat(skills): resolve evidências de instalação sem alterar remoção` com os quatro arquivos próprios. Revisar antes de T2.

### Tabela canônica de classificação e aceite T1

Os casos de manifesto/bundle só são avaliados sem current; uma identidade
corrente comprovada não depende do manifesto. Não autorizar a raiz substituída
por fallback no bundle. A categoria é definida pelo caminho de código, nunca
pelo texto da exceção. Apenas negativos normais retornam OriginEvidence sem
proveniência; erros jamais retornam relatório unknown.

| Condição observada | Categoria e resultado | Razão/base | Wrapper prove |
|---|---|---|---|
| Current validado, raiz correspondente | Positivo, proveniência corrente | proven_write | Mesma proveniência |
| Current validado, raiz diferente mas estável | Negativo, identity_valid=False | replaced_root | conflict, sem tentar bundle |
| Sem current, manifesto regular ausente | Negativo | unknown_origin | conflict |
| Manifesto v1 integralmente válido | Negativo | legacy_manifest | conflict |
| Manifesto v2 válido, nome ausente (inclusive manifesto vazio) | Negativo | not_in_manifest | conflict |
| Nome no v2, bundle seguro disponível, nome não inventariado | Negativo | not_in_package_bundle | conflict |
| Nome único no bundle, ambas as árvores seguras/estáveis, inventário ou bytes distintos | Negativo | bundle_mismatch | conflict |
| V2 íntegro e árvores completas correspondentes | Positivo BUNDLED | verified_bundle | BUNDLED |
| UTF-8 inválido, nome/hash inválido, duplicata ou mistura v1/v2 | Erro: manifesto inválido/corrompido | Sem OriginEvidence | Propagar conflict |
| Bundle ausente/ilegível, inseguro ou com nomes duplicados/inválidos | Erro: fonte não atestável | Sem OriginEvidence | Propagar kind de acesso/atestação |
| Symlink/hardlink, árvore insegura ou limites excedidos | Erro: segurança | Sem OriginEvidence | Propagar conflict |
| Primitiva de atestação indisponível | Erro: indisponibilidade | Sem OriginEvidence | Propagar unavailable |
| Captura/cadeia/manifesto/identidade muda durante observação | Erro: instabilidade concorrente | Sem OriginEvidence | Propagar conflict |

V1 válido: todas as linhas significativas são nomes válidos e únicos, sem
separador/hash. Ignorar comentários/linhas em branco conforme parser existente;
mistura com entradas v2 é erro. Vazio é conjunto sem entradas, não origem
legada comprovada. A equivalência compara execuções sobre fixtures estáveis;
corridas induzidas pertencem aos testes de erro. Aceite exige toda a tabela,
regressões e ausência de efeitos, não apenas testar wrapper contra um mock do
próprio resolver.

## Tarefa 2: serviço identify com captura somente leitura

**Files:**
- Create: `kairos_skills/identification.py`.
- Modify: `kairos_skills/identification_contract.py` (propriedade transferida após T1).
- Test: `tests/test_skill_identification.py`.
- Modify: `tests/test_container.py` (`RealImageTests.run_in`, teste real de identify).
- Existing regression: `tests/test_skill_removal_io.py`, `tests/test_skill_removal_projection.py`.

**Interfaces:**
- Consumes: contratos T1; `database_connection(home, write=False)`, `read_skill_repository`, `open_directory(stack, path, create=False)`, `check_chain`, `observed_mount_id`, `capture_skill(parent, name)`.
- Produces `read_installation_capture(home: Path, name: str) -> TreeCapture` e `identify_skill(home: Path, name: str) -> SkillIdentification`.
- Produces frozen `SkillIdentification` fields: `name: str`, `historical_creation_origin: str`, `installation_source: str`, `authorization_basis: str`, `ownership: str`, `current_installation_id: str | None`, `historical_creation_id: str | None`, `current_removal_id: str | None`, `pending_operation_ids: tuple[str, ...]`, `identity_valid: bool | None`, `known_protection: tuple[str, ...]`, `protection_coverage: str`, `removivel: bool`, `reasons: tuple[str, ...]`.

- [ ] **1. Escrever testes falhando do serviço.** USER criada/restore editada usa user/local/proven_write; BUNDLED exato sem create usa unknown/package_bundle/verified_bundle; restore BUNDLED usa unknown/package_bundle/proven_write. Legado e frontmatter/source declarados usam unknown/unknown/none, ownership=user conservador, identity_valid=None. Raiz trocada perde autoridade e source corrente passa a unknown; IDs históricos identificam prova anterior, sem atribuir a autoria anterior à árvore substituta. Pending torna removivel=False e acrescenta `pending_operation`, sem reconciliar.

```python
def test_unknown_origin_is_diagnostic_and_preserves_home(self):
    before = self.home_inventory()  # Nomes, bytes, modos e identidades.
    report = identify_skill(self.home, self.name)
    self.assertEqual(report.historical_creation_origin, "unknown")
    self.assertEqual(report.authorization_basis, "none")
    self.assertFalse(report.removivel)
    self.assertIsNone(report.identity_valid)
    self.assertEqual(self.home_inventory(), before)
```
- [ ] **2. Executar RED.** `uv run pytest -q tests/test_skill_identification.py`; falha por serviço ausente.
- [ ] **3. Implementar leitura por descritores.** Abrir home/skills com create=False, guardar/revalidar cadeia e IDs de montagem antes/depois da captura. Não instanciar SkillMutationFiles, SkillRemovalFiles, SkillMutationService, lock ou capability de criação. Erros FS mapeiam ao contrato existente; alvo ausente é conflict, primitiva indisponível unavailable.
- [ ] **4. Implementar composição `identify_skill`.** Validar nome antes de I/O. Consultar evidência, capturar árvore, resolver T1, consultar novamente evidência por nova cópia canônica, recapturar árvore antes de retornar. Divergência DB/árvore entre observações gera SkillMutationError(kind="conflict") por alteração concorrente: exit 1, nenhum relatório unknown, sem classificá-la como corrupção. Emitir diagnóstico sanitizado de alteração durante consulta e possibilidade de repetir; não incluir caminhos privados. Ausência de banco significa evidência vazia, não banco novo. Não anunciar atomicidade DB+FS. Valores known_protection=() e protection_coverage=`namespace_and_recorded_only` expressam que A1 não lê policy nem autentica mirrors planos. USER histórico só é atribuído à árvore cuja identidade prova a linhagem; raiz trocada mantém IDs anteriores, mas historical_creation_origin=unknown para a árvore atual.
- [ ] **5. Escrever testes de ausência de efeitos/concorrência.** Comparar nomes, conteúdo, modos e identidades antes/depois em DB fechado sem WAL e em conexão aberta com prova somente no WAL. Reader não registra escritor; DB/WAL/SHM, lock e staging intactos. Commit real durante cópia (padrão test_skill_removal_projection) recusa instabilidade. Mudança de árvore/evidência entre leituras recusa. Home ausente permanece ausente. Prepared real fica prepared, sem publicação/limpeza. Não afirmar atime imutável.
- [ ] **6. Executar GREEN e regressão.** `uv run pytest -q tests/test_skill_identification.py tests/test_skill_identification_evidence.py tests/test_skill_removal_io.py tests/test_skill_removal_projection.py`; todos passam. Exercitar binário, modo executável e diretório vazio na captura real, sem semver no legado.
- [ ] **6b. Exercitar imagem real.** Acrescentar `RealImageTests.test_identify_reads_tree_in_real_image_without_mutation`, com `run_in` executando Python instalado na imagem em home temporário: publicar USER via serviço real, identificar; criar legado com binário/diretório vazio, identificar; substituir raiz USER por cópia byte-idêntica e identificar sem autoridade. Comparar arquivos/identidades antes/depois de cada leitura. Reconstruir `kairos:test` e executar o nodeid; nenhuma produção é usada.
- [ ] **7. Commit.** `feat(skills): identifica instalações com leitura segura` e revisão antes de T3.

## Tarefa 3: CLI identify e documentação de uso

**Files:**
- Create: `kairos_cli/skill_identification.py`.
- Modify: `kairos_cli/commands.py`, `kairos_cli/main.py`, `kairos_cli/handlers.py`.
- Modify: `docs/decisoes.md` (exemplos/limites propostos em T3; status A1 somente no registro documental da conclusão validada após merge).
- Test: `tests/test_cli_skill_identification.py`.
- Existing regression: `tests/test_cli_skills.py`, `tests/test_cli_skill_mutations.py`, `tests/test_cli_skill_removal.py`.

**Interfaces:**
- Consumes: `identify_skill(home, name) -> SkillIdentification` de T2.
- Produces `identification_metadata(report: SkillIdentification) -> dict[str, object]` e `run_skill_identification(home: Path, args: argparse.Namespace) -> int`.
- CLI positional `name`; `--json` existente. Roteamento separado, sem passar por validação/escrita de mutações.

- [ ] **1. Escrever testes falhando da CLI.** Main com KAIROS_HOME isolado: identify exige nome, unknown retorna 0 e JSON negativo; USER comprovada retorna IDs verdadeiros; help anuncia somente identify além dos comandos já entregues. Nome inválido/_org retorna 2 sem I/O; alvo/home ausente retorna 1 sem criar pais. Corrupção gera stderr sanitizado e nenhum JSON de sucesso; primitive unavailable=69.
- [ ] **1b. Fixar compatibilidade de nomes.** `test_list_shows_legacy_name_but_identify_rejects_before_io`: diretório Legacy_Skill/SKILL.md real aparece em list; identify desse nome retorna 2 e não abre o reader (substituir apenas abertura do reader por função que falha se chamada). Inventário do home permanece igual. Mensagem explica que identify usa nomes kebab-case; documentar a limitação sem sugerir rename automático ou ampliar o parser. Testar conteúdo legado sem semver com nome válido separadamente: esse caso identifica normalmente.

```python
def test_cli_unknown_has_successful_negative_diagnostic(self):
    code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
    self.assertEqual(code, 0)
    self.assertEqual(stderr, "")
    metadata = json.loads(stdout)
    self.assertEqual(metadata["authorization_basis"], "none")
    self.assertFalse(metadata["removivel"])
    self.assertEqual(metadata["protection_coverage"], "namespace_and_recorded_only")
```
- [ ] **2. Executar RED.** `uv run pytest -q tests/test_cli_skill_identification.py`; parser/handler faltante.
- [ ] **3. Registrar e compor CLI.** commands registra identify implementado; `_extra_args` em main adiciona NOME; cmd_skills encaminha ao módulo leitor próprio. Emitir campos T2 via json.dumps ensure_ascii=False, tuple como arrays. Em texto mostrar nome, três dimensões, identidade e motivos; explicar que removivel é diagnóstico e cobertura organizacional limitada. Não imprimir conteúdo, hashes/home IDs, device/inode, caminhos privados ou exceção bruta. Mensagens de erro seguem padrão SkillMutationError/OSError/sqlite3.DatabaseError e códigos 2/77/69/1; exceções não viram unknown normal.
- [ ] **4. Acrescentar subprocesso real e privacidade.** Usar sys.executable chamando kairos_cli.main:main (não há kairos_cli.__main__). USER criada por CLI e legado anônimo; verificar exit/stdout JSON, estado do home antes/depois. SKILL.md com sentinela secreta nunca aparece em resposta/erro. Testar JSON ausente em corrupção, opção desconhecida e comando anterior preservado. `test_concurrent_observation_change_exits_one_without_success_json` induz mudança real entre leituras T2: code=1, stdout vazio, stderr explica alteração concorrente sanitizada; não imprime unknown nem acusa corrupção.
- [ ] **5. Documentar interface proposta A1.** Em D-CLI.SKILL.PROVENANCE acrescentar exemplos identify nome/--json, cobertura limitada, nomes legados e ausência de efeitos, preservando status de proposta. Não escrever “entregue” ou “implementado na fase A1” neste commit. A2/B continuam propostas; status A1 pertence à conclusão validada após gate final e merge.
- [ ] **6. Executar GREEN e regressão.** `uv run pytest -q tests/test_cli_skill_identification.py tests/test_cli_skills.py tests/test_cli_skill_mutations.py tests/test_cli_skill_removal.py`; todos passam.
- [ ] **7. Commit.** `feat(cli): expõe identificação somente leitura de skills`; revisão de tarefa e da branch completa.

## Gate final A1 e entrega

- [ ] `git diff --check`, `uv run ruff check .`, `uv run ruff format --check .`.
- [ ] `scripts/ci.sh` com binário Codex 0.154.0 e imagens exigidas disponíveis; revisar skips reais e rodar opt-ins pertinentes. runtime_live continua excluído sem credenciais. Não concluir teste de imagem a partir de skip.
- [ ] Exercitar capture/identify na imagem kairos:test com fixtures de USER, legado e root substituída; verificar leitura de toda árvore e ausência de efeitos. Reutilizar contexto RealImageTests sem criar testes que leiam fonte.
- [ ] Abrir PR A1, revisão independente final e jobs de verificação verdes antes de merge. Não implementar A2/B como solução para falhas A1.
- [ ] Após merge, conferir HEAD~1..HEAD para remoções acidentais e seguir o gate de revisão de A1 antes de escrever plano A2.
- [ ] Somente após conclusão validada e merge, registrar em atualização documental D-CLI.SKILL.PROVENANCE “implementado na fase A1”, com commit/PR e evidências da validação. Não marcar toda a decisão como entregue: adoção A2 e política/publicação B continuam propostas. A atualização documental segue os gates de entrega aplicáveis, sem antecipar status em T3.

## Handoff

Método multiagente já escolhido pela usuária. Este plano precisa de revisão
da usuária antes de iniciar código; aprovação da spec não substitui o plano.
