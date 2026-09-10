# Aceite do runtime Docker em produção

Atualização posterior: [aceite da interface HTTP em 2026-09-10](2026-09-10-runtime-http-acceptance.md),
com novas imagens da aplicação e do worker, backup restaurado e validação no Chromium.

Concluído em 2026-09-09T16:26:11Z, com status `accepted`. O deploy já estava
saudável ao retomar o trabalho; esta etapa executou o aceite real pendente,
reiniciou o broker e confirmou a persistência na mesma sessão.

## Versão e CI

- PR integrado: [#15](https://github.com/Patricia7sp/kairos/pull/15).
- Commit implantado: `11b89a0a8ee531d5eea657f2386bbe764c845e64`.
- [CI do PR](https://github.com/Patricia7sp/kairos/actions/runs/34362309029):
  nove checks concluídos com sucesso.
- [CI do commit integrado](https://github.com/Patricia7sp/kairos/actions/runs/34362635893):
  concluída com sucesso. Ambos os resultados foram consultados no GitHub nesta etapa.

Imagens validadas antes do deploy:

| Componente | ID imutável |
| --- | --- |
| Aplicação | `sha256:def0d6e40a761a874cf501ca6965137fa4976d4537ea7438cca39a7c8fdaf4f6` |
| Broker | `sha256:edd59a03dfa8f76a0aa94ea393616d6f68bee08fc244753911c893b38a898fd4` |
| Worker | `sha256:9468f72aa4e850c71fbb3fbd0615c29946a66ae4822c4b6b8200dff23c402c7b` |

A checagem final confirmou que os containers da aplicação e do broker usam os
IDs registrados. O smoke anterior com volume temporário também passou.

## Aceite real e reinício

Sessão: `production-runtime-acceptance-20260909`.
Thread preservada: `01a086f4-905f-7d12-98d1-5f4c0276e909`.

1. A API Web autenticada confirmou runtime `ready`, projeto autorizado
   `/projects/current` e conta ChatGPT dedicada autenticada.
2. O turno `aad4fdfb1e9b420fb28d22814386a385` terminou `completed` e criou
   `/workspace/production-marker.txt`. O archive do checkpoint foi aberto e o
   conteúdo `PRODUCAO_OK` foi confirmado.
3. Uma consulta ao banco confirmou zero turnos ativos antes do reinício.
   O broker foi reiniciado com `SIGINT`, voltou saudável e seu `StartedAt`
   mudou de `2026-09-09T14:36:09.540455574Z` para
   `2026-09-09T16:25:51.102145856Z`.
4. A retomada confirmou a mesma thread. O turno
   `d4a88bcc6afb4ff9bad7ebc3af2ac1ee` leu o marcador anterior e criou
   `/workspace/production-resumed.txt`. Terminou `completed`; o novo archive
   confirmou o conteúdo `PERSISTENCIA_OK`.

## Saúde, isolamento e dados

- Aplicação e broker saudáveis; CLI com `enabled=true` e `state=ready`.
- `/api/health` respondeu `status=ok` e `ui_present=true` pelo endereço Tailscale.
- Única publicação de porta da aplicação: `100.87.25.101:9119`; broker sem portas publicadas.
- Aplicação sem socket Docker; broker com socket Docker, raiz somente leitura
  e usuário `10000:10000`.
- Mesmo volume `kairos_kairos-data` nos dois containers.
- Projeto montado somente leitura no broker. O diretório sintético original
  continua contendo apenas `README.md`, idêntico ao arquivo do piloto.

## Backup e evidências locais

Diretório privado do backup:
`/home/paty7sp/.local/share/kairos-production-backups/20260909T143552Z`.

O deploy anterior registrou restauração em volume separado com 1.017 arquivos
comparados, SQLite íntegro e contagens preservadas de uma sessão e dez mensagens.
Quatro links temporários regeneráveis do Codex foram omitidos apenas na cópia
restaurada; o archive original foi preservado integralmente.

Nesta etapa, o SHA-256 do archive foi recalculado e coincidiu com o registro:
`eb6e0a47b49b01823fbadded6815d7d1f1e3ae70af6f09122730444b3663af6d`.

O diretório privado contém `deployment.json` com status `accepted`,
`live-create.json` e `broker-restart.json`. A evidência final da retomada
também persiste no volume em `/opt/data/runtime-production-acceptance.json`.
A imagem anterior foi registrada como
`sha256:5d779e2b7d2d42d4d107cae68a92d676fc03a61d26254a60c13a0410e5d65f24`,
com tag `kairos:rollback-20260909t143552z`.

## Limites deste aceite

O runtime está habilitado somente para o projeto sintético `/projects/current`.
As escritas pertencem à cópia isolada e aos checkpoints. Este aceite cobre
dois turnos reais via API Web e retomada após reinício ordenado sem turno ativo;
não comprova recuperação de um turno interrompido, teste de carga ou interação
visual no navegador. O backup validado precede estes dois turnos.

O procedimento de reversão permanece em
[Runtime em produção](../../runtime-production.md#reversão-sem-perda-de-dados).
Nenhum volume ou banco ativo foi substituído durante este aceite.
