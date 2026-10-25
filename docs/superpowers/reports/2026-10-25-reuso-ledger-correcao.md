# Correção e nova avaliação do reuso do ledger

Continuidade solicitada pela usuária em 25/10/2026 para resolver o bloqueio
do [PR #117](https://github.com/Patricia7sp/kairos/pull/117).
O [resultado anterior](2026-10-24-reuso-ledger.md) permanece reprovado e seus
dados não foram descartados ou reclassificados. **A nova candidata ainda
precisa ser medida; não há aceite de desempenho presumido.**

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
conferência independente. A suíte integrada e o Sonar da nova revisão ainda
precisam concluir; aprovações de `2843475` não são transferidas automaticamente.

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

Será executada uma rodada completa desse candidato fixado. O opt-in será
removido depois dela, antes de commits somente documentais, para não repetir
medições do mesmo código. Todos os resultados serão publicados, inclusive
eventual reprovação. Não houve merge ou deploy.
