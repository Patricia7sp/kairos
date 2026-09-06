# Aceite operacional do Agent Runtime

Base local: `9f2cf65` (`feat/agent-runtime-codex`). Registro iniciado em
2026-09-06T04:41:00Z. Todos os comandos foram executados na worktree isolada
`.worktrees/agent-runtime-codex`; nenhum deploy, push, merge, container ou
volume de produção foi alterado.

## TDD e smoke fake

- 2026-09-06T04:33Z — RED prescrito:
  `uv run pytest -q tests/test_runtime_e2e.py tests/test_container.py --deselect tests/test_container.py::RealImageTests`.
  O novo E2E aceitou o turno por HTTP e bloqueou esperando a aprovação que o
  fake persistente ainda não emitia; execução interrompida após 67,5 s:
  1 failed, 3 passed, 20 deselected. O teste foi então corrigido para impor
  deadline externo em todo receive WebSocket.
- 2026-09-06T04:35Z — RED de imagem/configuração selecionado antes das edições
  de produção: 6 failed, 4 passed. As falhas foram os arquivos/bundle do
  serviço runtime ausentes, pin/checksum/COPY do Codex ausentes e seção
  `agent_runtime` ausente da configuração semeada.
- 2026-09-06T04:38Z — GREEN E2E:
  `uv run pytest -q tests/test_runtime_e2e.py` — 1 passed in 3.06s.
- 2026-09-06T04:39Z — GREEN prescrito:
  `uv run pytest -q tests/test_runtime_e2e.py tests/test_container.py --deselect tests/test_container.py::RealImageTests`
  — 46 passed, 20 deselected, 40 subtests passed in 5.75s.
- 2026-09-06T04:40Z — conjunto focado com compose:
  49 passed, 20 deselected, 40 subtests passed in 4.71s.

O fake usa REST autenticado para criar/enviar, `/ws/runtime` para desconectar e
retomar por cursor, e o executável CLI real para aprovar. O estado e journal
SQLite atravessam restart ordenado do host e do filho. Um audit externo prova
`thread/start=1` e `turn/start=2`: reconnect e restart não reenviam; somente as
duas mensagens explícitas iniciam turnos. Receives, processos, subprocessos e
cleanup têm deadlines e kill de último recurso.

## Aceite real opt-in

2026-09-06T04:40Z —
`uv run pytest -q -m runtime_live tests/test_runtime_live.py` sem
`KAIROS_RUNTIME_LIVE=1`: 1 skipped in 0.17s.

Resultado: **não executado**. Não havia login/API key dedicada fornecida para
esta sessão. Nenhuma autenticação pessoal foi lida e nenhum turno pago foi
feito. Portanto este registro não declara aceite live completo. O teste opt-in
preparado usa dois diretórios temporários autorizados, confirma status/login e
logout, dois turnos na mesma thread atravessando restart real de host e Codex,
fila no mesmo projeto e execução entre projetos distintos.

## Container, pacote e CI local

### CI e distribuição Python

- 2026-09-06T04:42Z — `scripts/ci.sh --fast`: os testes passaram
  (1349 passed, 21 deselected e 5561 subtests em 35,91 s), assim como Ruff
  lint, shell checks, avaliações (20), lock e os frontends UI (17), Web (57) e
  Desktop (18). O comando agregado terminou com exit 1 porque
  `ruff format --check` encontrou somente três arquivos fora do formato:
  `kairos_state/connection.py`, `kairos_state/migrations.py` e
  `tests/test_runtime_storage.py`.
- Os três arquivos foram formatados mecanicamente. A segunda execução agregada
  repetiu e passou pytest, Ruff lint/format, shell, avaliações, UI e Web, mas foi
  interrompida durante o `tsc` Desktop (exit 130); não é registrada como uma
  execução agregada verde. Desktop e lock já tinham passado na primeira rodada
  e não foram alterados.
- 2026-09-06T04:46Z — checks individuais finais:
  `uv run ruff check .` — passed;
  `uv run ruff format --check .` — 263 files already formatted;
  `git diff --check` — sem erros.
- 2026-09-06T05:07Z — `uv build` final — exit 0 em cerca de 2 s; gerou
  `dist/kairos-0.1.0.tar.gz` e `dist/kairos-0.1.0-py3-none-any.whl`, incluindo
  `kairos_runtime` e a SPA atual. Uma tentativa anterior dessa repetição foi
  interrompida pela ferramenta sem saída e não foi contada como sucesso.

### Imagem e sandbox

- `docker build --check .` — exit 0, sem warnings.
- `docker build -t kairos:test .` — exit 0. O download oficial amd64 do Codex
  0.153.4 passou pelo SHA-256
  `a822187e1a2420c61c5926721bfbd878701ed95547c9bb0d4de4498a16ba1821`.
  Após a proteção fail-closed, a imagem foi reconstruída com manifest local
  `sha256:e7ec7a39ee112914f60a68755c9494763d406415ab9c18f22b610965b6091aa1`.
