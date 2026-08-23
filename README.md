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
| 02–21 | pendentes |

## Rodar os testes

```bash
python3 -m unittest discover -s tests -v
```

A suíte usa apenas a biblioteca padrão. `pytest` também a executa sem
alteração, se estiver disponível.
