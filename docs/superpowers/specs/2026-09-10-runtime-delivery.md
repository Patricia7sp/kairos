# Entrega de alterações do runtime ao Git

## Objetivo aprovado

Completar o fluxo de trabalho real: selecionar uma revisão recente e imutável,
executar uma tarefa isolada, revisar o checkpoint, aplicar o pacote aprovado em
uma branch separada, testar e abrir PR. Demonstrar também recuperação após uma
interrupção abrupta do broker sem repetir automaticamente o turno interrompido.

## Fronteiras

O operador exporta commits do Git para um catálogo local montado somente para
leitura no broker. Cada versão tem diretório próprio; exportações anteriores não
são substituídas. A Web seleciona versões autorizadas e apresenta/download de
alterações. Git, execução de testes e publicação ficam em comandos explícitos do
operador, sem credenciais Git ou socket Docker no processo Web.

O catálogo contém `<commit>/workspace` e `<commit>/project.json`. O commit é
completo (SHA-1 ou SHA-256), os arquivos vêm apenas do Git, e links/submódulos são
recusados. O manifesto identifica revisão, nome do projeto e fingerprint dos
arquivos exportados. O broker descobre catálogos configurados na inicialização;
novas exportações ficam disponíveis após reinício ocioso. Diretórios legados
continuam autorizados e as sessões preservam sua identidade original.

Cada nova sessão Docker guarda um baseline imutável antes de criar o worker.
O pacote de revisão compara esse baseline com o último checkpoint durável e
contém somente o workspace, nunca o home do agente. Sessões antigas sem baseline
retornam uma explicação explícita. Durante turno ativo não há exportação.

O JSON de revisão contém identidade da sessão, commit-base quando disponível,
fingerprint do baseline, digest do checkpoint, mudanças completas (conteúdo,
hash e modo), diff textual e um identificador SHA-256 de aprovação. O limite do
pacote serializado é 512 KiB, para caber no transporte IPC de 1 MiB. Pacotes
maiores falham explicitamente, sem truncar mudanças. Nomes e conteúdo são
tratados como dados; caminhos fora do workspace e componentes `.git` são
recusados. Binários têm representação base64 e indicação explícita no diff.

A aplicação exige `--approve` igual ao identificador revisado. Valida o pacote e
reconstrói o baseline a partir do commit no repositório antes de escrever. Cria
worktree e branch novos, aplica exatamente as mudanças aprovadas e executa os
testes especificados como argv, sem shell. Falha de teste mantém a worktree para
diagnóstico, sem commit/publicação. Testes não podem acrescentar mudanças além
do pacote. O commit usa apenas os arquivos aprovados e desativa hooks. Um recibo
vincula revisão, base, commit resultante, branch, worktree e testes aprovados.
Publicação verifica recibo, HEAD e worktree limpa antes de push sem force e PR
draft. O repositório de origem permanece intacto.

## Aceite

Testes unitários cobrem exportação idempotente, caminhos perigosos, pacote
adulterado/grande, adição/edição/exclusão/binário/modo, baseline errado, aprovação
errada, teste falho e alteração extra. API/UI cobrem revisão autenticada, conflito
com turno ativo, seleção de versão, diff tratado como texto e download completo.
Um teste opt-in com Docker mata um broker descartável por SIGKILL durante uma
ferramenta, reinicia com o mesmo estado, comprova checkpoint anterior preservado,
um único terminal interrompido, nenhum replay, limpeza de worker e retomada
explícita na mesma thread. A produção recebe backup com restauração verificada,
imagens validadas e um aceite real de tarefa até PR usando os novos comandos.

## Dependência de imagem do worker

Durante a execução, a imagem local do worker foi removida novamente enquanto não
havia workers ativos. O broker detectou a ausência e ficou indisponível. A entrega
inclui uma referência mínima em execução à mesma imagem do worker, sem rede,
volumes, credenciais ou privilégios adicionais, e um arquivo de imagem preservado
para restauração. Essa referência protege a dependência contra limpeza de imagens
sem uso; não promete resistir a remoção forçada pelo administrador. A causa externa
da remoção ainda não foi confirmada. A configuração do broker e a referência de
retenção devem apontar para o mesmo identificador imutável.
