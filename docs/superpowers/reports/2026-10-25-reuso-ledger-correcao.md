# Correção e nova avaliação do reuso do ledger

Atualização posterior: o PR #117 foi integrado e implantado em `8902010`.
O [fechamento operacional](2026-10-25-fechamento-ledger.md) registra CI,
Sonar da main, checkout implantado, fontes e smoke. Os resultados abaixo
preservam o estado da avaliação pré-merge.

Continuidade solicitada pela usuária em 25/10/2026 para resolver o bloqueio
do [PR #117](https://github.com/Patricia7sp/kairos/pull/117).
O [resultado anterior](2026-10-24-reuso-ledger.md) permanece reprovado e seus
dados não foram descartados ou reclassificados. **A nova candidata passou
no gate integral em uma única rodada pré-definida:** os três pares de 120
melhoraram, com ganho mediano de 11,507711479836553%. Naquele checkpoint
ainda não havia merge ou deploy.

## Investigação

A reconstrução efetiva já caía de 903 para 775 nos três pares anteriores.
No par que regrediu, funções com corpo e número de chamadas inalterados
também ficaram mais lentas. Isso não demonstra uma regressão intrínseca do
memo nem identifica a causa histórica exata; faltava telemetria de CPU.

Um diagnóstico separado do gate executou as revisões B2 e candidata antigas,
extraídas por SHA, com API/SQLite/arquivos reais e o harness corrigido.
Mediu wall-time, tempo de CPU do processo, espera de scheduler e throttling.
Em 120, a baseline variou de 1.235 a 2.223 ms de CPU entre sete operações;
o tempo de CPU ficou próximo do tempo decorrido, sem throttling registrado
e com espera de scheduler inferior a 2,3 ms. Isso enfraquece a hipótese de
I/O ou fila de CPU como explicação da dispersão dessa reprodução.

Uma segunda reprodução executou exatamente a mesma validação YAML 903
vezes por lote, sem SQLite: os 12 lotes variaram de 646 a 831 ms de CPU,
com 1.806 definições de classe em cada lote e 3,4–16,8 ms de coleta de lixo.
A coleta não explica a amplitude observada. Frequências/CPUs ativas variam
no host local; não houve tentativa de alterar governor, sysctl ou GC.
Contadores de hardware estavam indisponíveis por permissão.

Esses tempos instrumentados **não são amostras de aceite**. Não há base
para invalidar retroativamente o primeiro par antigo. O parser e seus
validadores permaneceram inalterados.

## Correções de contrato e manutenção

- O memo só armazena um registro quando tipo, home e operation_id correspondem
  à chave solicitada. Um carregador incorreto não contamina a leitura seguinte.
- Erros `io`/`conflict` originados na observação recebem o subtipo privado
  `RecordObservationError`. O wrapper preserva somente esses erros marcados;
  falhas comuns dos validadores continuam sendo classificadas como corrupção.
- A observação de cada arquivo foi extraída de `_file_stamps`, mantendo a
  distinção entre sidecar ausente e objeto que não pode ser observado com
  segurança. Symlink, hardlink e diretório não se tornam ausência elegível.
- A mensagem de mudança de observação usa uma constante. Nenhuma regra do
  Sonar ou do Ruff foi silenciada.

Os três casos funcionais novos foram observados falhar antes das correções.
A bateria focada ficou verde com 41 testes e 40 subtestes, inclusive na
conferência independente. Aprovações de `2843475` não foram transferidas
automaticamente: a suíte inteira foi executada para o runtime corrigido.

O Sonar de `0e2c9f2` confirmou a remoção dos dois code smells e cobertura
nova de 96,3%, mas **reprovou** o Quality Gate por `githubactions:S8541`:
o novo `uv sync` permitia scripts de build. Sucesso do job de upload da
análise não foi tratado como aprovação do gate.

O preparo foi corrigido com `--no-build --no-install-project`; a execução
usa `.venv/bin/python` diretamente para evitar que `uv run` sincronize e
instale o projeto novamente. O harness importa os pacotes extraídos por
SHA e não depende da instalação editável. Conforme a
[documentação do uv](https://docs.astral.sh/uv/concepts/projects/sync/),
omitir a instalação do projeto conserva suas dependências.

Uma venv e um cache novos em `/tmp` instalaram as 44 dependências do lock
com esse comando; fora do checkout, o pacote Kairos não estava instalado.
Os testes reais de fixture e gate passaram nesse ambiente: nove testes e
32 subtestes. `--no-build` impede builds nesta execução; o uv também pode
reutilizar wheels previamente construídos quando há cache. Não se afirma
que todo wheel venha necessariamente do índice.

Esse endurecimento do preparo ocorreu **depois** da medição de `0e2c9f2`.
Não altera runtime, testes, dependências/lock, driver ou harness, e não
representa nova medição do workflow corrigido. O fechamento desse achado
exige CI/Sonar do novo head, registrado na descrição do
[PR e seus checks](https://github.com/Patricia7sp/kairos/pull/117/checks),
além deste resultado histórico de `0e2c9f2`.

## Ambiente pré-definido para a nova rodada

O workflow existente ganhou o job **Benchmark pareado do ledger (opt-in)**.
Ele roda em VM própria do job GitHub, fixa uma CPU lógica escolhida antes dos
tempos e executa o mesmo driver/harness sequencialmente. Isso não garante
host físico exclusivo ou frequência constante; o gate continua podendo falhar.

O protocolo permanece integral: baseline
`b7346adeee312368746fddb3ca7cfe3058c49b99`, schema 11, três pares AB/BA/AB,
sete amostras em 0/40/120, perfil separado, 20 validações, ganho mediano de
pelo menos 10% em 120 e melhora nos três pares. Os limites de regressão em
0/40 continuam sendo a conjunção de mais de 5% e mais de 5 ms. Não se troca
wall-time por CPU-time para aprovar.

O opt-in usa a label `benchmark-ledger`, aplicada **antes** do push do SHA
candidato: adicionar a label sozinha não dispara o workflow, cujos eventos
permanecem os existentes. O job não roda em `workflow_dispatch`, na main
ou em PR sem opt-in. `deploy.needs` e sua condição permanecem intactos.

O checkout usa o head SHA do PR, histórico completo, credencial não
persistida e permissões `contents: read`. Dependências usam o lock congelado;
GC continua ativo e tracing/cobertura são recusados. Cada worker herda a
afinidade do driver. Pares, logs, ambiente, revisões e hashes são preservados
mesmo quando o gate retorna erro; exit 1 continua sendo reprovação.

Foi executada uma rodada completa do candidato fixado. O opt-in foi removido
depois dela, antes de commits somente documentais, para não repetir medições
do mesmo código. Não houve segunda tentativa desse candidato/ambiente.

## Resultado da rodada no runner

| Referência | Valor |
| --- | --- |
| Candidata medida | `0e2c9f265f8c61e05896204c9cc75e82e00d1df8` |
| Baseline | `b7346adeee312368746fddb3ca7cfe3058c49b99` |
| CI / tentativa | [40148739757](https://github.com/Patricia7sp/kairos/actions/runs/40148739757), tentativa 1 |
| Job / artefato | `121035589432` / `12581401584` |
| SHA-256 do ZIP baixado, igual ao digest do GitHub | `3b80137a8ead1993f9d4cb2161e607367689e9a78c9630cd15561e8ede731bbb` |
| Harness SHA-256, inalterado | `ca919b8a0caa4e23d01cad223274177d7b7122856d6b62a1039848ff7bddeb83` |
| Driver SHA-256, inalterado | `0d1f55fa583dda210cf669e95ebaeb5b922194fab1e3ded605d22c0321f8b499` |
| Ambiente | Python 3.11.17, SQLite 3.53.1, Linux 6.17.0-1022-azure / glibc 2.39, CPU lógica 0, GC ativo |

Os [seis brutos e o resumo](2026-10-25-reuso-ledger/runner-protocol/skill-writer-pairs/summary.json),
o [ambiente observado](2026-10-25-reuso-ledger/runner-protocol/skill-writer-environment.json)
e os logs estão preservados byte a byte conforme o ZIP. O
[manifesto](2026-10-25-reuso-ledger/runner-protocol/sha256.json) cobre todos
os 15 arquivos de evidência, exceto o próprio manifesto;
[artifact.json](2026-10-25-reuso-ledger/runner-protocol/artifact.json)
registra origem e digest do download. O Root recalculou o gate a partir de
todas as amostras, conferiu o resumo exato e validou os hashes dos módulos
contra os dois SHAs Git, além dos hashes comuns de harness/driver.

| Cenário | Par | Baseline (ms) | Candidata (ms) | Ganho |
| --- | --- | ---: | ---: | ---: |
| 0 | 1 | 69,898 | 67,084 | 4,0255% |
| 0 | 2 | 69,600 | 67,730 | 2,6872% |
| 0 | 3 | 68,636 | 67,534 | 1,6049% |
| 40 | 1 | 298,957 | 265,975 | 11,0324% |
| 40 | 2 | 304,565 | 267,845 | 12,0564% |
| 40 | 3 | 295,764 | 267,525 | 9,5480% |
| 120 | 1 | 751,751 | 665,242 | 11,5077% |
| 120 | 2 | 743,852 | 663,225 | 10,8390% |
| 120 | 3 | 750,996 | 658,776 | 12,2797% |

Os valores da tabela são apresentação. A decisão usa números completos:
`accepted=true`, `failures=[]`, mediana de ganho em 120
`11.507711479836553`. Os três pares de 120 melhoram; a mediana supera 10%;
0/40 não apresentam a regressão vedada. As 20 validações da timeline
permanecem em todos os 18 perfis. Em 120, o wrapper continua sendo chamado
903 vezes e as reconstruções reais caem de 903 para 775 em cada par.
Em 0/40, caem de 63/343 para 55/295, respectivamente.

O observador fez dez observações por perfil de 120, com tempo cumulativo
de 0,572–0,600 ms sob cProfile. Esse custo não é subtraído das amostras de
latência. Os 128 registros reutilizados ficam abaixo do limite de 1.024;
não houve saturação nessa rodada e consumo em bytes não foi medido.

## Validação e revisão

| Verificação | Resultado |
| --- | --- |
| Suíte principal local com cobertura | 4.105 passed; 21 skipped; 33 deselected; 6.542 subtestes; 1.292,53 s |
| Imagem real local | 32 passed; 26 subtestes; 204,59 s |
| Sessões Docker isoladas locais | 26 passed; um skipped; 64,15 s |
| Suíte principal remota em `0e2c9f2` | 4.102 passed; 24 skipped; 33 deselected; 6.542 subtestes; 620,88 s |
| Imagem real remota em `0e2c9f2` | 32 passed; 26 subtestes |
| Sessões Docker isoladas remotas em `0e2c9f2` | 26 passed; um skipped |
| Testes focados dos contratos | 41 passed; 40 subtestes; também conferidos independentemente |
| Fixture e gate no preparo sem builds | nove passed; 32 subtestes |
| Ruff, formatação e shellcheck locais | aprovados; 720 arquivos formatados |
| Recall G-19 e lock locais | aprovados |
| Frontends locais TUI / web / desktop | 17 / 204 / 18 testes; TypeScript dos três aprovado |

`scripts/ci.sh` concluiu integralmente com exit 0, incluindo as imagens
principal, broker, stub e worker e seus checks. O Sonar local foi pulado
explicitamente pela ausência do scanner/token;
a análise remota de `0e2c9f2` ocorreu e seu Quality Gate reprovado está
descrito acima. A revisão independente conferiu o código, executou os
testes focados e recalculou os seis brutos, manifesto e hashes de módulos.
As cópias publicadas coincidem byte a byte com o artefato baixado.

`runtime_live`, providers reais e opt-ins não executados não recebem
atestado dos skips/deseleções. O memo continua restrito aos callbacks
transacionais próprios; guards de SQL/schema/timeline/política permanecem
responsáveis pela autoridade e continuidade física. Nenhuma proteção
contra o mesmo UID ou controle transacional externo foi acrescentada.

## Alcance da conclusão

O bloqueio de desempenho deste candidato fica atendido no ambiente
pré-definido do runner. A nova rodada não prova a causa histórica do par
local que regrediu: o candidato e o ambiente mudaram, e os resultados antigos
continuam reprovados. Não se infere que todo host terá o mesmo ganho, nem
SLA ou significância estatística. Não houve seleção de rodada, descarte de
par ou mudança de limiar.

A decisão de usar uma VM própria do job e afinidade reduz interferência
entre os nossos trabalhos e torna o ambiente auditável. Custo residual:
a VM pode compartilhar hardware e variar em frequência; o gate permanece
obrigatório e pode reprovar futuras alterações. A execução desta rodada
consumiu cerca de seis minutos de runner, sem alterar parser, GC, schema,
dependências ou o corpo dos validadores. Rollback continua sendo reverter
o código, sem migração ou cache persistido a restaurar.
