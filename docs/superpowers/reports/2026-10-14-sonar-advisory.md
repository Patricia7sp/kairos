# Relatório de aceite — SonarQube Cloud (advisory)

Análise SonarQube Cloud **advisory** (cobertura de código novo, duplicidade,
complexidade) integrada ao CI sem virar segundo lint nem gate bloqueante.
Projeto chaveado em `sonar-project.properties`; o job `sonar` consome o
`coverage.xml` do job `tests` e **não** entra em `deploy.needs`.

- Base: `origin/main` @ `d730822242f287a2a0268e9c5d66d4779c7477e0`
- Branch: `feat/sonar-advisory` (worktree `.worktrees/sonar`)
- Plano: `docs/superpowers/plans/2026-10-14-sonar-cobertura-advisory.md`
- Spec: `docs/superpowers/specs/2026-10-14-sonar-cobertura-advisory-design.md`

## Commits

| Hash | Assunto |
|---|---|
| `a7cc1c6` | docs(ci): especifica e planeja análise SonarQube Cloud advisory |
| `312765a` | test(coverage): prova captura de subprocessos pelo .pth nativo |
| `dfb06e0` | docs(ci): configura análise SonarQube Cloud advisory |
| `9416bea` | ci(sonar): adiciona job advisory consumindo cobertura |
| `a893519` | chore(deps): alinha uv.lock ao pin coverage>=7.13 |

## Divergência deliberada em relação ao plano

O plano previa um habilitador manual
(`scripts/coverage-enable-subprocesses.py`). Ele foi **removido**: o wheel do
`coverage>=7.13` já instala o `.pth` nativo (`a1_coverage.pth` +
`coverage/pth_file.py`), que instrumenta subprocessos quando
`COVERAGE_PROCESS_START` está no ambiente. Manter os dois causava
instrumentação dupla. Registrado em `docs/decisoes.md` (`### D-CI.SONAR`) e na
spec/plano.

## O que foi validado

- **Captura de subprocesso pelo `.pth` nativo** (`tests/test_coverage_harness.py`,
  opt-in `KAIROS_COVERAGE_HARNESS=1`): caso positivo mede linhas do filho; caso
  negativo (sem `COVERAGE_PROCESS_START`) não mede. RED provado movendo o
  `a1_coverage.pth` para fora.
- **Configuração Sonar** (`tests/test_ci_sonar_config.py`, 13 casos → depois 15):
  `reportPaths=coverage.xml`, `qualitygate.wait=false`, paridade de exclusões com
  o `extend-exclude` do ruff, `cpd.exclusions` cobre `tests`, sem `SONAR_TOKEN`
  no arquivo, recursos de `multicriteria` em `.py`/`python:S<nnn>`.
- **Invariantes do workflow** (lidos como dado): `sonar.needs` contém `tests`;
  `sonar` não reexecuta a suíte; `sonar` não está em `deploy.needs`; artefato
  `coverage` liga `tests`→`sonar`; toda `uses:` fixada por SHA de 40 hex.
- **Paridade local** (`scripts/ci.sh`): suíte sob `coverage run` + `combine` +
  `xml`; scan condicional a `sonar-scanner` **e** `SONAR_TOKEN`, senão `skip`
  explícito.
