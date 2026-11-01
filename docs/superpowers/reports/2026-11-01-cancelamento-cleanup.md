# Cancelamento durante cleanup — evidência de implementação

Base: `a2987ccbebe94bfed0049024ee0d4fdb067653b5`. Branch:
`fix/cancelamento-cleanup`. Escopo: os onze alertas `python:S7497`
examinados no [desenho aprovado](../specs/2026-11-01-cancelamento-cleanup-design.md).
Este relatório e os testes abaixo são versionados; o revisor não precisa
dos JSONs locais ignorados em `.superpowers/`.

## Resultado e limites

RPC e relay combinam uma barreira `run_persistent_cleanup` com um
`AsyncCleanupCoordinator` por fechamento. O coordinator serializa e
compartilha tentativas; a barreira retém o chamador cancelado até o término
da limpeza. A semântica de cancelamento imediato de um waiter isolado do
coordinator permanece coberta por `tests/test_async_cleanup.py`.

Web drena scheduler e serviço próprio antes de propagar cancelamento e
limpa o estado também quando há falha. Telegram drena a digitação, propaga
cancelamento do stream e impede o envio final nesses caminhos. Ownership
preserva a posse até terminar o dreno e a tentativa de liberação; conserva
`TurnLeaseLostError` e `GeneratorExit` já em propagação. Falhas adicionais
nesse caso geram evento `turn.cleanup_failed` com classe do erro, sem
mensagem sensível. Nas demais fronteiras, uma falha real de limpeza é
primária e conserva o cancelamento como causa explícita.

**Não há teto global de dreno.** Os limites locais existentes permanecem,
incluindo `IO_TIMEOUT` no relay. `CodexRpc.writer.wait_closed()` continua
sem timeout próprio. Um recurso que nunca conclui pode bloquear o
fechamento e seu chamador cancelado indefinidamente. Os testes controlam o
bloqueio e verificam tanto a retenção quanto a conclusão após liberá-lo;
não demonstram recuperação de um transporte permanentemente preso.

## Regressões observadas antes da correção

Os testes usam eventos e callbacks de conclusão para provocar a janela
entre o término do filho cancelado e a retomada de quem o aguarda. Não
consultam provedores reais nem fazem envios Telegram.

| Etapa | RED válido | GREEN focado após a correção |
| --- | --- | --- |
| RPC | 4 falhas: cancelamento tardio, dreno bloqueado, precedência/retry e writer após falha do leitor | 20 testes, incluindo helpers |
| Relay | 4 falhas e 1 controle positivo: runner, requisição, dreno concorrente e retry | 56 testes, incluindo helpers e RPC |
| Web | 4 falhas: scheduler e serviço próprio/injetado, com cancelamento repetido/falha | 105 testes e 4.793 subtestes |
| Telegram | 4 falhas: cancelamento direto, stream, fim da digitação e dreno repetido | 42 testes |
| Ownership e contratos preservados | 7 falhas e 7 controles positivos: heartbeat/deadline, sucessor, terminais e causa explícita | 40 testes, incluindo 2 controles adicionais de logging sanitizado |

Comandos executados: `uv run --frozen pytest -q` com os pares/grupos
`tests/test_async_cleanup.py tests/test_codex_rpc.py`, acrescidos de
`tests/test_docker_model_relay.py`; `tests/test_web_shutdown_cancellation.py
tests/test_web.py`; `tests/test_telegram_inbound.py`; e
`tests/test_interaction_turn_ownership.py tests/test_cancellation_contracts.py`.
São recortes sobrepostos, não totais somáveis. Ruff passou nos arquivos
alterados. O aceite integrado está registrado separadamente abaixo.

Asserções verificáveis no PR:

- `tests/test_codex_rpc.py`: fechamento repetido/concorrente compartilha
  uma tentativa; o chamador cancelado espera o writer; falha permite retry;
  falha do leitor não impede a tentativa de fechar o writer.
- `tests/test_docker_model_relay.py`: cancelamento no fim de runner e
  requisição; dreno compartilhado por `end_turn` e `aclose`; nova requisição
  não reutiliza a conclusão da anterior; admissão revogada antes do dreno.
- `tests/test_web_shutdown_cancellation.py`: lifespan real da aplicação e
  scheduler controlado; estado limpo e eventos persistidos; serviço
  injetado preservado; cancelamento e falha do serviço continuam explícitos.
- `TelegramCancellationTests` em `tests/test_telegram_inbound.py`: tarefa
  de digitação diretamente cancelada, cancelamento durante o stream e
  no fim da digitação, nenhum envio final e dreno sob cancelamento repetido.
- `tests/test_cancellation_contracts.py`: lease SQLite real, sucessor
  bloqueado até liberação durável, terminais preservados, mensagem de erro
  ausente do log e expiração natural após tentativas de release esgotadas.

