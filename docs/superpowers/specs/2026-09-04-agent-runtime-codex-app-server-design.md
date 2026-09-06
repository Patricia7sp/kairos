# Agent Runtime Protocol v1 e Codex App Server

**Data:** 2026-09-04
**Estado:** design aprovado; aguardando revisão da especificação
**Escopo:** aplicação local, de usuário único

## Objetivo

Permitir que uma sessão do Kairos use um agente stateful executado pelo Codex
App Server, sem misturar esse ciclo de vida com as chamadas de modelos já
atendidas pelo `InteractionService`. A primeira versão introduz o
`AgentRuntimeProtocol v1` e um adapter para Codex. Claude e Gemini poderão
receber adapters posteriores sem alterar as superfícies Web e CLI.

O resultado deve preservar a thread externa entre turnos e reinícios, expor
streaming, tools, aprovações e cancelamento e impedir execução concorrente que
possa alterar o mesmo diretório de trabalho.

## Decisões de escopo

1. A primeira versão é local e de usuário único. Os contratos podem carregar
   identidade futura, mas não implementam tenancy ou servidor remoto.
2. Codex é o único runtime entregue no v1. Claude e Gemini ficam fora desta
   implementação.
3. O Kairos aceita os fluxos oficiais de autenticação do Codex: login ChatGPT
   por navegador/device code e API key.
4. Tokens e detalhes internos de autenticação permanecem sob responsabilidade
   exclusiva do App Server. O Kairos apenas orquestra login, status e logout.
5. Um processo Codex App Server é compartilhado e supervisionado pelo Kairos.
6. Há no máximo um turno ativo por sessão. Sessões em diretórios diferentes
   podem executar em paralelo; sessões no mesmo diretório são serializadas.

## Arquitetura

Uma sessão declara um de dois modos de execução:

- `model`: usa o `InteractionService` existente e seus providers;
- `agent_runtime`: usa o novo `AgentRuntimeService`.

As superfícies enviam o mesmo envelope a um `InteractionRouter`, que carrega a
sessão e escolhe o serviço apropriado. Essa fachada preserva paridade entre Web
e CLI sem obrigar providers stateless a simular threads externas.

```text
Web / CLI
    |
InteractionRouter
    |-------------------------|
InteractionService     AgentRuntimeService
(providers atuais)             |
                         AgentRuntimeProtocol v1
                                |
                       CodexAppServerAdapter
                                |
                      Codex App Server compartilhado
```

### InteractionRouter

O roteador valida a identidade da sessão e encaminha cada comando conforme
`execution_kind`. Ele não executa providers, runtimes ou regras de aprovação.
Sessões legadas sem esse campo são tratadas como `model`.

### AgentRuntimeService

O serviço coordena:

- criação, retomada e encerramento de threads externas;
- exclusão mútua por sessão e por diretório;
- envio de mensagens e consumo de streaming;
- persistência e publicação ordenada de eventos;
- pedidos e respostas de aprovação;
- cancelamento e recuperação após desconexões ou reinícios.

### AgentRuntimeProtocol v1

O protocolo é independente de Codex e define operações para:

- consultar a versão e as capacidades do runtime;
- criar e retomar uma thread;
- iniciar, observar e cancelar um turno;
- responder a uma solicitação de aprovação;
- reconciliar eventos a partir de um cursor;
- consultar o estado de uma thread ou turno após falha.

Cada evento inclui `protocol_version`, `event_id`, `session_id`, `turn_id`,
sequência monotônica e cursor de retomada. Eventos repetidos são idempotentes.
Campos novos são aditivos e ignoráveis; alterações incompatíveis exigem nova
versão principal.

O runtime anuncia capacidades como streaming de texto, reasoning, tools,
aprovações, cancelamento e retomada. O Kairos só oferece ou invoca operações
declaradas. Uma versão incompatível impede o início do turno com erro
acionável.

### CodexAppServerAdapter

O adapter traduz operações e eventos do App Server para o protocolo canônico.
Ele não lê, copia nem interpreta tokens do Codex. O supervisor inicia um único
processo, monitora sua saúde e o reinicia quando necessário. Reiniciar o
processo não autoriza reenviar um turno.

## Identidade e imutabilidade da sessão

Antes do primeiro turno, a sessão recebe:

- `execution_kind`;
- `runtime_kind`, quando aplicável;
- diretório de trabalho autorizado;
- perfil de sandbox.

No primeiro turno de runtime, o Kairos cria a thread e persiste
`external_thread_id`. A partir daí, esses cinco valores são imutáveis. Trocar
runtime, thread, projeto ou perfil de sandbox exige criar outra sessão Kairos.
A nova sessão pode referenciar a anterior como origem para preservar a
navegação do histórico, mas não reaproveita sua thread.