- **`scripts/ci.sh --fast` integral:** `coverage.xml` gerado com **275 classes
  de produção** e `line-rate` global `0.8907` (cita módulos de produção, não só
  o harness); Sonar **pulado explicitamente** ("sonar-scanner ou SONAR_TOKEN
  ausente"); `ruff check`/`format --check` ok; shellcheck ok; gate de recall
  (G-19) ok; `uv lock --check` ok.

### Comandos

```bash
uv run pytest -q tests/test_ci_sonar_config.py tests/test_coverage_harness.py
KAIROS_COVERAGE_HARNESS=1 uv run pytest -q tests/test_coverage_harness.py
scripts/ci.sh --fast
uv run ruff check . && uv run ruff format --check .
uv lock --check
```

## Actions fixadas por SHA

| Action | SHA | Versão |
|---|---|---|
| `actions/upload-artifact` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` | v7.0.2 |
| `actions/download-artifact` | `9000827ccba6bdab643e8b6fd33ac0654aef8333` | v8.0.2 |
| `SonarSource/sonarqube-scan-action` | `d209202bc7d53ff1cc128f7f907dac145c9d6ae9` | v8.3.0 |

## Falhas ambientais locais (não relacionadas à mudança)

Durante o `ci.sh --fast` integral, 9 testes falharam **sem** relação com a
mudança; reproduzidas sem cobertura e explicadas:

- 7 por `umask 0077` desta máquina (asserções de modo como `448 != 456`, i.e.
  `0o700 != 0o710`); com `umask 0022` (padrão do GitHub Actions) passam.
- 2 por versão do binário Codex local divergente da pinada
  (`test_codex_supervisor`, `test_runtime_host`).

Os testes que dão SIGKILL em subprocessos instrumentados podem deixar **um
data file de cobertura corrompido** ("1 file errored" no `combine`); o `combine`
e o `xml` continuam em exit 0 e o relatório é gerado a partir dos data files
íntegros.

## Pendências dependentes da chave (aprovação explícita antes) — atualização pós-PR 106

- [x] Criação do projeto no SonarCloud (`Patricia7sp_kairos`, org `patricia7sp`)
  e configuração de `SONAR_TOKEN` como segredo — feito pelo usuário; chaves
  reais em `sonar-project.properties` (`f6f5f72`).
- [x] Primeiro scan real e decoração de PR — PR 106 (`feat/sonar-advisory`):
  três rodadas até o verde, documentadas abaixo.
- Calibragem fina dos `multicriteria` (confirmado só `python:S103` para linhas
  longas; import tardio `PLC0415` e unicode ambíguo `RUF001-003` aguardam
  contraparte Sonar confirmada).

## Pós-PR 106 — três fixes até o Quality Gate verde

O check `SonarCloud Code Analysis` reprovou em três rodadas, cada uma com
causa distinta e fix próprio (todos com teste de invariante RED→GREEN em
`tests/test_ci_sonar_config.py` + ruff limpo):

1. **`daba6f9` — basenames ambíguos no `coverage.xml`.** O log do scanner:
   `ERROR Cannot resolve the file path '__init__.py' ..., ambiguity, the file
   exists in several 'source'` + `Cannot resolve 19 file paths, ignoring
   coverage measures`. Causa: `[tool.coverage.run] source` com N pacotes faz
   o `coverage xml` relativizar cada filename à sua raiz, entregando basename
   puro ao Sonar. Fix: `source = ["."]` (paths qualificados; provado local:
   316/316 únicos, `auth.py` desambiguado em 3 pacotes, 0 arquivos de
   `.venv/docker/scripts/tests/skills`, line-rate `0.8911`) + `omit` ampliado
   (`docker/*`, `scripts/*`, `.venv/*`) espelhado em `sonar.exclusões` e
   `.gitignore` (`.scannerwork/`).
2. **`70f7cc3` — testes novos contados como fonte a cobrir.** Com o XML
   legível, o gate seguia `0.0% Coverage on New Code`: a API do SonarCloud
   mostrava 174 `new_lines_to_cover`, todos em `tests/test_ci_sonar_config.py`
   (105) + `tests/test_coverage_harness.py` (69) — a heurística de
   `sonar.tests` não os classificou (o próprio scanner emite WARN pedindo a
   declaração). Fix: `sonar.tests=tests` explícito.
3. **`6165921` — dupla indexação.** Com `sources=.` + `sonar.tests=tests`, o
   scanner abortou (exit 3): `File tests/... can't be indexed twice ... disjoint
   sets for main and test files`. Fix: `tests/**` em `sonar.exclusions` (a
   exclusão resolve a disjunção; issues em teste já eram ruído, ver
   `sonar.cpd.exclusions`).

Estado final no PR 106: `Quality Gate passed` — 0 new issues, 0 security
hotspots, 0.0% duplicação em código novo; os 11 jobs do CI em SUCCESS
(`deploy` pulado, como esperado fora da `main`).
