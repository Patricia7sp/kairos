# Aceite da entrega revisada do runtime — 2026-09-11

## Resultado

O [PR #19](https://github.com/Patricia7sp/kairos/pull/19) foi integrado e
implantado no commit `a8a0c80d31bf374a5e4f3324c3c136ec97168dce`, após revisão
independente e aprovação dos nove jobs da CI. O conteúdo desse merge é idêntico
ao candidato testado `218ea128a795fd15d07766f5d3f0aa338804fab9`.

O aceite real percorreu seleção da versão imutável, tarefa no worker Docker,
revisão do diff na Web, download do pacote, aplicação em nova branch/worktree,
testes obrigatórios e publicação pelo comando `kairos runtime publish`.
O resultado é o [PR draft #20](https://github.com/Patricia7sp/kairos/pull/20),
commit `67f5d1bde49bef42610ba2fd37b7971c0488c8cb`, com todos os nove checks
aprovados. Ele acrescenta somente a seção de descoberta do guia ao README e
permanece em rascunho para revisão, conforme o fluxo aprovado.

## Evidências

- Revisão aprovada: `23952cd6368206f5a523f58004928080ca78496013eb27e4f6cde0d670d0954c`.
- Baseline: `3dbfad61cf3b2ec3bba6a07e03da08b9990bebeb9d8c44faadf7d41a7b347848`.
- A tarefa alterou apenas `README.md`; a Web exibiu o diff e baixou o JSON
  completo. O histórico permaneceu após recarregar a página, sem erros JavaScript.
- O operador verificou a seção exata e executou os testes de catálogo e pacotes
  de revisão. Ambos os comandos terminaram com código zero; o recibo vincula
  aprovação, commit, árvore Git e comandos de teste.
- Auditoria após o aceite: aplicação e broker saudáveis, zero turnos ativos,
  zero workers registrados, nove sessões Docker anteriores preservadas,
  quinze blobs validados e fingerprint da versão exportada inalterado.
- O serviço permanente `kairos-runtime-worker-image` retém o mesmo ID imutável
  configurado no broker. A retenção temporária só foi removida após a validação
  do serviço permanente. Os diretórios legados continuam autorizados.

## Validação e recuperação

A validação local passou em 1.722 testes Python e 5.562 subtestes, usando o
Codex 0.153.4 fixado pelo projeto; 24 testes Docker offline; 22 testes e
23 subtestes das imagens; 96 testes dos três frontends; TypeScript, Ruff,
shellcheck, gate de recall e lockfile. A CI remota aprovou os nove jobs.

O teste com SIGKILL de um broker descartável comprovou checkpoint e baseline
preservados, descarte de arquivos não confirmados, um único terminal interrompido,
ausência de replay e retomada explícita na mesma thread. A queda antes da
confirmação do envio tem regressão própria: a incerteza é persistida sob controle
de posse, e um proprietário antigo não pode gravar. Sem comprovação de inatividade
do proprietário anterior, a sessão continua bloqueada.

Antes da implantação, a restauração do backup comparou 4.251 arquivos e validou
oito checkpoints, dez sessões e treze turnos. O SHA-256 do arquivo é
`09c305dfc877dcbd238dae881db80b529047e4765ebe5dcf8e9757b603c870cf`.
As imagens da aplicação, broker e worker foram arquivadas por ID imutável.
Backups, recibo, pacote e capturas permanecem em armazenamento privado; não há
credenciais ou conteúdo do home do worker neste documento.

## Ajuste visual encontrado no aceite

O caminho completo da revisão imutável ultrapassava sua célula nos metadados da
sessão. O ajuste de CSS permite quebrar esse texto dentro da célula, preservando
o caminho completo. A verificação Playwright em larguras de 1.200 e 390 pixels
comprovou ausência de sobreposição e de rolagem horizontal.

## Limites mantidos

Pacotes têm limite de 512 KiB; sessões legadas sem baseline recebem erro
explicativo. Git, testes e publicação são ações do operador; a Web não recebe
credenciais Git nem socket Docker. A recuperação protege o checkpoint local e
não desfaz efeitos externos de ferramentas. A retenção da imagem não impede
remoção forçada por um administrador.
