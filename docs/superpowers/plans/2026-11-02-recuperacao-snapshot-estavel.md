# Recuperação por snapshot — plano e aceite da #129

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estabilizar a regressão de recuperação com snapshot terminal, preservando reconciliação, fencing, liberação da lease e ausência de reenvio.

**Architecture:** O teste exercita `AgentRuntimeService`, SQLite e o assinante reais. Uma barreira libera o snapshot depois que o assinante alcança o fim do journal; atraso controlado na fronteira de persistência cobre agendamento lento. Um watchdog de travamento cobre recuperação, consumo e dreno, sem estabelecer SLA de recuperação.

**Tech Stack:** Python 3.11, asyncio, pytest, SQLite; dependências existentes.

**Spec:** [Issue #129](https://github.com/Patricia7sp/kairos/issues/129). Execução autorizada pelo usuário após o plano de investigação, RED→GREEN, CI completo e PR para revisão antes de merge/deploy.

## Restrições e aceite

- Reproduzir a expiração de 0,3 s mantendo I/O e recuperação reais; registrar o resultado saudável após esse prazo.
- O runtime entrega `text` e `snapshot`, sem notificação terminal `turn`; o serviço deve publicar o terminal reconciliado.
- Conferir avanço da geração da lease, confirmação de inatividade, sessão pronta, journal e nenhum `start_turn` no runtime recuperado.
- Testes commitados e evidência sanitizada neste documento viajam no PR; `.superpowers/` contém apenas logs auxiliares.
- Rodar `scripts/ci.sh` completo com Docker e Codex 0.154.0 fixado, CI remoto por SHA e revisão independente que execute testes.

## Foco de revisão

- O assinante já drenou o replay antes da entrega do snapshot: a barreira deve observar a leitura real vazia, sem fabricar eventos.
- Persistência lenta: o caso deve executar todas as operações SQLite e aguardar o dreno, preservando cancelamento e falhas inesperadas.
- Falta de conclusão: o watchdog deve continuar falhando se o serviço deixar de consumir o snapshot ou publicar o terminal.

### Task 1: Regressão de recuperação sob agendamento controlado

**Files:** Modify `tests/test_runtime_recovery.py`; Create este plano com relatório de validação.

**Interfaces:** Consome `RuntimeStore._call(operation)`, `events_after(session_id, cursor)`, `AgentRuntimeService.recover()/subscribe()` e o `FakeRuntime` existente. Produz somente testes e evidência; API de produto preservada.

- [x] Acrescentar barreira de leitura real vazia e parametrizar persistência imediata/30 ms por chamada no teste `test_active_inspection_then_terminal_attachment_snapshot_finalizes_without_notification`.
- [x] Executar o caso lento com o watchdog original de 0,3 s e observar `TimeoutError` (RED).
- [x] Usar 3 s como limite de travamento, abrangendo `recover`, assinatura e dreno, e verificar os dois casos (GREEN).
- [ ] Executar os testes de serviço/recuperação, Ruff e a validação completa; revisar independentemente a branch.
- [ ] Commit `test(runtime): estabiliza recuperação por snapshot terminal` e PR com evidência por SHA, sem merge/deploy nesta etapa.

## Investigação confirmada

Base: `4fe697bb9e05e5cc1c3cce7f4d9476cd851f42a2`. Linha de base: 50 testes passaram em 15,03 s.

Uma medição adicionou espera antes de cada chamada real ao SQLite após `recover()`. O assinante e a tarefa de recuperação continuaram ativos após o watchdog diagnóstico de 0,3 s, permitindo conferir o resultado em vez de cancelá-lo. Os quatro casos terminaram com estado `completed`, conteúdo reconciliado, inatividade confirmada, sessão `ready`, sequência final `text/reconciled/turn_end` e zero reenvios.

| Atraso por chamada | Terminal recebido | Lease liberada | Watchdog de 0,3 s |
| --- | --- | --- | --- |
| 0 ms | 0,094 s | 0,096 s | passou |
| 10 ms | 0,326 s | 0,327 s | expirou |
| 20 ms | 0,276 s | 0,298 s | passou |
| 30 ms | 0,357 s | 0,394 s | expirou |

Os tempos são amostras diagnósticas, não limiares de produto. O caso de 10 ms superar o de 20 ms mostra que o tempo total não varia monotonicamente com a espera injetada; a medição inclui I/O, polling e agendamento. Isso comprova que o prazo original pode rejeitar uma recuperação correta; não determina retroativamente a latência exata do runner da falha original. Nenhum defeito funcional do serviço foi demonstrado nesta investigação.

## Validação da implementação

RED com o watchdog original: `delayed-store` falhou por `TimeoutError`; `immediate` passou (1 failed, 1 passed em 2,08 s). GREEN com o limite de travamento e a barreira: 51 testes de serviço/recuperação passaram em 16,79 s; Ruff check/format e `git diff --check` passaram.

O fechamento exige revisão independente e `scripts/ci.sh` completo, além de CI remoto por SHA. Os resultados e links finais serão registrados no corpo do PR, permitindo distinguir evidência executada de etapas ainda pendentes.
