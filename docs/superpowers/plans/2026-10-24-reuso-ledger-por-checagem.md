# Reuso do ledger por checagem — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para implementação nativa e sequencial. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** reduzir reconstruções repetidas de registros legados nas transações próprias de `prepare`/`finish`, preservando os validadores e comprovando o ganho.

**Architecture:** memo privado e limitado a uma tentativa de BEGIN IMMEDIATE, habilitado explicitamente só nos dois callbacks admitidos. A timeline continua integral; hits substituem somente reconstruções de registros imutáveis. Mudança SQL invalida a geração; observação ambígua nunca concede autoridade.

**Tech Stack:** Python 3.11, SQLite real, unittest/pytest, cProfile, Docker/CI existentes; dependências e schema inalterados.

**Spec:** [desenho aprovado](../specs/2026-10-24-reuso-ledger-por-checagem-design.md), conteúdo aprovado `abeee0d1f3fccc38e2271b9a4abd47851fe34da2`.

**Estado:** desenho escrito aprovado pela usuária em 24/10/2026. Execução nativa autorizada pela usuária; T1/T2 implementadas. T3 teve duas rodadas documentadas e o gate de consistência reprovou a candidata; aceite integrado permanece pendente. Método nativo preserva a preferência permanente de agente único. Baseline do PR #116 em `6e5ce1a` permanece intacta; não pressupor seu merge. Antes da execução, conferir a main e escolher base compatível sem perder a spec ou os dados históricos.

## Global Constraints

- “As chamadas e os corpos de `validate_skill_timeline`, dos validadores de schema, da capability de escrita e dos guards de política continuam intactos.”
- “`_load_validated_records` continua enumerando todos os IDs e só filtra por nome depois.”
- “Nada é compartilhado entre operações CLI, conexões, homes, processos, tentativas de retry ou transações. Sem cache global, TTL ou persistência.”
- “Habilitar somente os callbacks SQL privados de `prepare` e `finish` da família legada.” Padrão desabilitado para os outros callbacks e famílias.
- “Ausência, exceção, listas de pendências, projeções de instalação, árvores, adoções, publicações e decisões de política não são memoizadas neste incremento.”
- Limite inicial: 1.024 registros por observação; saturação deixa novos IDs como misses e não remove entradas válidas. Sem knob público/flag de produção.
- Benchmark B2 comparável mantém 20 validações da timeline. Gate: três pares AB/BA/AB, sete amostras nos cenários 0/40/120; cenário 120 melhora nos três pares e mediana de ganhos ≥10%; recusar cenários 0/40 se regressão mediana superar simultaneamente 5% e 5 ms.
- SQLite/arquivos reais, sem teste que leia fonte Python ou congele o número de chamadas do runtime. Tempos sem profiler/cobertura; perfil separado. Nenhum dado, credencial ou provider de produção na medição.
- Erros inesperados propagados ou categorizados com contexto sanitizado. Sem converter falha em sucesso ou relaxar guard para obter ganho. Sem migração, thread, nova ferramenta de modelo ou alteração de política/autoridade.

## Review Focus

1. DML próprio, DDL e rollback não podem ressuscitar estado antigo; provar em Task 1 e pela transição real PREPARED→COMMITTED em Task 2.
2. Conn/home/modo diferentes, TEMP/ATTACH ou transação externa não recebem hits; Task 1 cobre chaves/eligibilidade e Task 2 cobre o dono real.
3. Mudança durante lote não pode liberar uma tupla parcialmente cacheada; Task 1 descarta o resultado e Task 2 mantém a recusa de corrupção em outro nome.
4. Banco/WAL byte-idêntico em outro inode não é continuidade nem prova de autoridade; Tasks 1/2 distinguem miss de guard de identidade, sem prometer isolamento contra o mesmo UID.
5. Contagem de entradas na função, medianas arredondadas ou dados incompletos não podem aparentar ganho; Task 3 mede o carregador efetivo e testa a decisão do gate sobre amostras brutas.

---