Durante a execução, um controle do relay usava payload inválido e foi
interrompido; o RED contabilizado é o da repetição com payload válido.
Uma primeira validação Telegram detectou a remoção indevida de um import
ainda usado; ele foi restaurado antes do GREEN. Esses resultados falhos
não foram tratados como evidência de aprovação.

## Seis candidatos a falso positivo

As classificações abaixo dizem respeito ao bloco especificamente apontado,
não à segurança de toda a função. Nenhum dos onze S7497 foi reclassificado
remotamente neste lote.

| Chave do alerta | Tratamento preservado e prova versionada |
| --- | --- |
| `AaE_nNnWA4HaqsrPZgCy` | `_local_io` espera a thread e relança o cancelamento original. `test_catalog_local_io_waits_for_real_write_before_cancellation` faz escrita real em arquivo temporário, sob cancelamento repetido. |
| `AaE_nNoQA4HaqsrPZgDG` | `SQLiteAsyncInteractionPersistence._call` termina o worker e relança o cancelamento. `test_persistence_call_finishes_sqlite_write_before_cancellation` confirma INSERT real após o dreno. |
| `AaE_nNT4A4HaqsrPZf-f` | `run_persistent_cleanup` transporta o cancelamento em `PersistentCleanupOutcome`, sem reportar sucesso implícito. `test_persistent_cleanup_returns_external_cancellation_after_drain` verifica retenção e sinal; regressões de RPC/Web/relay verificam seu consumo na fronteira. |
| `AaE_nNRWA4HaqsrPZf93` | `_finish_stderr` consome cancelamento esperado do filho. `test_supervisor_consumes_only_the_expected_stderr_child_cancel` verifica esse resultado; `test_aclose_reaps_child_even_when_cleanup_caller_is_cancelled` em `tests/test_codex_supervisor.py` exercita o fechamento público com subprocesso controlado. |
| `AaE_nNnNA4HaqsrPZgCv` | O `except` de `acquire` relança o sinal ou traduz perda de posse. `test_acquire_propagates_body_cancellation` e os testes existentes de ownership preservam o contrato. O defeito adjacente no `finally` é coberto por `test_acquire_preserves_cancel_after_heartbeat_completion` e foi corrigido. |
| `AaE_nNnNA4HaqsrPZgCx` | O `except` de `_refresh_before_deadline` drena o filho e relança o cancelamento original. `test_refresh_reraises_original_cancellation_after_stopping_refresh` verifica o contrato. O defeito adjacente no `finally` é coberto por `test_refresh_preserves_cancel_when_deadline_finishes` e foi corrigido. |

Os demais cinco alertas correspondem às correções diretas de RPC
(`AaE_nNR0A4HaqsrPZf98`), relay (`AaE_nNQJA4HaqsrPZf9K`,
`AaE_nNQJA4HaqsrPZf9L`), Web (`AaE_nNMzA4HaqsrPZf75`) e
digitação Telegram (`AaE_nNsDA4HaqsrPZgDs`). Não foram adicionadas exclusões
de regra, suppressions ou alterações de perfil Sonar.

## Revisão e aceite integrado

A revisão independente do **desenho** foi estática e não executou testes;
é preliminar, não validação da implementação.

Primeira fonte validada: `44d2a70226e59dfc49bdedc590bae65d69bf9b46`.
Os resultados desta seção são históricos e vinculados a essa fonte;
o ajuste posterior solicitado pelo Sonar está discriminado ao final.
[Registro estruturado do aceite local, revisão e ajuste Sonar](2026-11-01-cancelamento-cleanup.json).

`scripts/ci.sh` completo terminou com **exit 0**:

| Verificação | Resultado |
| --- | --- |
| Suíte Python com cobertura | 4.345 testes e 6.571 subtestes; 21 skips e 34 deselecionados |
| Imagem real | 33 testes e 26 subtestes |
| Sessões Docker offline, com opt-in externo | 26 testes; 1 skip do opt-in separado de AppArmor/seccomp do host |
| Recall | 20 testes |
| Frontends, Vitest + TypeScript | TUI: 17; Web: 204; desktop: 18; três verificações TypeScript aprovadas |
| Lint e lock | Ruff, formatação, shellcheck, hadolint e `uv lock --check` aprovados |
| Builds | Aplicação, broker, stub e worker aprovados |

Os 33 testes de imagem deselecionados na suíte unitária foram executados
na etapa própria. `runtime_live` permaneceu deselecionado. Os opt-ins Docker
da suíte unitária foram exercitados na etapa offline; o opt-in separado
de sandbox interna do host não foi solicitado neste lote.