- Smoke sem autenticação dentro da imagem: `codex --version` retornou
  `codex-cli 0.153.4`; os recursos `bwrap`, zsh, rg e code-mode-host ficaram no
  pacote. `codex sandbox --help` passou, mas `codex sandbox /bin/true` falhou
  porque o `bwrap` não recebeu permissão para criar um namespace. Nenhuma
  capability, modo privilegiado, sysctl, socket Docker ou fallback amplo foi
  acrescentado.
- RED do admission probe: 4 failed, 1 passed. GREEN focado: 5 passed em 0,36 s.
  O covering de host, supervisor, segurança e E2E terminou com 36 passed em
  8,79 s. Ele cobre falha e timeout antes de ready, stderr público estático,
  terminate/kill/reap, sucesso e feature desabilitada sem executar probe.
- `uv run pytest -q tests/test_container.py::RealImageTests` — 21 passed e
  23 subtests passed em 42,14 s, sem skips. O teste real confirmou usuário
  `kairos`, pacote completo e estado `unavailable` quando a plataforma recusa o
  sandbox; o App Server não iniciou.

Resultado local: embalagem e comportamento fail-closed aceitos. O runtime não
ficou `ready` nesta plataforma Docker, portanto não há aceite operacional
completo do runtime em container. É necessário um ambiente que permita o
sandbox empacotado sem relaxar as proteções e, separadamente, credenciais
dedicadas para o aceite live.

Nenhum workflow remoto foi disparado. CI completa continua exigindo todos os
jobs obrigatórios do GitHub Actions; os resultados locais não substituem esse
gate. Nenhum deploy, push, merge, backup ou alteração em container/volume de
produção foi realizado.

## Correções após revisão local

Em 2026-09-06, a revisão do commit `05d16f4` encontrou três lacunas nos testes;
nenhuma exigiu mudança em código de produção ou rebuild da imagem.

- RED focado: 6 failed em 1,81 s. Os testes novos demonstraram que a expectativa
  da imagem era fixa, o processo auxiliar ignorava a saída antecipada de
  `serve_runtime`, `_start` não devolvia ownership limpo na falha e faltavam as
  asserções temporais do smoke live.
- GREEN focado: 6 passed em 1,84 s. O E2E offline completo mais os testes
  temporais terminaram com 6 passed e 1 live deselected em 4,29 s.
- O caso RealImage alterado passou em 3,61 s contra `kairos:test` existente. Ele
  agora invoca o probe de `CodexSupervisor` como UID 10000; o próprio probe usa
  o `CODEX_HOME` dedicado e sanitiza o ambiente antes de executar
  `codex sandbox /bin/true`. O teste compara `ready`/`unavailable` com esse
  retorno real. A falha determinística continua coberta pelo wrapper do teste
  de host.
- O smoke live agora espera `turn_start` e confirma o lease do primeiro turno
  antes de submeter outro turno no mesmo projeto e um em projeto distinto. Os
  timestamps duráveis exigem ausência de sobreposição no mesmo projeto e
  sobreposição entre projetos; testes offline rejeitam ambas as regressões.
- Ruff lint dos três arquivos passou. O format-check apontou uma expressão no
  helper live, corrigida mecanicamente; a checagem final passou. O smoke live
  continuou não executado, sem flag ou credencial dedicada.

## Onda final da revisão geral — I1–I5 e M1

2026-09-06, sobre `c708029`: corrigida a retomada da thread ociosa antes de
novo envio em outra geração; o fake exige carregamento por geração e confirma
`thread/start=1`, `thread/resume=1`, `turn/start=2` através do restart. Falhas
antes de dispatch conservam `not_sent`, não criam thread nem uncertain e
liberam os leases. Recuperação de turno ativo conserva `attach_turn` e a
barreira existente.

As superfícies agora conservam a aprovação rejeitada sem decisão efetiva,
permitem cancelar o ID real do turno queued, preservam instruções canônicas
online/reload/cancel e mesclam respostas por identidade. O CLI humano projeta
replacements, reconciliação, finais, ferramentas e estados com uma instância
por stream. O host registra um único diagnóstico allowlisted por falha de
startup, sem expor stdout/stderr ou credenciais; o contrato de status mantém
`unavailable`.

As regressões foram observadas RED antes das respectivas correções: E2E após
restart, journal real de aprovação/fila no reducer servido, instrução ausente
na view, resposta final ausente no CLI e diagnóstico não emitido no startup.
O relatório detalhado local está em
`.superpowers/sdd/2026-09-05-agent-runtime-codex-app-server/final-fix-report.md`.

Validação final local após as mudanças semânticas:

- Python completo: **1371 passed, 22 deselected, 5561 subtests passed**, 40,69 s,
  por `uv run pytest -q --deselect tests/test_container.py::RealImageTests`.
  A primeira execução completa teve apenas uma falha de prazo preexistente:
  `process.join(1)` excedido durante spawn/import do teste de falha antecipada.
  A espera foi limitada a 5 s, preservando a exigência de exit com erro; o foco
  passou e essa falha justificou a única repetição completa.
- Web: **58 passed**; TUI: **17 passed**; Desktop: **18 passed**.
  Os três `tsc --noEmit` passaram.
- Ruff lint e format, shellcheck, `uv lock --check` e `git diff --check`:
  exit 0. Nenhum comando agregado `scripts/ci.sh --fast` é declarado verde.
- Controller: `uv build` final gerou wheel/sdist atuais, exit 0. O wheel teve
  service, host, CLI e SPA comparados byte a byte ao source congelado. Apenas
  o pacote foi repetido após a correção do teste para incluir o sdist atual.
- Controller: `docker build --check .`, build `kairos:test` e rebuild da
  fixture stub: exit 0. Imagem final
  `sha256:f92652d8d477deaa459bb437a4dc377846c4434c934bca9d69bc697df378723e`.
  `uv run pytest -q tests/test_container.py::RealImageTests`:
  **21 passed, 23 subtests passed**, 43,06 s, sem skips.

A limitação local de namespaces continua tratada com runtime indisponível.
Não houve relaxamento de privilégios, autenticação live, turno pago, push,
merge, CI remota ou deploy. M2 (helper privado de cleanup) permanece diferida
pela decisão de mínimo impacto. O aceite operacional live e remoto segue
pendente, separado destes resultados locais.

## Resultado da revisão final

A revisão geral e a revisão restrita da correção funcional `771e9fe` encerraram
os cinco achados obrigatórios e o diagnóstico de startup, sem nova falha
Important/Critical. A implementação local está concluída e permanece na branch
`feat/agent-runtime-codex`, em worktree preservada. Os artefatos foram verificados
contra os arquivos funcionais desse commit.

Duas pendências menores não bloqueiam a entrega local:

- A instrução canônica pode aparecer novamente quando o App Server entrega um
  `userMessage` dentro de um evento `tool`; falta deduplicar também essa forma
  na exibição do transcript.
- O supervisor ainda importa o helper genérico privado de cleanup do pacote de
  providers; sua extração para infraestrutura neutra fica para mudança própria.

O [registro de decisões](agent-runtime-decisions.md) preserva as justificativas
e os custos de revisão assumidos durante a implementação. O aceite operacional
continua separado: login/turnos reais dedicados, CI remota e deploy não foram
executados. O runtime permanece desabilitado por padrão e falha de forma
controlada quando a plataforma bloqueia o namespace da sandbox.

## Retomada e publicação do PR — 2026-09-06

A branch foi publicada e o [PR #12](https://github.com/Patricia7sp/kairos/pull/12)
foi aberto em rascunho contra `main`. A worktree foi preservada. Não houve
merge, deploy, ativação do runtime ou autenticação live.

Validação repetida sobre `d8387d6`:

- `scripts/ci.sh --fast`: exit 0; 1371 passed, 22 deselected e 5561 subtests,
  93 testes frontend e os três typechecks, Ruff lint/format, shellcheck,
  recall e lockfile aprovados. A execução inicial dentro da sandbox da sessão
  parou de avançar e foi encerrada; a execução completa fora dela passou.
- `RealImageTests`: 21 passed e 23 subtests, sem skips, em 48,90 s contra a
  imagem final registrada acima.
- Probe descartável sem rede, como UID 10000: `bwrap` ainda recusa criação de
  namespace. O container usa `docker-default (enforce)` e seccomp modo 2;
  o host informa `unprivileged_userns_clone=1`. Esses dados não isolam qual
  restrição recusa a chamada. Nenhuma política foi relaxada.

A [primeira CI remota](https://github.com/Patricia7sp/kairos/actions/runs/34061224262)
passou em sete dos oito jobs, incluindo build e integração da imagem. O
hadolint identificou DL4006 no pipeline usado para verificar o checksum do
Codex. O aviso foi reproduzido localmente antes da correção. O Dockerfile
agora grava o checksum em arquivo temporário, verifica com `sha256sum` e
remove o arquivo na mesma camada; todas as etapas continuam encadeadas por
`&&`, sem pipeline ou supressão de lint. Hadolint, `docker build --check` e os
46 testes de Dockerfile/configuração passaram após a mudança. O resultado
da CI sobre o commit corrigido deve ser consultado nos checks do PR.

O rebuild local corrigido terminou com exit 0, imagem
`sha256:8b29fa85795b9f62ff19f04ce56c91ea6cf10e40ed870466142990192c6c5b51`.
Os testes contra essa imagem passaram: 21 passed e 23 subtests, sem skips,
em 43,76 s. `git diff --check` também passou.