## Mapa de arquivos

| Arquivo | Responsabilidade |
|---|---|
| Criar `kairos_state/repositories/skill_record_memo.py` | stamp SQL/namespace, geração, chave, limite e descarte do memo |
| Modificar `kairos_state/repositories/skill_mutations.py` | dono da transação e carregador; só ativação privada em prepare/finish |
| Criar `tests/skill_record_memo_fixtures.py` | homes/SQLite/árvores reais e loader contado que executa a API real |
| Criar `tests/test_skill_record_memo.py` | mecânica de observação, invalidação, lote, isolamento e limite |
| Criar `tests/test_skill_record_memo_integration.py` | API real, transações, callbacks excluídos, corrupção, política e identidade |
| Modificar `scripts/benchmark-skill-writers.py` | perfil adicional de reconstruções; protocolo temporal inalterado |
| Criar `scripts/benchmark_skill_writer_pairs.py` | execução pareada e decisão pura de aceitação, importável nos testes |
| Criar `tests/test_skill_writer_benchmark_pairs.py` | dados incompletos/adulterados e fronteiras do gate |
| Criar `docs/superpowers/reports/2026-10-24-reuso-ledger.md` e diretório `2026-10-24-reuso-ledger/` ao lado | resultados brutos dos três pares, ambiente, hashes, revisão e limites; sem substituir a baseline antiga |

### Task 1: observação e memo limitado com SQLite real

**Files:** helper e os dois arquivos de fixture/teste unitário do mapa. Não integrar no repositório ainda.

**Interfaces produzidas em `skill_record_memo.py`:**

- `DatabaseStamp`: dataclass frozen com `sql_epoch: tuple`, `files: tuple | None`, `eligible: bool`. `sql_epoch` distingue estado transacional, total_changes, data_version, cookie main, database_list e read_uncommitted; `files` contém somente observações de nomes, não dados/autoridade.
- `observe_database_stamp(connection: sqlite3.Connection) -> DatabaseStamp`: observa sem escrever SQLite. TEMP/ATTACH, memória, nome/entrada insegura ou indisponível tornam o stamp inelegível; não mascarar erro inesperado. Não criar TEMP ao observar seu cookie.
- `RecordMemo(connection: sqlite3.Connection, home_id: str, *, owner_token: object, limit: int = 1024)`; `entry_count: int` somente leitura; `close() -> None` descarta e desativa incondicionalmente, sem commit/rollback.
- `load(operation_id: str, *, modern: bool, loader: Callable[[], CommonRecord | None]) -> CommonRecord | None`: IDs validados; armazenar só tipo exato `SkillMutationRecord` positivo e da mesma chave/home. Não armazenar None/erro/famílias excluídas.
- `run_batch(operation: Callable[[], T], *, uncached: Callable[[], T]) -> T`: atesta antes/depois do lote puro. Sem lote, cada acesso reconfere o stamp. Token/conexão/home pertencem ao memo, sem compartilhamento.

**Fixtures produzidas:** `RecordMemoFixture(unittest.TestCase)` fornece `home`, `db`, `repo`, `service`, `source`, `records` de três criações COMMITTED pela API real, `loads` por ID; `memo_scope(*, limit=1024)` abre BEGIN IMMEDIATE em banco temporário, cria token novo e fecha memo antes de rollback. `load_record(memo, record, *, modern=True)` conta a execução do loader real `repo._get_validated(...)`, sem substituí-lo por DTO fixo. T2 reutiliza a fixture, mas exercita o dono de produção em vez de `memo_scope`. `CommonRecord` já existe em `kairos_skills.removal_contract`; `T` nas assinaturas é um TypeVar genérico local, não outro contrato de domínio.

- [x] **RED de reuso:** criar `test_same_observation_reuses_one_real_reconstruction` e `test_close_drops_entries_and_disables_reuse`. No primeiro, duas leituras elegíveis retornam o mesmo valor e `self.loads[id] == 1`; após close uma leitura retorna o valor real e aumenta o contador. Executar `.venv/bin/python -m pytest -q tests/test_skill_record_memo.py`; esperar falha pelo helper ausente, sem tomar erro de fixture como prova funcional.

