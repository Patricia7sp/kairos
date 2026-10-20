# A2 — medição de custo do ledger

Medição local de 2026-10-14 sobre `d730822`, com Python 3.11 e fixtures
temporárias. [Dados brutos](2026-10-14-skill-ledger-benchmark.json) incluem
amostras, plataforma, tempos de preparação e contagens por operação.

## Método

Executado no worktree `removal_design`:

```bash
.venv/bin/python scripts/benchmark-skill-ledger.py --output /tmp/kairos-ledger-benchmark-20261014.json
```

O script cria SQLite real, migra até v7, prepara/conclui registros de criação
pela API do repositório e executa as migrações v8/v9. Essas criações de prefixo
têm identidades sintéticas e não correspondem a árvores materializadas: medem
o custo de validar o histórico importado, não custo de criar skills reais.
A skill alvo é uma árvore real com `SKILL.md` legado; adoções e revogações
passam pelo serviço real. Cada ciclo contém uma adoção e uma revogação, e cada
cenário termina com uma adoção ativa adicional.

Cada operação é aquecida uma vez; sete amostras sem profiler produzem a
mediana. Outra execução, separada, usa cProfile para contar chamadas.
`history` filtra a skill alvo e pede limite 20. `identify` usa a API pública,
com suas cópias privadas do banco; as operações do repositório usam conexão
aberta. Não comparar esses caminhos como se fossem equivalentes.

O script recusa se faltar adoção ativa, se o histórico não apontar a adoção
final ou se a adoção idempotente trocar seu ID. Ao fechar cada cenário, compara
o SHA-256 do banco e device/inode da raiz da skill com os valores anteriores.
Os cinco cenários concluíram com essas verificações preservadas. Não há teste
de concorrência, adulteração ou carga de produção nesta medição.

## Resultados

Medianas em milissegundos:

| Criações anteriores | Ciclos revogados | Eventos na timeline | Evidência | History (20) | Identify | Adopt idempotente |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 2 | 0,65 | 1,30 | 31,87 | 44,38 |
| 100 | 0 | 202 | 94,07 | 205,47 | 442,40 | 907,05 |
| 1.000 | 0 | 2.002 | 1.138,46 | 2.276,87 | 4.667,67 | 9.139,28 |
| 0 | 20 | 82 | 19,07 | 42,17 | 200,38 | 207,72 |
| 0 | 100 | 402 | 135,40 | 164,04 | 393,25 | 854,29 |

Chamadas a `_get_validated` em uma execução perfilada:

| Criações anteriores | Ciclos revogados | Evidência | History (20) | Identify | Adopt idempotente |
|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 1 | 2 | 5 | 10 |
| 100 | 0 | 101 | 202 | 405 | 810 |
| 1.000 | 0 | 1.001 | 2.002 | 4.005 | 8.010 |
| 0 | 20 | 41 | 82 | 165 | 330 |
| 0 | 100 | 201 | 402 | 805 | 1.610 |

Nos cinco cenários, `validate_skill_timeline` e `validate_adoption_schema`
foram chamadas respectivamente 1/1, 2/2, 9/9 e 14/14 vezes nos quatro
caminhos. `identify` e adoção idempotente fizeram quatro cópias privadas do
banco cada. As contagens explicam trabalho repetido sobre históricos maiores;
não provam complexidade assintótica nem atribuem todo o tempo a uma função.
Há dispersão relevante nas amostras; não usar as medianas como SLA.

## Rastreabilidade e limite da revisão

O JSON registra o SHA-256 do script executado:
`1995a9269151fe4ee4e6b10ddb0a6f072fb0c6fb1cfab573685e0dad8d17cbfb`.
Durante a execução, Ruff formatou uma condição em três linhas. O script
formatado tem SHA-256
`bf460f8a01dc7d16df6e1ed24d800be846858bc4d884540471d2dd4704fbba7b`.
A reconstrução da condição original reproduziu o hash executado, e a
comparação de ASTs confirmou igualdade; não houve mudança semântica nem nova
rodada de tempos após formatação. Ruff check e format passaram.

Regressão focal executada depois da medição:

```bash
umask 0022 && .venv/bin/python -m pytest -q tests/test_skill_adoption_service.py tests/test_skill_identification.py tests/test_skill_identification_evidence.py tests/test_skill_adoption_lineage.py
```

Resultado: 69 testes e 107 subtestes passaram em 33,66 s. A primeira execução,
com a umask herdada `0077`, teve 67 testes aprovados e duas falhas: as fixtures
usam `mkdir(mode=0o710)` sem `chmod`, produzindo `0700` nesse ambiente e
divergindo da asserção `0710`. Uma reprodução mínima confirmou a causa.
O runtime preservou o modo efetivamente criado; testes e runtime não foram
alterados. A dependência dessas duas fixtures da umask permanece como limitação.
O benchmark foi executado com a umask original; não depende desses modos fixos.

## Implicação

A medição de acompanhamento de A2 está realizada. Há motivo concreto para
investigar reaproveitamento de validações dentro de uma observação estável.
Qualquer otimização precisa preservar detecção de troca do banco/WAL,
backup parcial, corrupção e mudança entre as observações de `identify`.
Não introduzir cache global nem eliminar varreduras com base só nestes tempos.
Uma alteração de desempenho exige proposta e regressões próprias.

Nenhum código de runtime foi modificado. Este relatório não substitui os
gates locais/remotos de uma futura implementação B1 ou de otimização.
