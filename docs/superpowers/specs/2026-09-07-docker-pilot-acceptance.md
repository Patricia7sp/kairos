# Integração e piloto do broker Docker

O [PR #13](https://github.com/Patricia7sp/kairos/pull/13) foi integrado em
2026-09-07, commit `748d65ea1f1247870f4290e7af7477b075fbadf1`.
Os nove jobs da [CI do PR](https://github.com/Patricia7sp/kairos/actions/runs/34167340633)
e da [CI de main](https://github.com/Patricia7sp/kairos/actions/runs/34167533778)
passaram. Isso inclui a imagem principal e o novo job de sessões Docker offline.
O [aceite anterior](2026-09-07-docker-sessions-acceptance.md) registra os testes locais
e o login real anteriores à integração.

## Piloto executado

Perfil: `/home/paty7sp/.local/share/kairos-docker-lab`. Único projeto autorizado:
`/home/paty7sp/projetos/kairos-runtime-lab`. Imagem fixada no ID
`sha256:10f1ff442a757a8266899db1220c4432962ed2e6d8a21ddaee7011c7e4f44074`.

A API Web autenticada foi exercitada pelo transporte ASGI em processo contra
o broker real pelo socket Unix; não foi um teste visual de navegador nem houve
exposição de porta. O modelo real usou uma ferramenta para criar um arquivo no
worker. Depois da parada e reinicialização do broker, a mesma thread foi retomada
em outro worker e leu o arquivo preservado. Os dois turnos terminaram em `completed`.

| Evidência | Valor |
| --- | --- |
| Sessão | `docker-pilot-web-cli-20260907` |
| Thread preservada | `01a07e07-8603-7a43-81b2-5c45e4aa8ca0` |
| Primeiro turno | `a2d2994e72504e66b019f40cb945f6b0` |
| Marcador no primeiro checkpoint | `PILOTO_OK` |
| Turno após reinício e correção operacional | `a13fca88a4544af8a65a09cb40d854fe` |
| Marcador no checkpoint retomado | `RETOMADA_OK` |

Os markers foram lidos dos archives validados, sem extrair no host. Os arquivos
gerados não existem no projeto original. A tentativa inicial de retomada sob
systemd falhou antes do envio ao modelo (`send_state=not_sent`); ela permanece no
journal e foi seguida por uma nova tentativa explícita, sem replay automático.

O CLI real criou a sessão `docker-pilot-cli-20260907` com sandbox `read_only`
e a encerrou com sucesso, sem chamadas ao modelo.

## Supervisão e acesso Docker

O serviço `kairos-docker-lab.service` e o timer `kairos-docker-lab-disk.timer`
foram instalados como unidades do usuário. O broker ficou `ready`, sem reinícios
automáticos, e o check de disco terminou com sucesso. O limite foi também
exercitado com um teto artificial de 1 KiB, produzindo a falha esperada.

O gerenciador systemd do usuário mantinha grupos anteriores à inclusão no grupo
Docker. Um diagnóstico offline a partir do backup retomava a thread no terminal,
mas o mesmo diagnóstico no serviço falhava em `docker image inspect` por falta
de permissão. Selecionar o grupo já autorizado via `sg docker` resolveu o acesso;
`setpriv --no-new-privs` é aplicado antes de executar o broker. Foram verificados
UID 1000, GID Docker 983 e `NoNewPrivs: 1` no processo real. Nenhuma permissão do
socket, associação de grupo ou perfil AppArmor foi alterado nesta etapa.

O host tem `Linger=no`; a unidade habilitada inicia com o gerenciador do usuário,
mas continuidade após logout e partida antes do login não estão garantidas.
O timer monitora espaço e bloqueia partidas acima dos limites; não é quota rígida
nem coleta automática de blobs. Não há alerta externo configurado.

## Backup validado

Com broker parado, socket ausente e lock exclusivo adquirido, foi criada uma
cópia privada em
`/home/paty7sp/.local/share/kairos-docker-backups/20260907T224128Z`.
O backup inclui configuração, banco de sessões e registry/blobs. Os dois bancos
passaram em `PRAGMA integrity_check`, e os archives de três sessões foram lidos e
validados. Uma cópia desse backup também foi usada para retomar a thread em um
worker real com transporte de modelo bloqueado. Credenciais não foram copiadas.

Esse backup antecede o turno `RETOMADA_OK` e a fixação da imagem no config: ele
restaura o ponto anterior, não o estado posterior do piloto. É uma cópia local,
sem agendamento automático ou replicação para outra máquina.

## Escopo da liberação

O piloto comprova API Web, modelo/ferramenta real, persistência, retomada e
isolamento do projeto original. A configuração do backend em produção continua
desabilitada, e o container `kairos` permaneceu saudável na imagem
`sha256:5d779e2b7d2d42d4d107cae68a92d676fc03a61d26254a60c13a0410e5d65f24`.
Não houve deploy da aplicação de produção nem ampliação da allowlist para projetos
de trabalho. A adoção gradual começa pelo perfil piloto já configurado.

A revisão automática do GitHub chegou após o merge com dois achados sobre ordem
de cancelamento e retenção de snapshots. As correções e seus testes ficam no PR
complementar, junto da correção de seleção do grupo Docker do serviço.
O cancelamento interrompe o Codex antes de revogar o relay e remove o worker se
a interrupção falhar. O cache tem limite de 32 observações e é limpo no encerramento;
consultas de turnos removidos do cache recorrem ao histórico durável, sem redispatch.

O [PR #14](https://github.com/Patricia7sp/kairos/pull/14) reúne essas correções.
Observações sem ID ou não terminais não entram no cache: uma consulta a histórico
ausente não pode ocultar o último turno em `inspect(None)` ou `reconcile`.

A compatibilidade de parada foi verificada com um processo pai sintético que
não repassa SIGINT, reproduzindo a variante shadow-utils de `sg`. O launcher
notifica o PID do broker e o `ExecStop` aguarda sua saída explicitamente.
O teste confirmou entrega de SIGINT e preservação de uma limpeza com atraso,
terminando em 1,069 s. A unidade real `Type=notify` também reiniciou e ficou
`ready`, sem reinícios automáticos. A escolha do PID segue o contrato de
[systemd-notify](https://github.com/systemd/systemd/blob/main/man/systemd-notify.xml).
