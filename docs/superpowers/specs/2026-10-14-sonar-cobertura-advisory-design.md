# SonarQube Cloud como observabilidade de qualidade (advisory)

Status: especificação; aguarda aprovação antes da implementação.
Base: `main` em `d730822`. Correção independente. Não altera garantias de
runtime; é ferramenta de CI.

## Objetivo e problema

Adicionar análise estática contínua ao Kairos para enxergar **cobertura em
código novo**, **duplicação** e **complexidade** por PR — hoje o CI tem 8 jobs
por ferramenta, mas nenhum mede o que um PR novo deixa sem teste. O objetivo é
**observabilidade**, não um segundo lint nem um gate de vaidade.

O repositório é **público**, então o produto correto é o **SonarQube Cloud**
(plano gratuito para projetos públicos; plano OSS para organizações open
source). **Não** entra servidor self-hosted: o Community Build é grátis, mas
analisa só a branch principal (PR/branch é recurso pago), exige Postgres e
upgrade mensal — custo operacional sem retorno para um projeto solo.

## Princípios que a integração precisa respeitar

Do `AGENTS.md`:

- **O Ruff continua sendo a autoridade de lint.** O conjunto de regras do
  `pyproject.toml` é deliberado (import tardio `PLC0415`, `E501`, `skills/`
  fora). O Sonar não pode reabrir esses temas como se fossem defeitos.
- **Comportamento real, não snapshot.** Cobertura não é gate absoluto; vira
  change-detector e incentiva teste de fachada.
- **Gate de merge = CI verde.** O job novo reporta; não bloqueia merge na
  primeira fase.
- **`skills/` é conteúdo de terceiros.** Fora da análise, como já é do Ruff.
- **Actions fixadas por SHA**, nunca por tag.

## Decisões

1. **Análise advisory, gate em "código novo".** O repo tem código legado com
   achados; o Sonar é configurado para medir **Clean as You Code** (só o que o
   PR muda). O job não falha o merge (`sonar.qualitygate.wait=false`) na
   primeira fase; a especificação de como torná-lo bloqueante fica documentada
   para depois de estabilizar.
2. **Cobertura inclui subprocessos.** 44 dos 210 arquivos de teste spawnam
   processos (CLI via `subprocess`, `multiprocessing`, Docker stub). Sem
   instrumentar o filho, o número sai artificialmente baixo e engana. A
   cobertura é gerada com `coverage.py` (>=7.13) em modo paralelo; o próprio
   pacote instala o `.pth` permanente de `process_startup`, ativado por
   `COVERAGE_PROCESS_START`, e os dados são combinados antes do upload.
   *(Divergência de execução — 2026-10-15: a partir do coverage 7.12.1b1/7.13.0
   o `.pth` vem no wheel e é instalado junto, então o enabler manual previsto
   neste spec era redundante e inseria instrumentação dupla. Removido; a
   dependência é pinada em `coverage>=7.13`.)*
3. **Não rodar a suíte duas vezes.** O job `sonar` **consome o `coverage.xml`
   do job `tests`** via artefato; não executa `pytest`. Isso respeita o custo
   do CI atual (8 jobs + build Docker).
4. **Conflito de lint resolvido por exclusão de regras, não por mudar o
   perfil.** No plano gratuito não há perfis de qualidade customizados; a
   calibragem é feita por `sonar.issue.ignore.multicriteria` referenciando as
   decisões já documentadas, e pelo modelo de "código novo".
5. **Sem token, sem scan — nunca falha.** PR de fork não recebe
   `SONAR_TOKEN`. O passo é condicional e, sem token, o job passa sem
   scanear. Não reportar sucesso de análise que não ocorreu (princípio
   "sem efeito real é pior que ausência"): a mensagem deve dizer
   explicitamente que a análise foi pulada.
6. **Paridade local.** `scripts/ci.sh` gera a cobertura (sempre) e roda o
   `sonar-scanner` **somente** se `sonar-scanner` estiver instalado e
   `SONAR_TOKEN` presente; senão, `skip` — mesmo padrão de shellcheck/npm.

## Mudanças de arquivo

| Arquivo | Mudança |
|---|---|
| `pyproject.toml` | `coverage>=7.13` em `[project.optional-dependencies].dev`; bloco `[tool.coverage.*]` |
| `.github/workflows/ci.yml` | job `tests` passa a gerar `coverage.xml`; novo job `sonar`; passo de upload de artefato |
| `sonar-project.properties` (novo) | chaves, fontes, exclusões, novas linhas, gate advisory |
| `scripts/ci.sh` | cobertura + sonar opcional, espelhando o CI |
| `docs/decisoes.md` | decisão nova (`D-CI.SONAR`) com o racional e os limites |
| `tests/test_coverage_harness.py` (novo) | prova comportamental de que o caminho de subprocesso entra na cobertura |

## Contrato do job `sonar`

- `needs: [tests]`, `runs-on: ubuntu-latest`, checkout com `fetch-depth: 0`
  (necessário para o sonar resolver new-code e SCM blame).
- Baixa o artefato `coverage` (o `coverage.xml` do job `tests`).
- Passo `SonarSource/sonarqube-scan-action` **fixado por SHA**, com
  `SONAR_TOKEN` do segredo. Sem token, passo pulado com aviso explícito.