```python
def test_same_observation_reuses_one_real_reconstruction(self):
    record = self.records[0]
    with self.memo_scope() as memo:
        first = self.load_record(memo, record)
        second = self.load_record(memo, record)
        self.assertEqual(first, second)
        self.assertEqual(self.loads[record.operation_id], 1)
```
- [x] **GREEN do núcleo:** implementar as interfaces acima, chave token/conexão/home/modo/ID/geração e limite. Para arquivos, observar presença, tipo, nlink, device/inode e stamps dos nomes main/WAL/SHM/journal. A observação não prova o inode usado pelo SQLite; não substitui guards externos. Suporte não comprovado resulta em loader real, nunca em resultado antigo.
- [x] **RED de invalidação:** adicionar os casos da tabela abaixo; rodar o arquivo antes do ajuste correspondente e registrar a asserção que falhou. DML usa tabela auxiliar real na mesma conexão; erro/corrupção usa loader real do ledger. Não alterar transações por fora do dono para fingir um escopo suportado.

| Teste | Asserção necessária |
|---|---|
| `test_own_dml_and_ddl_require_fresh_load` | após INSERT e após DDL, contador do loader aumenta e a geração antiga não retorna; total_changes isolado não basta |
| `test_rollback_and_new_scope_do_not_reuse_old_entries` | escopo seguinte recarrega, mesmo que os dados voltem ao valor anterior |
| `test_connection_home_mode_and_unsupported_namespaces_do_not_share` | conexões/homes/modo distintos não obtêm o primeiro resultado por hit; TEMP/ATTACH/memória/read_uncommitted usam o caminho real |
| `test_none_errors_and_other_record_families_are_not_cached` | repetir ausência/erro chama loader de novo; família excluída não ocupa entrada; exceção inesperada não vira sucesso |
| `test_limit_preserves_semantics_without_unbounded_growth` | com limite de fixture 2 e três registros: `entry_count <= 2`, os três valores continuam corretos e ID excedente recarrega |
| `test_replaced_database_and_sidecars_disable_reuse` | cópia byte-idêntica em inode diferente e transição presença/ausência invalidam; loader real volta a executar; não confundir sua visão SQLite com autoridade física |
| `test_batch_discards_partial_cached_values_on_change` | nunca retorna parte cacheada como observação válida após mudança; SQL alterado inesperadamente recusa com conflito; mudança só física descarta o lote e o refaz sem memo |
| `test_unexpected_observation_error_propagates_sanitized` | erro SQLite mantém o fluxo de retry/erro existente; OSError inesperado resulta em erro io sanitizado, sem caminho privado |

- [x] **Fixar o algoritmo de lote:** entrada e saída reconferem SQL/namespace; dentro do laço só loaders privados de leitura, sem efeito/callback externo. Mudança de DML/DDL/controle transacional inesperada durante o lote levanta `SkillMutationError(kind="conflict")`, antes de expor o resultado. Se apenas a prova física ficar indisponível/mudar, desativar o memo da tentativa, descartar o resultado parcial e executar `uncached()` sobre a mesma conexão/snapshot, sem reuso. Erro inesperado continua erro; não há catch genérico para fazer fallback.
- [x] **GREEN e commit:** repetir o arquivo com todos os casos; `.venv/bin/ruff check kairos_state/repositories/skill_record_memo.py tests/skill_record_memo_fixtures.py tests/test_skill_record_memo.py`, Ruff format --check e `git diff --check`. Esperar zero falhas; commit `perf(skills): delimita observação de registros validados`.

### Task 2: habilitação privada em prepare/finish e equivalência integrada

**Files:** modificar `skill_mutations.py`; criar `tests/test_skill_record_memo_integration.py`; consumir fixture e interfaces T1. Usar testes existentes de mutation state/service/recovery/rollback, org services/state e publication state/authority como regressão, sem enfraquecê-los.

