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

## Pendências dependentes da chave (aprovação explícita antes)

- Criação do projeto no SonarCloud e configuração de `SONAR_TOKEN` como segredo
  (`projectKey`/`organization` estão provisórios como `kairos`).
- Primeiro scan real, e a partir dele a decoração de PR.
- Calibragem fina dos `multicriteria` (confirmado só `python:S103` para linhas
  longas; import tardio `PLC0415` e unicode ambíguo `RUF001-003` aguardam
  contraparte Sonar confirmada).
