# Sandbox externa: protótipo isolado

Desenho autorizado pelo usuário em 2026-09-07: separar o serviço principal
do container que executa o agente; avaliar antes de habilitar em produção.

## Entrega e fronteiras

Entregar um protótipo executável offline, com Codex 0.153.4 real e a política
`externalSandbox`, um Dev Container para desenvolvimento e evidência repetível
de isolamento. O protótipo não é backend habilitável pela configuração de
produção. O runtime existente e o Compose de produção permanecem inalterados.

O launcher confiável roda no host com acesso já autorizado ao Docker. O agente
não recebe socket Docker, diretórios do host, banco, cofre nem credenciais.
Cada worker recebe uma cópia limitada do projeto por arquivo tar validado,
antes de iniciar o App Server. Não há exportação automática ao original.

## Worker

Imagem separada sem Kairos, s6 ou VOLUME, pacote Codex com versão e checksum
fixados. Resolver a imagem para ID antes de criar. Docker local com diretório
de configuração vazio para impedir herança de contextos/proxies/credenciais.
Rede `none`, rootfs somente leitura, UID/GID 10000, capabilities removidas,
no-new-privileges, seccomp e AppArmor padrão, init e namespaces privados.
Limites: 1 CPU, memória e swap total 512 MiB, 128 processos, shm 16 MiB;
tmpfs de workspace 128 MiB, home 64 MiB e temporários 64 MiB.

Importação por descritores sem seguir symlinks, rejeitando hardlinks e arquivos
especiais. Máximo 64 MiB de conteúdo e 10.000 entradas. Excluir metadados Git,
caches e `.env*`; isso não é um detector geral de segredos: o projeto de entrada
deve ser explicitamente autorizado. Snapshot nunca inclui caminhos externos.
No modo read_only, importador root confiável cria diretórios/arquivos de root
sem escrita por UID 10000; no modo workspace_write o importador é UID 10000.
O agente nunca executa como root. Scratch e CODEX_HOME continuam graváveis em
ambos os modos. Nenhuma opção de broad_access ou rede é exposta no protótipo.

## Protocolo e ciclo de vida

Reutilizar CodexRpc em stdio através de `docker exec -i`, após validar inspect,
versão e initialize. Executar `command/exec` com externalSandbox e rede
restricted. A política declarada depende da fronteira Docker validada; não
significa que o Codex aplica o isolamento. Comandos com timeout e saída limitada.
Investigar também criação de thread, sem chamada paga de modelo.

Nome aleatório conhecido antes de create permite remover inclusive criações
cuja resposta se perdeu. Em falha, timeout ou cancelamento: remover worker e
volumes próprios, reap do cliente Docker e fechar RPC. Não reutilizar workers
entre projetos nem tentar repetir turnos. Limpeza falha deve ser visível.

## Dev Container

Ambiente de desenvolvimento separado do worker: ferramentas Python e Node,
apenas cópia do repositório em volume próprio, sem configuração de produção.
Sem Docker socket nem privilégios extras. O launcher Docker é executado pelo
host; testes unitários e edição podem rodar no Dev Container. A configuração
não reivindica as mesmas garantias do worker atestado.

## Aceite

Testes unitários de snapshot, política/inspect e limpeza; integração real
opt-in sem autenticação que prove execução, negações de acesso ao host/rede,
read_only, separação de dois workers e remoção após cancelamento. Registrar
resultados e limitações, incluindo kernel compartilhado, recurso de disco
temporário e ausência de autenticação/rede para modelos. Integrar Web/CLI,
broker de execução, egress e credenciais de escopo mínimo é uma etapa posterior
à validação deste protótipo, não uma capacidade já entregue.