**Interfaces produzidas/modificadas:**

- `SkillMutationRepository._write(operation: Callable[[], T], *, memo_records: bool = False) -> T`. Só `prepare` e `finish` legados passam True. Wrappers de árvore/adoção e callbacks genéricos mantêm False. A recusa de transação já aberta ocorre antes de criar qualquer memo.
- Campo privado `_record_memo: RecordMemo | None`, inicializado None, e `_get_validated_uncached(operation_id: str, *, modern: bool) -> CommonRecord | None`, contendo o corpo atual do carregador, sem alterar consultas/validações.
- `_get_validated` permanece ponto de entrada: sem dono elegível delega ao uncached; dentro do dono usa `memo.load(..., loader=lambda: self._get_validated_uncached(...))`.
- `_load_validated_records` preserva todos os IDs e o filtro final; usa `run_batch` somente sobre o laço síncrono de carregamentos. O callback `uncached` repete esse laço com o carregador uncached e sem resultados parcialmente reutilizados.

- [x] **Baseline:** executar `.venv/bin/python -m pytest -q tests/test_skill_mutation_state.py tests/test_skill_mutation_service.py tests/test_skill_mutation_recovery.py tests/test_skill_mutation_rollback.py tests/test_skill_org_services.py tests/test_skill_org_state.py tests/test_skill_publication_state.py tests/test_skill_publication_authority.py`; esperar aprovação antes da integração. Falha preexistente exige diagnóstico, não aprovação presumida.
- [x] **RED do dono real:** `test_prepare_reuses_records_but_runs_each_timeline_validation` cria prefixo e novo add pela API real; spy chama o carregador real e verifica que a segunda varredura pré-INSERT evita reconstruções, enquanto sentinelas dos validadores continuam executando. `test_finish_returns_committed_after_its_insert` exige resultado COMMITTED, eventos preparados/concluídos reais e conteúdo publicado; nunca o PREPARED anteriormente memoizado. Não congelar 20 em unittest.

```python
def test_finish_returns_committed_after_its_insert(self):
    self.source.write_text(creation_text(name="new-target"))
    record = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
    self.assertEqual(record.state, SkillMutationState.COMMITTED)
    states = [row[0] for row in self.db.execute(
        "SELECT state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence",
        (record.operation_id,),
    )]
    self.assertEqual(states, ["prepared", "committed"])
    self.assertEqual((self.home / "skills/new-target/SKILL.md").read_bytes(), self.source.read_bytes())
```
- [x] **Implementar o dono:** depois de BEGIN IMMEDIATE bem-sucedido criar token novo e memo somente quando explicitamente admitido. Executar o callback; fechar/remover memo em finally **antes** de commit/rollback. Retry recebe objeto novo. Importar o helper tardiamente no caminho de escrita; não adicionar cache a leitores avulsos. Preservar register_mutation_writer, Budget, ordem de SQL, erros e todos os guards.
- [x] **RED de isolamento/recuperação:** acrescentar testes abaixo, com chamadas reais e fault injection nas fronteiras; executar os casos novos antes de corrigir cada lacuna.

| Teste | Asserção necessária |
|---|---|
| `test_external_transaction_and_other_write_callbacks_never_enable_memo` | transação externa continua recusada por _write; callbacks genéricos/famílias excluídas não ganham hits |
| `test_memo_is_closed_before_commit_rollback_and_retry` | recolher observações e validá-las fora de callbacks SQLite; erro/rollback/retry não retêm entrada; não usar asserção dentro de trace callback que SQLite possa engolir |
| `test_corrupt_other_name_and_changed_triggers_still_refuse` | memo populado não oculta bytes/eventos inválidos em outro nome nem schema adulterado; erro categorizado e nenhum novo efeito |
| `test_policy_change_after_staging_still_vetoes_effects` | política/binding mudados na fronteira real recusam publicação e conservam versões conforme os testes atuais |
| `test_real_writer_contention_restarts_with_empty_observation` | duas conexões/processos reais exercitam BEGIN/retry; estado confirmado pelo outro escritor é observado no novo dono |
| `test_database_identity_guard_still_refuses_after_replacement` | caminho seguro database_connection e troca byte-idêntica real mantêm a recusa; não exigir que memo sozinho autentique o inode da conexão |

