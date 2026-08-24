# Kairos

Reimplementação do Hermes a partir das especificações geradas pelo Reversa.

**Specs (fonte da verdade):** `../hermes-agent/_reversa_sdd/`
**Plano de reconstrução:** `../hermes-agent/_reversa_sdd/reconstruction-plan.md`

O Kairos é **reimplementação, não fork**: livre para corrigir os 5
anti-padrões catalogados em `architecture.md#8`. As divergências deliberadas
em relação ao legado estão em [`docs/decisoes.md`](docs/decisoes.md).

## Estado

| Tarefa | Status |
|---|---|
| 01 — Schema do banco de dados | ✅ concluída |
| 02 — Entidades de domínio | ✅ concluída |
| 03 — Máquinas de estado | ✅ concluída |
| 04 — native (`fts5_cjk`) | ✅ concluída |
| 05 — hermes-state | ✅ concluída |
| 06 — i18n | ✅ concluída |
| 07 — container | ✅ concluída |
| 08 — tools | ✅ concluída |
| 09 — skills | ✅ concluída |
| 10 — plugins | ✅ concluída |
| 11 — providers-gateway | ✅ concluída |
| 12 — mcp | ✅ concluída |
| 13 — agent | ✅ concluída |
| 14 — cron | ✅ concluída |
| 15–21 | pendentes |

## Ambiente

O projeto usa [uv](https://docs.astral.sh/uv/) e fixa **Python 3.11**, a mesma
versão do legado (`.python-version`). O Python do sistema é 3.14, então o
`uv` provisiona o 3.11 próprio — não dependa do interpretador global.

```bash
uv venv --python 3.11     # cria .venv com o 3.11 gerenciado pelo uv
uv pip install -e ".[dev]"
```

## Imagem de container

```bash
docker build -t kairos:test .
```

Os testes de integração (`RealImageTests`) verificam contra a imagem
construída e são **pulados** se ela não existir. Eles cobrem o que o
`docker build --check` não alcança: deriva entre `pyproject.toml` e o
Dockerfile, diretórios de destino e o `PATH` de runtime.

## CI

`.github/workflows/ci.yml` roda seis jobs em paralelo: testes, ruff, shellcheck,
hadolint, imagem+integração e `uv lock --check`. Actions fixadas por **SHA**,
não por tag — tag é mutável.

O repositório ainda não tem remoto, então o workflow não executa em lugar
nenhum. Para que o CI não seja ficção, `scripts/ci.sh` roda **exatamente os
mesmos passos** localmente:

```bash
scripts/ci.sh          # tudo
scripts/ci.sh --fast   # pula o que exige Docker
```

Cada passo é pulado com aviso, não silenciosamente, quando a ferramenta falta.

## Rodar os testes

A extensão CJK opcional exige `gcc`:

```bash
./native/fts5_cjk/build.sh    # instala em $KAIROS_HOME/lib
```

Sem ela a suíte passa igual — os testes que a exigem são pulados, que é o
comportamento correto: a extensão é opcional por desenho.

```bash
uv run pytest -q                          # caminho normal
.venv/bin/python -m unittest discover -s tests   # sem dependência nenhuma
```

A suíte é escrita em `unittest` da biblioteca padrão, então roda dos dois
jeitos. `pytest` só é necessário para o relatório mais legível — nunca para
que os testes existam.
