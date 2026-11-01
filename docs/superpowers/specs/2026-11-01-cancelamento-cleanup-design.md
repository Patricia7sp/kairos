# Cancelamento externo durante limpeza assíncrona

**Estado:** proposta para aprovação; nenhuma correção de produção aplicada.
**Base examinada:** `a2987ccbebe94bfed0049024ee0d4fdb067653b5`.
**Escopo solicitado:** as 11 ocorrências abertas de `python:S7497` na main.

## Intenção e contrato

Cancelar um turno ou fechar um recurso não pode terminar como sucesso depois
de consumir o cancelamento do próprio chamador. A limpeza dos recursos já
adquiridos deve concluir antes de devolver esse cancelamento, conservando as
falhas de limpeza e a responsabilidade por recursos ainda não encerrados.

O cancelamento esperado de uma tarefa filha, solicitado por seu proprietário
para encerrá-la, deve continuar sendo consumido sem tornar um fechamento
normal em falha. Os dois sinais precisam ser distinguidos.

A referência existente é `docs/decisoes.md`, **D-PLUG.6**: quem cancela o turno
continua cancelando; `run_persistent_cleanup` preserva esse sinal em
`PersistentCleanupOutcome.cancellation`. A documentação de
[cancelamento do Python 3.11](https://docs.python.org/3.11/library/asyncio-task.html#task-cancellation)
explica a relação com `TaskGroup` e `asyncio.timeout`.
Não há nova política de timeout, aprovação, workspace ou credenciais.

## Diagnóstico reproduzido

As reproduções usam eventos e callbacks de conclusão, sem temporizações
arbitrárias ou consultas a provedores. O chamador recebe uma solicitação de
cancelamento quando a tarefa filha termina, antes de seu `await` retornar.
Hoje ele termina normalmente, com `cancelled() == False`, apesar de conservar
uma solicitação de cancelamento. Foram reproduzidos os seguintes casos;
os dois de ownership estão em blocos `finally` próximos aos alertas, não
nos `except` especificamente apontados pelo scanner:

| Componente | Falha observada |
| --- | --- |
| `CodexRpc.aclose` | Cancelamento do chamador consumido ao aguardar o leitor cancelado. |
| `ModelRelay.aclose` | Cancelamento do chamador consumido ao aguardar o runner. |
| `ModelRelay.end_turn` / `_cancel_request` | Cancelamento do chamador consumido ao aguardar a requisição. |
| `SessionTurnOwnership.acquire` | Cancelamento durante o encerramento do heartbeat é consumido; a saída aparenta sucesso. |
| `SessionTurnOwnership._refresh_before_deadline` | Cancelamento na limpeza da tarefa de deadline é consumido e a renovação retorna normalmente. |
| Lifespan Web | Cancelamento durante o encerramento do scheduler é consumido pelo shutdown. |
| `TelegramInbound._typing_loop` | A tarefa diretamente cancelada retorna normalmente. |

O caminho completo do Telegram revelou duas regressões relacionadas:
`_stream_turn` captura `BaseException`, convertendo cancelamento em mensagem
de erro, e a espera da tarefa de digitação pode consumir um novo cancelamento
do despacho. Em ambos os experimentos, houve uma tentativa de envio após o
cancelamento. O canal e o router foram substitutos em memória; nenhum envio
real nem chamada a modelo ocorreu.

Cinco alertas correspondem diretamente às perdas acima: RPC, os dois pontos
do relay, shutdown Web e tarefa de digitação. Nos outros seis, o tratamento
especificamente apontado não demonstrou perda do cancelamento:

| Ocorrência | Contrato que deve permanecer |
| --- | --- |
| `skill_catalog_turn._local_io` | Aguarda a operação em thread terminar e relança o cancelamento original. |
| `SQLiteAsyncInteractionPersistence._call` | Aguarda o worker SQLite e relança o cancelamento; falha operacional continua explícita. |
| `run_persistent_cleanup` | Devolve o cancelamento em um resultado explícito consumido pela fronteira chamadora. |
| `CodexSupervisor._finish_stderr` | Consome o cancelamento esperado de sua tarefa filha dentro da limpeza protegida; a fronteira pública conserva o cancelamento externo. |
| `SessionTurnOwnership.acquire:323` | Relança o cancelamento original ou o traduz em perda de posse, conservando a causa. A perda reproduzida está na limpeza posterior do heartbeat. |
| `SessionTurnOwnership._refresh_before_deadline:459` | Consome o cancelamento do filho e relança o cancelamento original no `except` externo. A perda reproduzida está no `finally` que limpa o deadline. |

Essas seis classificações permanecem candidatas a falso positivo até a
revisão e o aceite integrado. Nenhum S7497 foi reclassificado remotamente.
Isso não atesta a segurança de toda a função: os defeitos adjacentes de
ownership também integram a correção, independentemente da localização do
alerta.

Evidências locais em `.superpowers/evidence/sonar-cancelamento-20261101/`:
`initial-probes.json`, `ownership-web-probes.json`,
`telegram-turn-probes.json` e `preserved-cancellation-probes.json`.
A baseline focada passou: **39 testes**, com 213 itens fora desse recorte.
Não é aprovação das correções ainda inexistentes nem da suíte integral.

## Abordagem proposta

Reaproveitar a coordenação persistente existente nas fronteiras que possuem
os recursos. Aguardar uma tarefa filha cancelada deve tratar seu resultado
como o resultado daquela tarefa, sem capturar indistintamente o cancelamento
injetado no chamador. Um helper pequeno no módulo existente de limpeza pode
compartilhar essa distinção; nenhuma ferramenta ou dependência nova entra no
core.

Nos fechamentos que precisam percorrer vários recursos, proteger a operação
completa: encerrar a primeira tarefa não pode interromper o fechamento do
writer, a liberação de leases ou a limpeza do serviço. A propagação ocorre
depois dessa etapa, usando o resultado explícito já adotado pelo projeto.

Alternativas examinadas:

- Relançar todo `CancelledError` nas esperas de filhos quebraria fechamentos
  normais e os consumidores de `PersistentCleanupOutcome`.
- Verificar somente contadores `cancelling()` espalharia a distinção pelos
  consumidores e confundiria pedidos anteriores com cancelamentos novos.
- A coordenação já existente conserva a semântica aprovada de limpeza e
  oferece um ponto comum para testar cancelamento repetido. É a proposta do
  Root, ainda sujeita à aprovação deste desenho.

## Alterações delimitadas

- `kairos_providers/_async_cleanup.py`: estender o módulo somente se for
  necessário compartilhar a espera de filhos; preservar as interfaces e o
  resultado de `run_persistent_cleanup` e `AsyncCleanupCoordinator`.
- `kairos_runtime/codex_rpc.py`: fechar leitor, tarefas auxiliares e writer
  conservando o cancelamento externo. Fechamentos repetidos ou concorrentes
  devem conservar responsabilidade e resultado, com possibilidade de retry
  quando o fechamento real falhar.
- `kairos_runtime/docker_backend/model_relay.py`: revogar a admissão antes
  de drenar a requisição; limpar runner, requisição e writer. Cancelamento
  não pode reabilitar um turno nem perder a referência a trabalho pendente.
- `kairos_integration/turn_ownership.py`: limpar heartbeat e deadline sem
  perder cancelamento; concluir o protocolo de liberação durável/local e
  impedir que um sucessor entre antes de a limpeza do proprietário terminar.
  A exceção já existente de exaustão de tentativas permanece: depois de
  drenar o trabalho, emitir o aviso e aguardar a expiração natural do lease
  durável, liberando o gate local. A aquisição durável continua recusando
  um sucessor enquanto o lease anterior for válido. Não ampliar tentativas,
  remover o lease por força nem introduzir um novo erro público nesse caso.
- `kairos_web/server.py`: preservar o cancelamento do shutdown depois de
  encerrar scheduler e serviço próprio e limpar o estado da aplicação.
  Serviço injetado permanece sob responsabilidade de quem o injetou.
- `kairos_gateway/inbound.py`: propagar cancelamento da digitação e do
  stream; drenar a tarefa auxiliar e impedir envio final após cancelamento.
  Não alterar autorização do canal, protocolo ou tratamento dos demais
  erros operacionais neste lote.

Um erro de limpeza não vira sucesso. Nas fronteiras novas de fechamento,
se falha real de limpeza e cancelamento externo ocorrerem juntos, a falha
de limpeza é primária e o `CancelledError` externo fica como causa explícita
(`raise error from cancellation`), como nos leitores de thread/SQLite já
existentes. Se só houver cancelamento, relançá-lo depois do dreno. O
cancelamento esperado do filho não é uma falha de limpeza.

Não alterar a precedência existente de `TurnLeaseLostError`, `GeneratorExit`
e falhas de observadores nos compositores de turno. As regressões existentes
de erro aninhado precisam continuar passando. O fechamento de tarefas filhas
não pode descartar exceções inesperadas nem publicar seus detalhes sensíveis
em logs. D-PLUG.6 permanece vigente.

## Aceite e validação

1. Transformar as reproduções em regressões comportamentais e observar RED
   contra a implementação atual antes de alterar produção.
2. Exercitar cancelamento externo simples/repetido, filho já terminado,
   fechamento normal, erro real de limpeza e cancelamento durante o dreno.
3. Verificar efeitos: tarefas terminadas, writers fechados, nenhuma entrega
   Telegram após cancelamento e leases liberados na ordem correta, sem
   sobreposição do sucessor.
4. Confirmar os seis tratamentos preservados com operação real em thread,
   SQLite temporário, perda de posse e subprocesso controlado do supervisor.
   Verificar também a exaustão de liberação: o sucessor só adquire a posse
   durável quando o lease anterior expirar, depois de concluído o dreno.
5. Rodar a suíte integrada pertinente e `scripts/ci.sh` completo, com Python
   3.11 e Codex 0.154.0 conferido por SHA-256, builds e integrações Docker.
   `runtime_live` e exportação Sonar local continuam desabilitados.
6. Obter revisão independente e conferir CI/Sonar do SHA publicado. Só
   registrar os seis falsos positivos depois de sua revisão específica;
   defeitos reais precisam de correção e nova análise, sem exclusão de regra.

Execução proposta: Root sequencial, com revisão independente ao final.
O implementador decide nomes privados, pequenos desdobramentos de funções e
organização dos testes, preservando as interfaces públicas e os contratos
acima. Não ampliar para todos os outros `except` do projeto, rever timeouts,
adicionar `uncancel()` generalizado, alterar schema ou acessar credenciais
reais.

## Entrega e limite de autorização

Este documento registra a proposta de implementação do lote S7497. A
autorização de merge/deploy do PR #126 já foi executada e não é tratada como
aprovação automática de uma nova entrega de código.
Após aprovação deste desenho, preparar o plano, implementar e validar a
correção e apresentar a branch/PR concretos antes da decisão de integração.
