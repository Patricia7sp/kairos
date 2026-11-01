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

Pendente nesta revisão do relatório: revisão independente da branch com
execução focada, `scripts/ci.sh` completo com Docker e Codex 0.154.0
verificado por SHA-256, e CI/Sonar do SHA efetivamente publicado no PR.
`runtime_live` e exportação Sonar local permanecem desabilitados.
Merge e deploy desta nova branch aguardam decisão sobre a entrega concreta.