- **Não** entra em `deploy.needs` enquanto for advisory.
- Timeout curto; sem Docker.

## Contrato de cobertura

- `[tool.coverage.run]`: `branch = false` (linha primeiro — branch dobra o
  ruído sem mudar a decisão de review); `parallel = true`;
  `source = ["kairos_cli", "kairos_skills", "kairos_state", "kairos_filesystem", ...]`;
  `relative_files = true`.
- `[tool.coverage.paths]` normaliza o venv do CI e o local para o mesmo path
  de fonte (senão o Sonar não casa o `coverage.xml` com os arquivos).
- `omit`: `tests/*`, `skills/*`, frontends e gerados.
- Fluxo no job `tests` (sem alterar a seleção atual de testes): exportar
  `COVERAGE_PROCESS_START=<repo>/pyproject.toml` e `COVERAGE_FILE=<repo>/.coverage`
  (absolutos) → `coverage run -m pytest -q -m "not runtime_live" --deselect
  tests/test_container.py::RealImageTests` → `coverage combine` → `coverage xml`
  → `actions/upload-artifact` (pinado por SHA) com `coverage.xml`. O
  `COVERAGE_PROCESS_START` é o que ativa o `.pth` permanente em cada
  subprocesso. O `PYTHON_VERSION` e o setup de Node permanecem.
- O código de saída do pytest é preservado (`coverage run` propaga).

## `sonar-project.properties` (calibragem)

- `sonar.projectKey` / `sonar.organization` do projeto no SonarCloud.
- `sonar.sources = .`; `sonar.python.coverage.reportPaths = coverage.xml`.
- `sonar.exclusions = skills/**, ui-tui/**, web/**, apps/desktop/**, ...`
  (alinhado ao `extend-exclude` do Ruff — **fonte única de exclusão**).
- `sonar.cpd.exclusions = tests/**, ...` (duplicação em teste é ruído).
- `sonar.issue.ignore.multicriteria` para as decisões deliberadas já
  registradas (ex.: `PLC0415` import tardio, `E501`, `RUF001-003` em
  português) — cada ignore com comentário citando o motivo no `pyproject.toml`.
- Modelo de código novo + `sonar.qualitygate.wait=false`.

## Testes obrigatórios (RED→GREEN)

1. **Cobertura de subprocesso é capturada** (`tests/test_coverage_harness.py`,
   marcador `integration` / opt-in via env, pula se `coverage` ausente):
   rodar o harness, executar a CLI em subprocesso, `coverage combine` e
   afirmar que os módulos executados **no filho** aparecem no relatório
   combinado. No arranjo sem o `.pth`, o teste falha (RED) — é a prova de que
   o número não é artificialmente baixo.
2. **Sem token não falha nem mente.** O passo do job é pulado com mensagem
   explícita; nenhuma saída de "análise concluída" é produzida.
3. **Paridade local.** `scripts/ci.sh --fast` gera `coverage.xml` e reporta
   `skip` do sonar quando não há `sonar-scanner`/`SONAR_TOKEN`.
4. **Não duplica a suíte.** Verificar que o job `sonar` não contém passo
   `pytest` (asserção de configuração no teste, não leitura de fonte em
   runtime de teste — validar o `ci.yml` como dado de configuração é aceito,
   não confundir com ler `.py`).

## Fora de escopo

- Servidor self-hosted (Community Build + plugin comunitário de branch).
- Transformar a análise em gate bloqueante de merge (fica documentado, não
  ativado).
- Teste de mutação (`mutmut`) — é o instrumento certo para avaliar *qualidade*
  de teste, mas inviável no tamanho desta suíte com testes que spawnam
  processos; decisão registrada, não executada.
- CodeQL / `pip-audit` / Dependabot (podem entrar depois, jobs próprios).

## Riscos e cuidados (viram requisitos)

1. **Ruído de lint concorrente** — mitigado por `multicriteria` + código novo;
   se o ruído persistir, a saída é excluir a regra com motivo, nunca "corrigir"
   um padrão deliberado.
2. **Agentes de IA otimizando cobertura/gate** — o Sonar é sinal para revisão
   humana; não é alvo de loop autônomo. Registrar em `docs/decisoes.md`.
3. **Cobertura enganosa** — o item 1 dos testes existe para impedir isso.
4. **Custo de CI** — job `sonar` curto e sem Docker, alimentado por artefato.
5. **Divergência de exclusões** — uma fonte única (`pyproject.toml` → espelhar
   no `properties`), com comentário cruzado.
6. **Degradação silenciosa** — sem token, dizer que pulou; nunca fingir
   análise.

## Entrega e gates

- Commits em português, conventional commits (`ci(sonar):`, `test(coverage):`,
  `docs(ci):`).
- `docs/decisoes.md`: `D-CI.SONAR` com racional (free/público), escopo
  advisory, hierarquia Ruff > Sonar e o cuidado com cobertura de subprocesso.
- Gates: testes novos RED→GREEN; suíte atual verde; `uv run ruff check .` e
  `--check`; `scripts/ci.sh --fast` verde (com `coverage.xml` gerado); job
  `sonar` verde num PR de teste (com token) e pulado com segurança (sem token);
  jobs remotos verdes. Não incluir `sonar` em `deploy.needs`.
- Aprovação explícita antes de criar o projeto no SonarCloud e cadastrar o
  segredo `SONAR_TOKEN`.
