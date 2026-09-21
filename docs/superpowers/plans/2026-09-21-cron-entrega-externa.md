# Entrega externa do cron por canal — harness local offline

Fecha o ponto declarado "entrega a canais" da entrega externa do cron com
**validação offline do caminho completo de produção** — aquele que, em
produção, atravessa dois processos: o dashboard roda o `Scheduler`
(`kairos_web/server.py`) e o serviço `main-kairos` roda o gateway
(`kairos gateway`), compartilhando `state.db`.

## Contrato

Um job agendado com `delivery` roda o turno pelo caminho real
(`build_interaction_service` + LLM fake determinístico sem `tool_calls`), a
obrigação durável `cron-<execution>` nasce no ledger, e o `GatewayService` com o
`TelegramAdapter` real (construído do mesmo home: `messaging.json` + cofre)
drena a obrigação enviando `sendMessage` para a Bot API fake:

```
JobStore.create(delivery=telegram:CHAT) -> Scheduler.tick (interaction real)
-> obrigação no ledger -> GatewayService.tick -> TelegramAdapter real
-> POST {token}/sendMessage -> bot fake recebe o texto do turno
```

Únicos pontos "fingidos" (mesmo padrão do harness de entrada):
`__TELEGRAM_API` do adapter apontado para o servidor de loopback e o endpoint do
provedor (SSE determinístico). Nenhuma credencial real, nenhuma rede externa.

## Setup

- Home descartável com cofre de verdade (passphrase sintética via
  `KAIROS_DISABLE_KEYRING` + `KAIROS_VAULT_PASSPHRASE_FILE`): chave fake do
  provider `custom` e token fake do bot (`save_platform_secret`).
- `config.yaml`: `provider: custom`, `model: stub-chat`,
  `provider_settings.custom.base_url` → LLM fake.
- `messaging.json`: `telegram.enabled: true` (canais de entrada desligados).

## Casos cobertos

1. **Laço feliz da entrega externa**: o turno do cron completa, a obrigação
   nasce com o texto exato do LLM fake e o destino `telegram:CHAT`; o gateway
   com o adapter real entrega `1` obrigação e o bot fake recebe `sendMessage`
   com `chat_id` e texto exatos; o ledger confirma (`delivered == 1`).
2. **Fail-closed sem adapter**: `telegram` habilitado mas sem segredo no cofre
   → `build_platform_adapters` não constrói o adapter; o gateway não finge
   entrega: `delivered == 0`, a obrigação permanece `pending` e **nenhum**
   `sendMessage` chega ao bot fake.

## Validação

- `tests/test_cron_delivery_local_harness.py`: 2 testes verdes na suíte normal
  (offline, loopback, sem opt-in).
- Suíte local completa: **2.699 testes + 5.801 subtestes**, 39 pulados, 1
  deselecionado (runtime_live).
- `ruff check` e `ruff format --check` limpos.