# Aceite da interface HTTP do runtime

Atualização posterior: [aceite do repositório real](2026-09-10-real-project-acceptance.md),
com healthcheck da imagem do worker, correção da reconexão WebSocket e validação
de leitura, escrita isolada e retomada pelo Chromium.

Concluído em 2026-09-10, com evidência local `status=accepted`. A correção do
envio em HTTP foi implantada pelo stack Komodo e validada no Chromium pelo
endereço Tailscale `http://100.87.25.101:9119`.

## Código, imagens e testes

- PR integrado: [#16](https://github.com/Patricia7sp/kairos/pull/16).
- Commit implantado: `828379f5b46bdb077757720bc027680d0c3334ad`.
- [CI do PR](https://github.com/Patricia7sp/kairos/actions/runs/34387382455):
  nove checks aprovados.
- [CI do merge](https://github.com/Patricia7sp/kairos/actions/runs/34477819077):
  concluída com sucesso.
- Validação local: 59 testes Web, TypeScript e `git diff --check` aprovados.
- Worker reconstruído: 23 testes Docker aprovados, incluindo isolamento,
  execução de ferramentas, checkpoints e retomada com modelo simulado.

| Componente | ID conferido no daemon de produção |
| --- | --- |
| Aplicação | `sha256:502e992d49a99edf60c309eb799cc8b08c979fe3157be567863d8ed8ed546a37` |
| Broker | `sha256:edd59a03dfa8f76a0aa94ea393616d6f68bee08fc244753911c893b38a898fd4` |
| Worker | `sha256:f482b46cf7edafc620e770d3d5cdd020a89e9e380b0cf39129522b2eefadcc53` |

A imagem da aplicação foi reconstruída porque o artefato da sessão anterior
não estava disponível. Após sua implantação, o primeiro aceite visual encontrou
outra ausência: o worker configurado pelo ID `9468f72…` não existia no daemon.
A imagem foi reconstruída do código integrado, testada e fixada pelo novo ID
em `agent_runtime.docker_image`. O broker foi reiniciado sem turnos ativos.
A causa da remoção das imagens não foi determinada nesta etapa.

## Backup e restauração

Backup privado anterior à implantação:
`/home/paty7sp/.local/share/kairos-production-backups/20260910T124057Z`.

- SHA-256 do archive:
  `1d2b7e224e419e1f2c92d75ea7e9cf41a594c1ee64ddf2f2281958aa987f38e2`.
- Ambos os escritores foram parados durante a cópia e reiniciados saudáveis.
- Restauração em volume separado: 3.727 arquivos comparados; bancos íntegros;
  três sessões, 14 mensagens, dois turnos e dois checkpoints preservados.
- Todos os checkpoints foram abertos pelo registry como UID `10000`, incluindo
  os marcadores `PRODUCAO_OK` e `PERSISTENCIA_OK` do aceite anterior.
- A extração preservou os modos privados dos diretórios após `tarfile.data_filter`;
  oito links temporários regeneráveis do Codex foram omitidos somente na cópia.
- Configuração anterior à troca do worker e configuração do stack preservadas
  no mesmo diretório privado. Imagem anterior mantida como
  `kairos:rollback-http-20260910` (`sha256:def0d6e40a761a874cf501ca6965137fa4976d4537ea7438cca39a7c8fdaf4f6`).

O backup precede o turno de aceite visual. Nenhum banco ou volume ativo foi
substituído por uma restauração.

## Aceite no navegador e estado final

O Chromium confirmou `isSecureContext=false`, ausência de `crypto.randomUUID`
e presença de `crypto.getRandomValues`. A interface criou a sessão
`f129cf0682bc455ca969a2a801d07d6a` no projeto sintético `/projects/current`, em
`read_only`, e enviou o turno `38aed5e8d4a844dcaf432004f8ee9fad` com HTTP 202.
O turno terminou `completed`; a interface apresentou `INTERFACE_OK`.

A navegação por Sessões → Abrir runtime e o recarregamento da página preservaram
o histórico, com zero erros JavaScript. A auditoria final confirmou o turno no
banco, seu checkpoint válido, os marcadores anteriores preservados, zero turnos
ativos e zero workers registrados. O projeto sintético original permaneceu
idêntico, contendo apenas `README.md`.

Aplicação e broker saudáveis, `/api/health` com `status=ok` e `ui_present=true`,
runtime `ready`, mesmo volume `kairos_kairos-data`, porta publicada somente no
Tailscale, aplicação sem socket Docker e projeto somente leitura no broker.

Evidências privadas: `http-deployment.json` no diretório do backup e
`synthetic.json`, `synthetic-completed.png`, `synthetic-reopened.png` em
`/home/paty7sp/.local/share/kairos-production-acceptance/20260910`.

Este aceite cobre o envio e a reabertura no projeto sintético. A configuração
continua autorizando somente `/projects/current`; não habilita o repositório
real do Kairos nem comprova recuperação de um turno interrompido. O healthcheck
atual não detecta imagem de worker removida; a conferência operacional está em
[Runtime em produção](../../runtime-production.md#imagens-e-configuração).
