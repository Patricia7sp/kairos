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