- [x] **GREEN da vertical:** repetir os dois novos arquivos e o comando de baseline. Rodar também as famílias completas `tests/test_skill_adoption_*.py`, `tests/test_skill_removal_*.py`, `tests/test_skill_publication_*.py` e CLI de skills; preservar resultados/autoridade e recusa de clientes antigos. Corrigir com RED→GREEN somente o que esta integração afetar.
- [x] **Lint e commit:** Ruff check/format e diff-check dos arquivos; commit `perf(skills): reutiliza registros em prepare e finish próprios`. Conferir que validadores, capability, serviços, schema e abertura segura não tiveram alterações de comportamento; nenhuma liberação intermediária como ganho comprovado.

### Task 3: benchmark pareado, avaliação do ROI e aceite integrado

**Files:** script writer existente, novo `scripts/benchmark_skill_writer_pairs.py`, teste puro de gate e relatório/dados do mapa. Não mudar contratos de runtime para fazer o benchmark passar.

**Interfaces:** manter CLI atual de `benchmark-skill-writers.py`; acrescentar ao perfil `_get_validated_uncached`, `_legacy_get`, `_base`, `_blob` e observador de stamp, por nome/arquivo. Novo módulo fornece `compare_pairs(pairs: list[dict], *, baseline_sha: str, candidate_sha: str) -> dict`, com `accepted: bool`, `failures: list[str]`, `paired_gain_percent: dict[int, list[float]]` e `median_gain_percent: float`. Cada par é `{"index": 1, "order": ["baseline", "candidate"], "baseline": H, "candidate": H}`, onde H é um output completo do harness com exatamente um case; o segundo par inverte order e o terceiro repete o primeiro. Dados estruturalmente inválidos levantam ValueError; medições válidas que descumprem guard/performance retornam accepted=False. CLI: `python -m scripts.benchmark_skill_writer_pairs --baseline SHA --candidate SHA --output-dir PATH`; chama o harness atual em seis execuções sequenciais e salva `pair-N/{baseline,candidate}.{json,log}` mais summary.json com referências aos brutos e resultado. Exit 0 só com accepted=True; falha deixa resultado de erro explícito e exit não zero.

- [x] **RED do gate:** importar a função, sem ler seu texto, em `tests/test_skill_writer_benchmark_pairs.py`. `test_requires_three_complete_compatible_pairs` recusa par ausente/duplicado, SHA/schema/harness incompatível, amostra faltante, NaN/infinito/não positivo e mediana adulterada. `test_rejects_gain_that_skips_validation` recusa candidata rápida com validação diferente da baseline contratual 20. `test_applies_paired_thresholds_without_rounding_or_best_run_selection` cobre exatamente ≥10%, melhora em todos os pares e a conjunção >5% **e** >5 ms nos cenários menores; reprovação de um par não some por uma rodada melhor.

O fixture local desse teste fornece `self.pairs` com três outputs completos e compatíveis do formato do harness, sete amostras sintéticas finitas por cenário, baseline 100 ms e candidata 90 ms, validações completas e SHAs hexadecimais distintos em `self.baseline_sha`/`self.candidate_sha`. Não é necessário medir relógio para testar a regra de aceitação.

