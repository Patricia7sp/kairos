# Aceite local — sessões Docker com ChatGPT

Data: 2026-09-07. Branch: `feat/runtime-external-sandbox`.
Implementação validada até `68e4c14`. Desenho e implementação concluídos
localmente, com revisão independente e correções incorporadas.

## Resultado observado

O login dedicado no ChatGPT foi concluído. Um turno enviado pelo RuntimeClient
ao broker, executado em worker Docker isolado e atendido pelo modelo `gpt-5.5`,
terminou em `completed` com a resposta `KAIROS_OK`.

- Sessão: `chatgpt-acceptance-20260907-verified`.
- Turno: `7153dbf8ba6140488892e72f5de72e81`.
- Broker de teste: `/home/paty7sp/.local/share/kairos-docker-lab`.
- Único projeto autorizado: `/home/paty7sp/projetos/kairos-runtime-lab`.
- Estado final consultado pelo CLI: `ready`.
- Nenhum container `kairos-worker-*` restante após a validação.

As credenciais ficam somente no perfil privado do broker. O worker recebe
workspace e estado da sessão, sem credenciais, mounts do host ou rede externa.
A imagem validada `kairos:external-sandbox` tem ID
`sha256:10f1ff442a757a8266899db1220c4432962ed2e6d8a21ddaee7011c7e4f44074`.
O Docker usa seccomp e AppArmor padrão, sem depender dos perfis adicionais
experimentados anteriormente no host.

## Evidências de validação

| Verificação | Resultado |
| --- | --- |
| Regressão Python final, excluindo `RealImageTests` | 1.545 passaram, 12 ignorados, 22 desmarcados; 5.561 subtestes; 39,98 s |
| Grupo de integração Docker externa, SessionWorker e sessões | 23 passaram; inclui execução real de ferramenta com modelo simulado e restauração após reinício |
| Testes de autenticação e transporte após a última correção | 13 passaram |
| Frontend no dev container existente | 93 passaram: 58 Web, 17 TUI, 18 desktop; três typechecks aprovados |
| Pacotes Python | Wheel e sdist gerados por `uv build` |
| Conta e modelo reais pelo RuntimeClient | `completed`, resposta `KAIROS_OK` |

A regressão Python final foi executada com
`timeout -k 5s 150s uv run pytest -q --deselect tests/test_container.py::RealImageTests`.
Seu registro local está em `/tmp/kairos-docker-sessions-final-tests.log`; o build
está em `/tmp/kairos-docker-sessions-final-build.log`. Esses logs são temporários.
Os testes reais de Docker usam `KAIROS_EXTERNAL_SANDBOX_TEST=1` e os arquivos
`test_external_sandbox_integration.py`, `test_docker_session_worker.py` e
`test_docker_sessions_integration.py`.

O frontend não foi alterado nesta etapa; sua verificação usou a imagem de
desenvolvimento já existente. Isso não comprova um rebuild dessa imagem com
o novo backend. Os 22 testes de imagem desmarcados não integram a contagem de
aprovações desta regressão; a imagem do worker teve validação Docker separada.
Testes de IPC/SQLite assíncronos e Docker foram executados fora da sandbox da
ferramenta Codex, que restringe operações necessárias neste ambiente.

## Correções relevantes ao aceite

- A criação materializa a thread antes de salvar o primeiro checkpoint, usando
  `historyMode: legacy` e `thread/name/set` do Codex 0.153.4. A retomada funciona
  em outro worker, inclusive antes do primeiro turno.
- O registro distingue criação pendente de container confirmado. Recuperação
  incerta bloqueia novas tarefas; ausência durante criação pendente não prova
  que o daemon deixou de criar o container.
- Checkpoints exigem encerramento confirmado do Codex e suspensão dos processos
  restantes antes das duas exportações. O terminal só é publicado depois da
  gravação atômica e remoção do worker.
- O relay valida também `additional_tools` e `tool_search_output`. Busca de
  ferramentas é permitida apenas com execução local declarada como `client`.
- O transporte preserva pequenos deltas SSE. O endpoint real retornou HTTP 200
  sem `Content-Type`; nesse caso, um prefixo SSE limitado precisa ser validado
  antes do encaminhamento. HTML e tipo de conteúdo explicitamente incompatível
  continuam recusados.

## Limites e integração pendente

O backend continua opt-in e recusa `broad_access`. O broker precisa de acesso
confiável ao Docker; o dev container organiza o desenvolvimento, e os workers
separados fornecem o isolamento de execução. Não há escrita automática de
alterações no projeto original nem repetição automática de turnos abandonados.

O modelo validado é `gpt-5.5`, com Codex 0.153.4. Ferramentas hospedadas,
namespaces de ferramentas e contas com roteamento FedRAMP não estão habilitados
neste transporte. Blobs antigos ou órfãos não têm coleta automática nem quota
global de disco; cada archive permanece limitado e validado.

CI remota, push, merge e deploy desta branch não foram realizados. O runtime
anterior já havia sido integrado em `main` (`46c4087`); este aceite não representa
merge da nova arquitetura Docker. A produção permaneceu saudável na imagem
`sha256:5d779e2b7d2d42d4d107cae68a92d676fc03a61d26254a60c13a0410e5d65f24`,
com o runtime desabilitado. As duas pendências menores do aceite anterior e os
alertas de dependências frontend já registrados não foram resolvidos nesta etapa.

Operação e exportação revisável: [guia do runtime Docker](../../docker-session-runtime.md).
