# Reuso de registros validados dentro da checagem transacional

Proposta escrita em 24/10/2026 para a [issue #109](https://github.com/Patricia7sp/kairos/issues/109).
A usuária escolheu reuso limitado por checagem ou transação, mantendo todas as
varreduras e as 20 validações da timeline do benchmark. **A escolha da abordagem
está aprovada; este desenho escrito ainda aguarda aprovação.**

O [PR #116](https://github.com/Patricia7sp/kairos/pull/116), head `6e5ce1a`,
permanece como baseline documental/diagnóstica, com seus checks concluídos.
Este desenho está em branch separada; não altera o runtime nem os dados medidos.
Sua aprovação precede o plano de implementação. Não autoriza merge ou deploy.

## Objetivo e evidência

Reduzir a recarga e reconstrução de registros já validados sem omitir qualquer
validação de timeline, schema, política, pendência ou identidade. A referência é
o [benchmark reproduzível](../reports/2026-10-24-skill-writer-benchmark.md):
no cenário B2/v11 com prefixo 120, mediana 1.369,54 ms; no add perfilado com
128 criações anteriores, 20 chamadas a `validate_skill_timeline` e 903 a
`_get_validated`. As amostras têm dispersão e não constituem SLA.

O primeiro ponto concreto é `SkillMutationRepository.prepare`: dentro do
mesmo `BEGIN IMMEDIATE`, `_assert_no_pending` e `_current_create` carregam os
registros de toda a timeline antes do primeiro INSERT. Reaproveitar esses
registros não exige retirar as duas varreduras. As checagens externas de
`SkillMutationService.add` atravessam staging, publicação e transações e
continuam independentes. Não se promete eliminar os 903 carregamentos.

## Abordagens consideradas

1. **Escolhida pela usuária:** memoizar resultados dentro de uma checagem ou
   transação delimitada. Mantém a ordem e execução dos validadores. Exige provar
   validade do resultado e descartar o memo nas mudanças de estado.
2. Otimizar consultas e carregamentos em lote mantendo todas as varreduras.
   Evita memoização, mas altera a construção/validação dos resultados e amplia
   a comparação semântica necessária. Não foi a abordagem escolhida.

Decisão técnica proposta pelo Root: começar **somente nas transações de escrita
que o próprio `SkillMutationRepository._write` abre e encerra**, com resultados
positivos do tipo imutável `SkillMutationRecord`. É um subconjunto conservador
da abordagem escolhida. Leituras avulsas, transações recebidas de terceiros e
escopos que atravessem efeitos no filesystem ficam fora do primeiro incremento.
Habilitar somente os callbacks SQL privados de `prepare` e `finish` da família
legada. Outros usos de `_write`, inclusive wrappers das famílias de remoção e
adoção ou callbacks genéricos, continuam sem memo neste incremento.

## Contratos preservados

- As chamadas e os corpos de `validate_skill_timeline`, dos validadores de
  schema, da capability de escrita e dos guards de política continuam intactos.
  O caminho comparável do benchmark deve continuar executando as 20 validações
  da timeline, na mesma ordem de efeitos; não se transforma validação em no-op.
- `_load_validated_records` continua enumerando todos os IDs e só filtra por
  nome depois. Corrupção em outro nome não passa a ser ignorada.
- Mutações, journal, transações, locks, fsync, guards, categorias de erro e
  recuperação seguem os contratos A1/A2/B1/B2. Sem schema ou dependência nova.
- Registros cacheados não são prova de autoria, permissão, recibo de política,
  estado do filesystem ou autorização de efeito. Essas decisões continuam
  sendo obtidas pelos caminhos atuais.
- Nada é compartilhado entre operações CLI, conexões, homes, processos,
  tentativas de retry ou transações. Sem cache global, TTL ou persistência.

## Limite e ciclo de vida

O repositório mantém um objeto privado de observação, criado **depois de um
BEGIN IMMEDIATE bem-sucedido**, exclusivamente para a chamada de `operation()`
daquela tentativa de `_write`. Não se cria transação nova para obter hits.

O objeto pertence à instância do repositório, à conexão e ao `home_id`; um token
novo distingue cada tentativa. Antes de `commit`, rollback ou propagação de
exceção ele é descartado incondicionalmente. O retry começa vazio. A recusa
existente de `_write` quando já há transação pendente continua igual.
O padrão do dono é memo desabilitado; a habilitação é privada e explícita nos
dois callbacks admitidos, nunca inferida apenas de uma conexão em transação.

Não se propaga o objeto por `ContextVar`, singleton, função SQL, conexão global
ou repositórios de outras famílias. Não se instala callback que troque handlers
de SQLite do chamador. Chamadas recursivas fora do dono ou escopo ambíguo usam o
caminho sem memo. O incremento não altera o gerenciador global de conexões.

Somente callbacks privados atuais, cujo BEGIN/COMMIT/ROLLBACK são controlados
por `_write`, podem receber o objeto. SQL arbitrário que termina/reabre a
transação, `executescript`, SAVEPOINT externo e chamadas que cedam controle
para código externo não são admitidos dentro desse dono. Um dono sem essa
garantia não habilita o memo; verificar apenas `in_transaction` não prova a
continuidade de uma transação encerrada e reaberta.

## Conteúdo e chave

Memoizar somente o resultado positivo e já validado cujo tipo exato seja
`SkillMutationRecord`, com draft e identidades imutáveis. CREATE e ROLLBACK
continuam sujeitos às validações originais, inclusive do pai no primeiro load.
Ausência, exceção, listas de pendências, projeções de instalação, árvores,
adoções, publicações e decisões de política não são memoizadas neste incremento.

A chave distingue token, conexão, home, `operation_id`, modo `modern` e geração
da observação. IDs continuam validados antes de usar um resultado. O caminho
de miss é o carregador atual; um hit retorna o mesmo valor imutável validado,
sem nova leitura/reconstrução daquele registro. O laço global permanece real.

O armazenamento tem limite finito. Proposta do Root: até 1.024 registros por
observação; atingido o limite, conservar entradas válidas e deixar novos IDs
como misses. Sem eviction com autoridade implícita nem erro convertido em
sucesso. O valor pode ser afinado pelo implementador com medição de memória;
os testes exercitam a propriedade do limite, sem congelar o número como regra
de negócio. Falhas inesperadas de memória/SQLite continuam propagadas.

## Estabilidade e invalidação

Um hit só é elegível enquanto conexão, dono e geração continuam válidos.
O stamp combina `total_changes` da conexão, cookies de schema relevantes,
`PRAGMA data_version` da mesma conexão e observações do nome físico do banco
e sidecars. Cada valor tem papel limitado; nenhum deles isolado atesta tudo.

- `total_changes` detecta DML da própria conexão, inclusive dentro da transação;
  não é contador de DDL, fronteira transacional ou mudança por outra conexão.
- `data_version` é um sinal adicional para commits externos, comparado sempre
  na mesma conexão; não detecta commits feitos por ela própria.
- Cookies de schema e a lista de bancos cobrem mudanças estruturais/configuração
  de namespace SQL. Não memoizar com bancos anexados, TEMP que possa sombrear
  tabelas, `read_uncommitted` ativo ou modo/conexão sem observação suportada.
- A continuidade dos nomes físicos usa observação conservadora do arquivo e
  sidecars: presença, tipo, links, device/inode e stamps disponíveis. Banco em
  memória ou caminho não observável usa misses. Troca/ausência observada de
  banco, WAL, SHM ou journal desabilita o memo daquela tentativa.

Mudança de DML/schema invalida todas as entradas antes de reutilizar qualquer
uma, inclusive PREPARED→COMMITTED/ABORTED/CONFLICT e escrita revertida. A
geração não é recuperada ao voltar a valores anteriores. Se a observação não
puder ser comprovada, o carregador atual é executado; erro inesperado não é
engolido como cache miss. Os guards atuais continuam recusando corrupção ou
fronteira inválida com seu erro categorizado.

A observação de nomes **não prova qual inode uma conexão SQLite já aberta
usa**, nem substitui os descritores/guards de `database_connection` e dos
serviços. Ela só invalida reuso perante mudanças observadas. Não se introduz
garantia nova contra adulteração pelo mesmo UID. O memo deve ser semanticamente
equivalente às leituras da mesma conexão/transação, inclusive quando uma
conexão antiga ainda enxerga seu snapshot; não confundir isso com autoridade
para escrever num home cujo guard recusou.

Para não substituir a carga evitada por centenas de stats/pragmas, é permitido
atestar o stamp antes/depois de um lote síncrono de carregamentos, desde que
não haja escrita, callback externo, mudança de transação ou efeito nesse lote.
Entre lotes ou acessos avulsos, reconferir a geração antes de qualquer hit.
As mesmas provas e regras valem para os dois modos; otimizar a amostragem do
stamp nunca pode criar uma janela sem invalidação.

Fontes primárias que limitam o desenho: [isolamento SQLite](https://www.sqlite.org/isolation.html),
[data_version](https://www.sqlite.org/pragma.html#pragma_data_version) e
[total_changes](https://www.sqlite.org/c3ref/total_changes.html). São referências
de semântica, não prova de que o memo foi implementado ou validado.

## Integração e fronteiras de responsabilidade

`kairos_state/repositories/skill_mutations.py` contém o dono e a integração do
carregador; um helper privado pequeno pode isolar o estado de observação.
Não expor knob ao usuário nem feature flag de produção. O caminho não elegível
permanece naturalmente sem reuso.

O primeiro incremento não memoiza validadores de schema ou timeline, não muda
SQL complexo, não filtra antes de validar e não altera coordenador de publicação,
serviços de adoção/remoção ou abertura segura do banco. Se o ganho útil exigir
atravessar esses limites, voltar ao desenho antes de expandir a implementação.

O implementador define nomes e organização interna do helper, localização dos
testes e detalhes dos stamps com as primitivas existentes. Não ficam em aberto
o dono, a duração, o descarte obrigatório, o tipo de resultado elegível ou a
preservação dos validadores. Dúvida de equivalência resulta em miss, não em
reuso otimista.

## Aceite e recuperação

Testes comportamentais com SQLite e filesystem reais, sem leitura de fontes:

1. Mesmo registro em duas leituras elegíveis evita uma reconstrução real e
   mantém valor, estado e resultado. As varreduras e os validadores executam.
2. Escrita na mesma conexão, commit, rollback, retry e novo dono não reutilizam
   estado anterior. A sequência PREPARED→COMMITTED é observada corretamente.
3. Outra conexão/home/repositório, transação não pertencente ao dono, SQL TEMP/
   ATTACH e contexto sem prova não obtêm hits. Concorrência de outro escritor
   exerce bloqueio/retry real e nova observação, sem leitura suja.
4. Corrupção de conteúdo/evento/linhagem, triggers alterados e pendência de
   outro nome continuam recusados, inclusive com memo previamente populado.
5. Troca byte-idêntica de banco/WAL e transições de sidecars invalidam o memo;
   os testes integrados mantêm a recusa dos guards de identidade atuais.
6. Mudança de política/binding entre as fronteiras continua vetando efeitos;
   adoção, revogação, remoção, restore, sync, promoção e recuperação mantêm seus
   testes. O limite de registros não muda semântica nem cresce sem controle.

O benchmark antes/depois usa o mesmo protocolo e ambiente em revisões fixadas,
com ordem alternada e tempos separados do profiler. Publicar dispersão, misses,
hits e custo do stamp, além de carregamentos de apoio efetivos: `_get_validated`
pode continuar sendo chamado 903 vezes, com parte das chamadas atendida pelo
memo. Instrumentar também `_legacy_get`/`_base`/`_blob` ou equivalente, sem
confundir entrada na função com consulta/reconstrução executada.

Preservar as 20 validações do caminho B2 comparável é gate de benchmark;
os testes funcionais verificam seus efeitos e fronteiras, sem criar teste
change-detector de contagem fixa. Demonstrar redução real de trabalho e ganho
de latência, sem esconder overhead em cenários sem hits.

Critério de aceitação proposto pelo Root, explicitado após a pergunta do
builder-check (é um gate de engenharia desta entrega, não SLA ou alegação de
significância estatística):

- Usar baseline B2 sem memo e candidata, ambas fixadas por SHA, no mesmo
  ambiente e harness. Três pares completos, em ordem baseline/candidata,
  candidata/baseline e baseline/candidata. Cada execução repete os cenários
  0/40/120 e suas sete amostras, com fixtures novas e perfil fora dos tempos.
- No cenário 120, a mediana da candidata deve ser menor nos três pares.
  A mediana das três reduções percentuais pareadas deve ser de pelo menos
  10%, junto de redução reproduzível das reconstruções efetivas e manutenção
  das 20 validações. A escolha de 10% busca ganho útil frente à complexidade
  adicionada; é proposta do Root sujeita à aprovação deste desenho.
- Nos cenários 0 e 40, recusar se o aumento mediano pareado superar
  simultaneamente 5% e 5 ms. Publicar os três pares, amostras e faixas, inclusive
  casos que não passem; não selecionar somente a melhor rodada.
- Registrar carga/ambiente e evitar probes concorrentes durante a rodada.
  Incidente comprovado de ambiente invalida o par completo, que permanece
  documentado; não repetir indefinidamente até obter aprovação.
- Resultado que não satisfaça esses critérios permanece inconclusivo ou
  insuficiente para o ROI proposto. Informar o resultado antes de ampliar
  escopo, mudar limiar ou recomendar merge; não remover guards para passar.

Antes de integrar: revisão independente, integração pelo Root, suíte completa,
imagem instalada e jobs remotos verdes. Reutilizar evidências somente quando
forem válidas para os arquivos/ambiente atuais. Rollback é reverter o código;
não há dado persistido ou migração para desfazer. Erros continuam propagados
ou categorizados com contexto sanitizado, sem log de conteúdo privado.

## Revisão do desenho

Builder-check independente, GPT-6 Luna/high, somente leitura: a pergunta
remanescente foi o critério de ganho conclusivo. O Root a tratou como decisão
técnica e acrescentou o protocolo/limiar acima; nenhuma decisão adicional de
produto foi atribuída à usuária. A revisão não executou testes nem atestou
implementação. Probes locais separados, em SQLite temporário real, confirmaram
as limitações dos contadores, a visibilidade de escritas próprias e o bloqueio
de outro escritor por BEGIN IMMEDIATE; isso fundamenta o desenho, não comprova
uma implementação de memoização.
