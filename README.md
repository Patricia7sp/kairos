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
| 04–21 | pendentes |

## Ambiente

O projeto usa [uv](https://docs.astral.sh/uv/) e fixa **Python 3.11**, a mesma
versão do legado (`.python-version`). O Python do sistema é 3.14, então o
`uv` provisiona o 3.11 próprio — não dependa do interpretador global.

```bash
uv venv --python 3.11     # cria .venv com o 3.11 gerenciado pelo uv
uv pip install -e ".[dev]"
```

## Rodar os testes

```bash
uv run pytest -q                          # caminho normal
.venv/bin/python -m unittest discover -s tests   # sem dependência nenhuma
```

A suíte é escrita em `unittest` da biblioteca padrão, então roda dos dois
jeitos. `pytest` só é necessário para o relatório mais legível — nunca para
que os testes existam.