Somente diretórios previamente autorizados na configuração podem ser usados.
O caminho deve ser normalizado e validado antes da criação da sessão e antes
de adquirir o lease.

## Sandbox e aprovações

O Kairos oferece três perfis administrados:

- `read_only`: nenhuma alteração no projeto;
- `workspace_write`: escrita restrita ao diretório autorizado;
- `broad_access`: acesso ampliado, habilitado explicitamente pelo usuário.

O App Server aplica tecnicamente a sandbox. O Kairos envia a política
declarada, registra o perfil e impede que um adapter eleve permissões sem uma
nova sessão e consentimento explícito.

Pedidos de aprovação são persistidos e encaminhados para Web e CLI. O turno
permanece em `waiting_approval` até uma resposta explícita. Se a interface se
desconectar, o pedido continua pendente. Não há aprovação automática, timeout
permissivo nem resposta presumida. A decisão é persistida antes de ser enviada
ao App Server.

## Concorrência e leases

Dois controles complementares protegem a execução:

1. um lease de sessão garante no máximo um turno ativo naquela sessão;
2. um lease de diretório garante no máximo um turno ativo entre todas as
   sessões vinculadas ao mesmo projeto.

Turnos que disputam o diretório entram em fila. A fila não bloqueia turnos de
outros diretórios. A chave do lease deriva do caminho canônico, não do texto
fornecido pelo cliente, para impedir aliases por caminho relativo ou symlink.

Expirar ou recuperar um lease permite reconciliar o estado; nunca permite
reproduzir automaticamente a mensagem. O detentor e a renovação do lease são
persistidos de modo que reinícios não criem dois donos aparentes.

## Fonte de verdade

O Codex App Server é a autoridade do estado operacional da thread: contexto
interno, continuidade e execução de tools. O Kairos é a autoridade do registro
exibido e auditável: mensagens, eventos normalizados, decisões de aprovação,
uso, estados e vínculo com a thread externa.

Ao retomar uma sessão, o Kairos usa `external_thread_id`; não reconstrói uma
thread Codex a partir do transcript local. Se a thread externa desaparecer, a
sessão fica não retomável, preserva seu histórico e oferece criar uma sessão
sucessora.

## Fluxo de um turno

1. Web ou CLI envia `session_id`, conteúdo e uma chave idempotente.
2. O `InteractionRouter` carrega a sessão e seleciona `AgentRuntimeService`.
3. O serviço valida diretório e política e adquire os leases da sessão e do
   diretório.
4. No primeiro turno, cria a thread e persiste ID, capacidades e política. Nos
   seguintes, retoma a thread persistida.
5. A mensagem do usuário é persistida antes de ser enviada ao runtime.
6. O adapter inicia o turno e converte cada evento para o protocolo canônico.
7. Cada evento é validado e persistido antes de ser publicado ao WebSocket.
8. Aprovações suspendem o turno até a decisão explícita persistida.
9. No término, o serviço registra resposta, uso, estado e cursor final e libera
   os leases.
10. Cancelamento solicita interrupção, aguarda confirmação e registra o
    resultado sem presumir reversão de ferramentas.

Se o cliente desconectar, o turno continua. Na reconexão, o cliente informa o
último cursor confirmado e recebe apenas eventos posteriores.

## Estados

Uma sessão de runtime pode estar em:

- `ready`: apta a iniciar um turno;
- `running`: turno em execução;
- `waiting_approval`: execução suspensa por decisão do usuário;
- `recovering`: Kairos reconciliando o estado externo;
- `interrupted`: resultado do último turno inconclusivo;
- `unavailable`: runtime ou thread indisponível;
- `ended`: sessão encerrada explicitamente.

O turno possui estado próprio. Um turno negado, cancelado ou interrompido não
encerra automaticamente uma sessão retomável.

## Recuperação e idempotência

O Kairos nunca reenvia automaticamente uma mensagem depois de falha, pois uma
tool pode já ter produzido efeitos externos.

Após reinício, turnos não terminais entram em `recovering`. O serviço consulta
o runtime e reconcilia eventos usando cursor e `event_id`:

- se o turno segue ativo, retoma o streaming;
- se terminou, persiste os eventos e o estado final faltantes;
- se o resultado não puder ser determinado, marca `interrupted` e oferece
  continuar com um novo turno ou criar uma nova sessão;
- se a thread desapareceu, marca a sessão como não retomável e oferece uma
  sessão sucessora.

