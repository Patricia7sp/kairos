# SonarQube Cloud advisory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para execução nativa. Steps usam checkbox (`- [ ]`) para rastreio.

**Goal:** Adicionar análise SonarQube Cloud (cobertura em código novo, duplicação, complexidade) como job advisório de CI, com cobertura que inclui código executado em subprocessos — sem virar segundo lint nem gate bloqueante.

**Architecture:** Um harness de cobertura (`coverage.py` em modo paralelo + `.pth` de `process_startup`) gera `coverage.xml` no job `tests`; um job `sonar` curto consome o artefato e chama a action oficial fixada por SHA. A calibragem fica no `sonar-project.properties` (modelo "código novo", exclusões alinhadas ao Ruff). Sem token, o passo é pulado com aviso explícito.

**Tech Stack:** Python 3.11, `coverage` (dev), GitHub Actions, SonarQube Cloud; sem dependência de runtime nova.

**Spec:** [SonarQube Cloud advisory](../specs/2026-10-14-sonar-cobertura-advisory-design.md). Base inspecionada: `d730822`. Branch própria a partir da `main`; independente de B1 e da correção de backup.

## Global Constraints

- “O Ruff continua sendo a autoridade de lint.” Achado do Sonar é consultivo; não “corrigir” padrão deliberado do `pyproject.toml` para agradar regra do Sonar.
- Análise **advisory**: `sonar.qualitygate.wait=false`. O job reporta; não bloqueia merge. Não entra em `deploy.needs`.
- “Não rodar a suíte duas vezes”: o job `sonar` **não** executa `pytest`; consome `coverage.xml` por artefato.
- Cobertura **inclui subprocessos**: 44 dos 210 arquivos de teste spawnam processos; sem instrumentar o filho o número mente.
- Cobertura coleta com **`COVERAGE_PROCESS_START` e `COVERAGE_FILE` absolutos**: o primeiro habilita o `process_startup()` no filho, o segundo impede que filhos com `cwd` em tempdir escrevam `.coverage.*` fora do repositório e sumam do `combine`.
- Sem `SONAR_TOKEN` (PR de fork): pular **explicitamente**, nunca reportar análise que não ocorreu.
- Actions fixadas por **SHA**, nunca por tag.
- Exclusões com **fonte única**: `pyproject.toml` (`extend-exclude`) → espelhado no `sonar-project.properties`, com comentário cruzado.
- `skills/` fica fora da análise (conteúdo de terceiros), como já fica do Ruff.
- Testes comportamentais; validar `ci.yml`/`properties` como **dado de configuração** é aceito, ler `.py` de produção em teste não é.
- Nenhuma criação de projeto no SonarCloud nem cadastro de `SONAR_TOKEN` sem aprovação explícita do usuário.
- Commits em português, conventional commits.

## Review Focus

1. Cobertura de subprocesso é de fato capturada, e não artificialmente baixa — tarefa 1.
2. Sem token, o job pula com mensagem explícita e não produz saída falsa de análise — tarefas 3 e 4.
3. O job `sonar` não reexecuta a suíte e não entra em `deploy.needs` — tarefa 3.
4. Exclusões têm fonte única e não divergem do Ruff — tarefa 2.
5. Gate permanece advisory (`wait=false`) e o Ruff permanece autoridade — tarefas 2 e 4.

---

## Estrutura e decisões de integração

| Unidade | Responsabilidade |
|---|---|
| `pyproject.toml` | dependência `coverage>=7.13` em `dev`; blocos `[tool.coverage.run/paths/report]` |
| `.pth` permanente do coverage (>=7.13) | instala `process_startup` no site-packages; ativado por `COVERAGE_PROCESS_START` — dispensa enabler manual (divergência 2026-10-15) |
| `.github/workflows/ci.yml` | job `tests` gera/sobe `coverage.xml`; novo job `sonar` |
| `sonar-project.properties` (novo) | chaves, fontes, exclusões, `multicriteria`, código novo, gate advisory |
| `scripts/ci.sh` | cobertura local sempre; sonar só com scanner+token, senão `skip` |
| `docs/decisoes.md` | decisão `D-CI.SONAR` |
| `tests/test_coverage_harness.py` (novo) | prova de captura de subprocesso |
| `tests/test_ci_sonar_config.py` (novo) | invariantes de configuração do job e das exclusões |