```python
def test_complete_paired_gain_is_accepted(self):
    result = compare_pairs(self.pairs, baseline_sha=self.baseline_sha, candidate_sha=self.candidate_sha)
    self.assertTrue(result["accepted"])
    self.assertGreaterEqual(result["median_gain_percent"], 10)
```
- [x] **Implementar a comparação:** recomputar medianas das sete amostras brutas; não confiar só no campo pronto do JSON. Por cenário e par, ganho é `100*(baseline-candidate)/baseline`; aceitar 120 somente com três ganhos positivos e mediana ≥10. Em 0/40 usar medianas das diferenças pareadas percentuais e absolutas; recusar quando ambas excederem seus limites. Não arredondar antes de julgar. Registrar falhas explicitamente, sem erro convertido em accepted.
- [x] **Implementar o driver:** ordem AB/BA/AB, schemas 11, prefixes 0/40/120 e samples 7 em ambos os lados. Mesmo executável/ambiente/harness; revisar SHA por git sem shell; verificar hash do script comum e módulos carregados; saída inválida ou subprocesso falho encerra com erro. Não expor flag de memo no produto.
- [x] **GREEN do diagnóstico e commit:** executar teste do gate; probe sintético curto do harness em candidata e baseline, fora dos dados definitivos. Ruff check/format e diff-check; commit `test(skills): compara custo pareado e preservação dos validadores`. Fixar o SHA candidato antes de medir; nenhum código pode mudar no meio da rodada.
- [ ] **Executar a rodada definitiva:** `.venv/bin/python -m scripts.benchmark_skill_writer_pairs --baseline b7346adeee312368746fddb3ca7cfe3058c49b99 --candidate SHA_FIXADO --output-dir .superpowers/sdd/2026-10-24-reuso-ledger/paired`. Esperar três pares completos, seis execuções, fixtures íntegros e gate verdadeiro. Não rodar outros probes durante os tempos. Incidente comprovado invalida o par completo, que permanece registrado. Gate falso/inconclusivo não autoriza ampliar escopo, mudar limiar ou indicar merge.
- [x] **Relatar os resultados:** copiar logs/JSONs de medição sem dados privados para o diretório de relatório; registrar comandos, SHAs/hashes, ambiente/carga, dispersão, hits/misses derivados e redução de reconstruções efetivas. Os 903 acessos ao wrapper podem permanecer; não descrevê-los como 903 reconstruções se houver hits. Custo do stamp e saturação precisam aparecer. Preservar o JSON histórico do #116, cujo campo script_sha256 é d93b41e…; o novo harness tem hash próprio e é idêntico para os dois lados do comparativo.
- [ ] **Validar a integração:** `env -u SONAR_TOKEN bash scripts/ci.sh` completo com Python/Codex pinados conforme projeto; opt-ins Docker pertinentes, imagem reconstruída com aceite instalado, lint, lock e frontends. O scanner local pulado não recebe aprovação presumida; análise Sonar será conferida remotamente. Conservar skips de pré-requisito e falhas observadas como tais. Reutilizar somente evidências válidas para fontes/configuração/ambiente atuais; não acionar providers/runtime_live sem credenciais/opt-in.
- [ ] **Revisão e entrega:** revisão independente do diff inteiro e das provas de invalidação; Root resolve achados e confere integração. Publicar PR em português com escopo final, evidências e gate do SHA proposto; conferir jobs remotos e Sonar do head efetivo. Merge/deploy dependem de autorização aplicável à nova revisão e dos gates do AGENTS.md; rollback reverte código, sem migration/estado de cache para restaurar.

## Auto-revisão do plano

Cobertura: T1 possui mecanismo, chaves, limite, stamps, invalidação e lote; T2 possui dono admitido, fim da tentativa, efeitos, famílias excluídas e invariantes de segurança; T3 possui dados comparáveis, gate, erro explícito, CI/imagem e revisão. Os cinco focos têm testes nas tarefas proprietárias. Tipos e nomes consumidos em T2/T3 correspondem às interfaces acima. Nenhum passo depende de alterar os validadores ou o limite aprovado de reuso.

Execução proposta: nativa/sequencial pelo Root, uma única implementação compartilhando os contratos; revisão independente no fechamento. A usuária autorizou esta execução nativa após revisar o plano. O resultado de desempenho está no [relatório](../reports/2026-10-24-reuso-ledger.md) e não autoriza merge. O conteúdo aprovado da spec permanece a fonte das restrições, sem aprovação inferida de resultados futuros.