Eventos repetidos são descartados por `event_id`. Uma lacuna de sequência
aciona reconciliação; não é silenciosamente ignorada. O estado confirmado pelo
App Server prevalece para a execução, enquanto eventos já persistidos no
Kairos permanecem no registro auditável.

## Persistência

A migração deve manter sessões existentes compatíveis e introduzir estruturas
para, no mínimo:

- modo de execução, runtime e thread externa da sessão;
- diretório canônico e perfil de sandbox;
- versão e capacidades negociadas;
- turnos de runtime e suas chaves idempotentes;
- eventos ordenados e cursores;
- pedidos e decisões de aprovação;
- leases de diretório e fila de espera.

As tabelas e índices exatos serão definidos no plano de implementação após
inspeção do schema atual. Chaves estrangeiras devem preservar a integridade de
sessões e turnos, e migrações devem ser idempotentes e transacionais.

## Erros e segurança

Erros canônicos distinguem:

- runtime indisponível ou incompatível;
- thread externa ausente;
- diretório ou política inválidos;
- conflito ou perda de lease;
- aprovação negada;
- cancelamento confirmado ou de resultado parcial;
- falha de transporte;
- evento duplicado, fora de ordem ou inválido;
- falha interna do runtime.

Tokens, headers de autenticação e detalhes internos do login não entram no
banco, eventos ou logs do Kairos. Erros são redigidos antes de persistência e
publicação. Conteúdo parcial já recebido é preservado.

## Web e CLI

Web e CLI oferecem as mesmas operações essenciais: criar sessão escolhendo
runtime, diretório e sandbox; enviar mensagem; acompanhar streaming; responder
aprovação; cancelar; reconectar e criar sessão sucessora.

A interface diferencia claramente sessões `model` de `agent_runtime`, mostra
o projeto e o perfil de sandbox fixados e explica quando um turno aguarda a
liberação do lease do diretório. Operações não anunciadas nas capacidades do
runtime não aparecem como disponíveis.

## Testes e aceite

A CI usa um App Server simulado e não depende de login real nem chamadas
pagas. Deve comprovar:

- criação e retomada da mesma thread externa;
- imutabilidade de runtime, diretório e sandbox;
- um turno ativo por sessão;
- serialização no mesmo diretório e paralelismo entre diretórios;
- ordenação, deduplicação e retomada por cursor;
- persistência anterior à publicação;
- aprovação pendente durante desconexão e resposta explícita;
- negação e cancelamento sem replay;
- recuperação após reinício dos dois processos;
- tratamento seguro de thread desaparecida;
- negociação de capacidades e rejeição de versão incompatível;
- ausência de credenciais em banco, eventos e logs;
- paridade entre Web e CLI;
- regressão completa do caminho de providers existente.

O aceite operacional também exige login e logout oficiais, retomada após
reinício real, concorrência entre dois projetos, fila no mesmo projeto,
container saudável depois do redeploy e CI completa verde.

## Estratégia incremental

1. Introduzir schema, tipos e contrato do `AgentRuntimeProtocol v1` sem alterar
   o caminho de providers.
2. Implementar leases de diretório, fila e persistência de eventos/aprovações.
3. Implementar o `CodexAppServerAdapter` e seu supervisor contra um servidor
   simulado.
4. Compor `AgentRuntimeService` e `InteractionRouter`.
5. Integrar WebSocket, REST e CLI com testes de paridade.
6. Entregar fluxos Web de sessão, streaming, aprovações e recuperação.
7. Validar autenticação e retomada reais de forma opt-in.
8. Construir a imagem, redeployar e validar a saúde do stack.

Cada etapa deve deixar o caminho `model` executável e testado. O novo modo só
é habilitado na interface quando contrato, adapter e persistência estiverem
disponíveis.

## Fora de escopo

- servidor remoto multiusuário e tenancy;
- adapters de Claude, Gemini ou outros agentes;
- migração dos providers atuais para `AgentRuntimeProtocol`;
- múltiplos App Servers para balanceamento;
- troca de runtime, diretório ou sandbox dentro da mesma sessão;
- reconstrução de thread externa a partir do transcript;
- aprovação automática;
- rollback automático de efeitos produzidos por tools.

## Referências internas

- `docs/superpowers/specs/2026-08-26-providers-chat-completion-design.md`
- `docs/superpowers/specs/2026-08-27-interaction-accounting-lifecycle-design.md`
- `docs/providers-e-chat.md`
- `kairos_integration/interaction_service.py`
- `kairos_integration/turn_ownership.py`
- `kairos_integration/event_protocol.py`