Rodar a execução no worktree atual ou criar branch limpa a partir da `main`
(`d730822`); não reutilizar branch de B1. Revalidar `git status` antes.

## Tarefa 1: harness de cobertura com subprocessos

**Files:** modificar `pyproject.toml`; criar `tests/test_coverage_harness.py`; atualizar `uv.lock`.

**Interfaces produzidas:**

```toml
# pyproject.toml
[project.optional-dependencies]
dev = [..., "coverage>=7.13"]

[tool.coverage.run]
source = ["kairos_cli", "kairos_skills", "kairos_state", "kairos_filesystem", ...]
parallel = true
branch = false
relative_files = true
omit = ["tests/*", "skills/*", "ui-tui/*", "web/*", "apps/*"]
```

**Divergência de execução (2026-10-15).** O plano previa um script
`scripts/coverage-enable-subprocesses.py` que escrevia o `.pth` de
`process_startup` no site-packages. A partir do coverage **7.12.1b1/7.13.0** esse
`.pth` (`a1_coverage.pth`, gerado de `coverage/pth_file.py`) **vem no wheel e é
instalado junto** — confirmado no changelog oficial e no `RECORD` do pacote.
O enabler manual era, portanto, redundante e inseria instrumentação dupla no
filho; foi **removido**. A dependência é pinada em `coverage>=7.13` para
garantir o `.pth` nativo.

Observações de ambiente já verificadas: o projeto está instalado em modo
**editable** (os módulos resolvem na raiz do repo), então `relative_files=true`
já entrega caminhos relativos à raiz — `[tool.coverage.paths]` só entra se
surgir divergência. `multiprocessing` aparece majoritariamente como
`get_context("spawn")` (novo interpretador → o `.pth` dispara); dois casos usam
`fork` em `tests/test_skill_mutation_io.py`, aceitos como cobertura parcial e
documentados. O `source` enumera os pacotes de produção (em vez de `.`) porque o
`.venv` fica dentro do repositório e a raiz mediria site-packages de terceiros.

- [ ] **1. Escrever o teste de captura (RED).** `tests/test_coverage_harness.py`, `unittest.TestCase`, **opt-in** por env (`KAIROS_COVERAGE_HARNESS=1`) e `skip` se `coverage` ausente — padrão dos testes de integração do repo. Contrato: com um `.coveragerc` temporário apontando `source` para um módulo real, rodar `coverage run --parallel-mode` sobre um driver que spawna um **subprocesso** Python executando uma função do módulo sob teste; depois `coverage combine` + `coverage json` e **afirmar que as linhas do filho aparecem executadas** no relatório combinado (contagem de linhas cobertas, não mera presença do arquivo). Exportar `COVERAGE_PROCESS_START` (caminho do covrc) e `COVERAGE_FILE` (absoluto) no ambiente do driver e do filho. Um caso negativo (sem `COVERAGE_PROCESS_START`) prova que o filho não é medido — o contraste é o contrato. Sem o `.pth` instalado, o caso positivo falha — é o RED.
- [ ] **2. Rodar RED:** `KAIROS_COVERAGE_HARNESS=1 .venv/bin/python -m pytest -q tests/test_coverage_harness.py`. Falha esperada: `coverage` ausente / `.pth` fora do lugar / filho fora do relatório. Não aceitar falha de infraestrutura como RED.
- [ ] **3. Implementar.** Adicionar `coverage>=7.13` a `dev`; `[tool.coverage.run]`. `uv lock` atualizado junto da dependência (job `lockfile` do CI exige). Confirmar que `coverage run -m pytest` propaga o exit code do pytest e que `parallel=true` + `combine` não perde dados de processos concorrentes.
- [ ] **4. Rodar GREEN e Ruff:** `KAIROS_COVERAGE_HARNESS=1 .venv/bin/python -m pytest -q tests/test_coverage_harness.py`; `.venv/bin/ruff check tests/test_coverage_harness.py` e `.venv/bin/ruff format --check` (Ruff não lê TOML).
- [ ] **5. Commit:** `test(coverage): prova captura de subprocessos pelo .pth nativo`.

## Tarefa 2: propriedades do Sonar, exclusões e decisão

