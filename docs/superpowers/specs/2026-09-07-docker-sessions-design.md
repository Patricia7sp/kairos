# Sessões Docker com login ChatGPT

Continuação autorizada do protótipo externo; usuário escolheu login ChatGPT.
O backend local existente continua padrão. O novo backend é opt-in, testado em
homedir/projetos temporários antes de qualquer habilitação em produção.

## Arquitetura

O host de runtime torna-se o broker confiável: possui Docker, estado durável e
um Codex App Server dedicado somente a autenticação. Reutiliza RuntimeAuth para
login ChatGPT/device-code, refresh e logout; nunca importa o perfil pessoal do
operador. Web/CLI continuam usando RuntimeClient e o socket Unix com SO_PEERCRED.
O worker recebe somente uma cópia do projeto, estado próprio de sessão e acesso
limitado ao modelo. Nunca recebe tokens ChatGPT, auth.json do broker, Docker
socket, banco principal ou rede externa.

Um worker por sessão; máximo configurado de workers concorrentes. Cwd interno
fixo `/workspace`; políticas read_only/workspace_write são aplicadas pelo Docker
atestado. broad_access é recusado neste backend. Thread/start/resume usa política
read-only (nenhuma execução nessa chamada); cada turn/start declara externalSandbox.
O adaptador valida política retornada e mantém identidade/roteamento de sessão.

## Canal do modelo

Uma ponte HTTP em loopback dentro do worker transmite somente POST /responses
por stdio ao broker. Não há rede Docker nem bind mount de socket. O broker permite
requisições apenas durante um turno, limita corpo/saída/número de requisições,
fixa o modelo e encaminha exclusivamente ao endpoint ChatGPT do Codex, com TLS
e sem redirects. Cabeçalhos do worker não atravessam essa fronteira. Tokens são
lidos do perfil dedicado após refresh pelo Codex e usados apenas no broker.
Erros retornados ao worker são genéricos; respostas SSE são limitadas e não
incluem cabeçalhos sensíveis. Websockets de modelo ficam desativados nessa ponte.
Testes usam transport HTTP simulado/local, sem autenticação real nem cobrança.

## Persistência e recuperação

Registry próprio em sandbox-state guarda identidade de sessão, worker conhecido
antes de create, thread e checkpoints. Arquivos tar são limitados, sem links nem
arquivos especiais, armazenados atomicamente como conteúdo opaco; nunca extraídos
no host. Workspace e CODEX_HOME do worker são separados do perfil de autenticação.
Após turno terminal, encerrar App Server, capturar checkpoint e remover container.
O turno só é publicado como concluído após o checkpoint. Exportação é um artefato
revisável, sem escrita automática no projeto original.

No restart, remover e confirmar ausência dos workers registrados antes de admitir
novos. Incerteza de criação/limpeza mantém o backend indisponível. Restaurar apenas
checkpoint confirmado; turno abandonado não é repetido. Alterações posteriores
ao último checkpoint podem se perder após crash abrupto e devem ficar marcadas
como incertas pelo serviço existente. Corrupção de archive/identidade recusa sessão.

## Integração e aceite

Configurar `agent_runtime.backend: docker` com imagem, modelo e limite de workers;
o host pode rodar separado do processo Web/CLI, que recebe somente o socket de IPC.
Login API-key é recusado no novo backend: a escolha desta entrega é ChatGPT.
Não há deploy automático da produção nesta etapa. Entregar configuração de exemplo
e roteiro de login dedicado; login interativo é a etapa que depende do usuário.

Aceite: testes de fronteira do relay/credenciais, arquivos maliciosos e checkpoints;
turno real do Codex contra modelo simulado executando ferramenta dentro do worker;
roteamento de duas sessões, restart/retomada, cancelamento e aprovação sem escape;
integração via RuntimeClient e superfícies existentes, regressão Python/frontend,
revisão independente. Manter isolamento Docker padrão sem perfis extras do host.