A combinação de cobertura padrão avisou sobre **nove fragmentos inválidos**,
incluindo dados anteriores à execução e fragmentos de subprocessos
interrompidos. O XML agregado local não fundamenta uma métrica quantitativa
neste aceite e não foi exportado. Para resolver a lacuna nos módulos
alterados, o Root repetiu o recorte dos nove arquivos acima com um prefixo
de cobertura novo e sem instrumentação automática dos subprocessos:
**257 testes e 4.793 subtestes**, exit 0, XML gerado sem warnings. O recorte
cobriu 1.767 de 2.174 linhas dos seis módulos completos (81,28%); isso não
é cobertura da aplicação inteira nem métrica de linhas novas do Sonar.

A revisão independente final foi realizada por **GPT-6 Astra/high**,
somente leitura, no range `a2987cc..44d2a70`. O revisor executou os nove
arquivos do recorte, incluindo `tests/test_codex_supervisor.py`: **257 testes
e 4.793 subtestes**, exit 0, em 24,14s. Não encontrou achados Critical,
Important ou Minor. As tentativas no sandbox restrito ficaram retidas no
recorte RPC e foram interrompidas; o teste isolado e o recorte completo
passaram fora dessa restrição. O revisor não executou Docker nem CI completo;
essas verificações foram realizadas pelo Root, como discriminado acima.

A imagem `sha256:a87976fb8b589115326d0781d9c24744e12d8cab406144b54ab394d22a4a3411`
foi inspecionada como UID 10000, sem rede, capabilities ou código do host
montado e com filesystem somente leitura. Os seis módulos alterados foram
importados e seus hashes coincidem com a fonte validada. O Codex 0.154.0
no host e nessa imagem tem SHA-256
`3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022`;
o tarball permanece pinado pelo Dockerfile em
`fc6e3e3b85f2cf7d664520ee5c66a7fe4aa12bae7d46834f47e2f165fd0d6f78`.

Decisões de escopo após a revisão, com seus custos:

1. Conservar o dreno sem teto global: um recurso permanentemente preso pode
   bloquear o chamador indefinidamente.
2. Manter fora do lote a auditoria geral de cancelamento repetido antes
   de estabelecer posse em `_acquire_durable`/`_offload`: nenhum defeito novo
   foi reproduzido; este aceite não cobre integralmente essa fase preexistente.
3. Manter fora do diff a sanitização ampla dos `logger.exception` antigos
   de ownership: os logs novos foram testados; este aceite não certifica
   a sanitização de todas as mensagens preexistentes.

Não houve achado menor adiado. A aprovação da implementação é condicionada
à conferência de CI/Sonar do SHA publicado; o resultado remoto deve ser
consultado nos checks e na descrição atualizada do PR. Não exportar este
XML local nem reclassificar os onze S7497 integra esta entrega.
Merge e deploy desta nova branch aguardam decisão sobre o PR concreto.

## Ajuste após a primeira análise do PR

O [CI remoto inicial](https://github.com/Patricia7sp/kairos/actions/runs/41339664579)
passou no SHA `43c27b26ed54bb660ffc024a8c5a75cfe382297e`, incluindo a
análise Sonar efetivamente executada. O **quality gate ficou ERROR**, com
um S5707, sete S5754, um S3776 e um S5778 nos trechos novos. A análise não
trouxe novos S7497; cobertura nova foi 80,6% e duplicação nova 0%. O log do
scanner confirmou o SHA acima. O job unitário remoto passou em 4.342 testes
e 6.571 subtestes, com 24 skips e 34 deselecionados; a combinação remota
também avisou sobre seis fragmentos de cobertura inválidos.

O ajuste mantém o contrato aprovado: o tipo da causa fica explícito nos
raises; `complete_cleanup_steps` reaproveita o carrier existente para
drenar todas as etapas antes de propagar a primeira falha; RPC e relay
separam a espera das tarefas e o fechamento do writer. Isso elimina as
sete capturas amplas novas e reduz a complexidade do RPC. O teste de
ownership prepara a tarefa antes do bloco que verifica sua exceção.

Seis casos adicionais verificam a primeira falha após todas as etapas
(RuntimeError, CancelledError, GeneratorExit, SystemExit e KeyboardInterrupt)
e cancelamento tardio/repetido antes da última escrita real. Um controle
por mutação temporária, que substituiu o carrier por espera direta, falhou
nos seis casos; a barreira foi restaurada. Esse RED é do controle por
mutação, não da main. O recorte completo restaurado passou em **263 testes
e 4.793 subtestes**, e Ruff/formatação passaram. O JSON versionado vincula
esse recorte aos hashes dos cinco arquivos alterados.

A revisão complementar e a repetição do CI completo/Sonar sobre essa fonte
posterior são necessárias; os resultados finais são registrados na
descrição e nos checks do [PR #127](https://github.com/Patricia7sp/kairos/pull/127).
Os resultados históricos acima não substituem essa repetição. Nenhuma
classificação remota foi alterada.