**Files:** criar `sonar-project.properties`; modificar `docs/decisoes.md`; criar `tests/test_ci_sonar_config.py` (parte de configuração).

**Interfaces consumidas:** `coverage.xml` (tarefa 1).

**Interfaces produzidas:**

```properties
# sonar-project.properties
sonar.projectKey=...
sonar.organization=...
sonar.sources=.
sonar.python.coverage.reportPaths=coverage.xml
sonar.exclusions=skills/**,ui-tui/**,web/**,apps/desktop/**,**/__pycache__/**
sonar.cpd.exclusions=tests/**,...
sonar.qualitygate.wait=false
# sonar.issue.ignore.multicriteria.* — uma por decisão deliberada do pyproject.toml
```

- [ ] **1. Escrever testes de configuração.** `tests/test_ci_sonar_config.py` lê `pyproject.toml` e `sonar-project.properties` **como dado** e afirma invariantes: (a) todo caminho em `sonar.exclusions` cobre `skills` (paridade com `extend-exclude`); (b) `sonar.python.coverage.reportPaths` aponta para `coverage.xml`; (c) `sonar.qualitygate.wait` é `false`; (d) `sonar.cpd.exclusions` inclui `tests`. Nada de congelar número de regras nem listar todas as chaves.
- [ ] **2. Rodar RED:** `.venv/bin/python -m pytest -q tests/test_ci_sonar_config.py` — `properties` ausente.
- [ ] **3. Implementar propriedades e decisão.** Criar o `properties` com as exclusões derivadas do `pyproject.toml` (fonte única, comentário cruzado) e os `multicriteria` mapeando cada família deliberada (import tardio `PLC0415`, `E501`, `RUF001-003`) à regra Sonar equivalente, com motivo. Em `docs/decisoes.md`, nova subseção `D-CI.SONAR` sob a área de CI: racional (free/público, sem self-hosted), escopo advisory, hierarquia **Ruff > Sonar**, e o cuidado com cobertura de subprocesso.
- [ ] **4. Rodar GREEN e Ruff:** teste acima; `.venv/bin/ruff check tests/test_ci_sonar_config.py` e format.
- [ ] **5. Commit:** `docs(ci): configura análise SonarQube Cloud advisory`.

## Tarefa 3: job `sonar` no CI e artefato de cobertura

**Files:** modificar `.github/workflows/ci.yml`; ampliar `tests/test_ci_sonar_config.py`.

**Interfaces produzidas:**

```yaml
# job tests (trecho alterado)
- name: Habilitar cobertura de subprocessos
  run: uv run python scripts/coverage-enable-subprocesses.py
- name: pytest (com cobertura)
  env:
    COVERAGE_PROCESS_START: ${{ github.workspace }}/pyproject.toml
    COVERAGE_FILE: ${{ github.workspace }}/.coverage
  run: |
    coverage run -m pytest -q -m "not runtime_live" \
      --deselect tests/test_container.py::RealImageTests
    coverage combine
    coverage xml
- uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
  with: { name: coverage, path: coverage.xml }

# job novo
sonar:
  needs: [tests]
  runs-on: ubuntu-latest
  env:
    SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

SHAs fixados (resolvidos em 2026-10-14): `upload-artifact`
`cf430e030ddbb5b0abf93d22962f4752f3646cd9` (v7.0.2), `download-artifact`
`9000827ccba6bdab643e8b6fd33ac0654aef8333` (v8.0.2), `sonarqube-scan-action`
`d209202bc7d53ff1cc128f7f907dac145c9d6ae9` (v8.3.0).

- [ ] **1. Escrever/estender testes de configuração.** Afirmar, como dado: (a) o job `sonar` depende de `tests` e **não** contém passo `pytest`; (b) `sonar` não aparece em `deploy.needs`; (c) o job `tests` publica artefato `coverage` e o `sonar` o baixa; (d) todo `uses:` do arquivo é `@<sha40>` (regex), cumprindo a regra "fixadas por SHA". Não congelar nomes de action além do necessário.
- [ ] **2. Rodar RED:** `.venv/bin/python -m pytest -q tests/test_ci_sonar_config.py` — job/artefato ausentes.
- [ ] **3. Implementar o CI.** No job `tests`: habilitar `.pth` (`uv run python scripts/coverage-enable-subprocesses.py`), trocar `uv run pytest` por `coverage run` (preservando `-m`, `--deselect` e o setup de Node já existente), exportar `COVERAGE_PROCESS_START`/`COVERAGE_FILE` absolutos, `combine`/`xml`, upload de artefato. No job `sonar`: `checkout` com `fetch-depth: 0`, baixar artefato, definir `SONAR_TOKEN` em `env` de job e usar passo condicional `if: env.SONAR_TOKEN != ''` (o contexto `secrets` não vale em `if`), com a action oficial **fixada por SHA**; **sem token, pular com `echo` explícito** ("análise Sonar pulada: SONAR_TOKEN ausente"), nunca falhar nem reportar análise falsa. `timeout-minutes` curto; sem Docker. Atualizar o comentário-cabeçalho do job explicando que é advisory e por quê.
- [ ] **4. Rodar GREEN:** `.venv/bin/python -m pytest -q tests/test_ci_sonar_config.py tests/test_coverage_harness.py`.
- [ ] **5. Validar o YAML.** Se houver `actionlint` disponível, rodar; senão, o teste de configuração é a barreira. Confirmar que o `tests` job continua passando localmente (tarefa 4).
- [ ] **6. Commit:** `ci(sonar): adiciona job advisory consumindo cobertura`.

## Tarefa 4: paridade local e aceite

**Files:** modificar `scripts/ci.sh`; criar `docs/superpowers/reports/2026-10-14-sonar-advisory.md`.

**Interfaces consumidas:** script de harness (tarefa 1), `properties` (tarefa 2), job (tarefa 3).

- [ ] **1. Escrever teste de paridade.** Estender `tests/test_ci_sonar_config.py` para afirmar que `scripts/ci.sh` gera `coverage.xml` (procura o comando `coverage` no script, como dado) e que o scan do Sonar é condicional a `sonar-scanner`/`SONAR_TOKEN` — mesma forma dos `skip` de shellcheck/npm já existentes.
- [ ] **2. Rodar RED:** teste falha — `ci.sh` ainda sem cobertura/sonar.
- [ ] **3. Implementar a paridade.** Em `scripts/ci.sh`: rodar a suíte sob `coverage run`, `combine`, `xml`; depois, `if command -v sonar-scanner && [ -n "${SONAR_TOKEN:-}" ]` → `run "Sonar (análise)" sonar-scanner`; senão `skip "Sonar" "sonar-scanner/SONAR_TOKEN ausente"`. Manter `--fast` coerente (cobertura roda no `--fast`; o scan é pulado sem token).
- [ ] **4. Rodar GREEN e aceite local.** `.venv/bin/python -m pytest -q tests/test_ci_sonar_config.py tests/test_coverage_harness.py`; depois `scripts/ci.sh --fast` integral com `coverage.xml` gerado e `skip` explícito do Sonar; `uv run ruff check .` e `--check`. Conferir que o `coverage.xml` cita módulos de produção e **não** só o harness.
- [ ] **5. Commit e PR em rascunho:** `ci(sonar): espelha cobertura e análise no CI local`. Descrição em português com limites, evidências e o item de aprovação pendente (criação do projeto no SonarCloud + `SONAR_TOKEN`). Executar CI remoto; conferir jobs verdes. Não fazer merge antes da aprovação explícita para criar o projeto/segredo.
- [ ] **6. Relatório de aceite.** Registrar em `docs/superpowers/reports/2026-10-14-sonar-advisory.md`: comandos, hashes, o que foi validado, e o que **permanece pendente por depender da chave** (primeiro scan real, decoração de PR, calibragem fina dos `multicriteria`).

## Autorrevisão e handoff

Antes de cada commit, revisar o diff contra a spec e conferir os logs RED/GREEN.
Cobertura conferida: harness de cobertura e subprocesso (1); propriedades,
exclusões e decisão (2); job advisory, artefato e segurança sem token (3);
paridade local, aceite e relatório (4). Os cinco itens de Review Focus têm
casos nas tarefas indicadas. O plano não cria projeto no SonarCloud, não ativa
gate bloqueante, não adiciona self-hosted, CodeQL ou mutação.

Execução recomendada: nativa, quatro tarefas sequenciais, com aprovação
explícita do usuário antes do primeiro scan real com token. O usuário aprovou
a spec; este plano aguarda revisão antes da implementação.
