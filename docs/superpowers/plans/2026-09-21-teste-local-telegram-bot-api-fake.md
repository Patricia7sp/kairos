# Teste local Telegram→Kairos com Bot API fake

Teste local, offline, de loopback (nenhuma rede real) que valida a **conexão**
Telegram→Kairos para uma operação que não tem (nem deve ter) token real de bot
em ambiente de desenvolvimento. A regra `runtime_live` continua valendo para o
caminho real: credencial real → não roda sem ela.

## Contrato

Com uma **Bot API fake** local (mesmo contrato da API pública: `getMe`,
`getUpdates` com long-poll e offset, `sendMessage`, `sendChatAction`,
`answerCallbackQuery`) e um **LLM fake** OpenAI-compatible (`GET /v1/models` +
`POST /v1/chat/completions` em SSE, resposta determinística e sem `tool_calls`),
o harness percorre o laço pelo caminho de produção:

```
update -> TelegramInbound -> InteractionRouter -> ProviderGateway ->
adapter custom -> LLM fake -> delta -> sendMessage -> bot fake
```

`kairos_gateway.inbound._TELEGRAM_API` é apontado para o bot fake por
monkeypatch — único ponto "fingido" no transporte. O agente (router, seleção de
modelo, gateway de provedores, adapter) é o código real; o provider é um stub
determinístico. Sem credencial real, sem execução de ferramentas, sem socket do
runtime (a resposta do stub não emite `tool_calls`).

## Setup do home descartável

- `config.yaml`: `provider: custom`, `model: stub-chat` e
  `provider_settings.custom.base_url` → `http://127.0.0.1:PORT/v1` (via
  `update_config_document`).
- `messaging.json`: canal Telegram ligado, `inbound.enabled: true`,
  `mode: poll`, `poll_interval_seconds: 0.5`, `experiences: false` e
  `allowed_user_ids: [TG_USER]`. Fail-closed: lista vazia → ninguém fala.
- Vault real (encriptado, passphrase sintética via
  `KAIROS_DISABLE_KEYRING=1` + `KAIROS_VAULT_PASSPHRASE_FILE`): token fake do
  bot (`save_platform_secret`) e chave fake do provider `custom`
  (`CredentialRef("custom", "primary")`).

## Casos cobertos

1. **Laço feliz**: um usuário autorizado manda "Enviar para o agente"; o laço
   devolve a resposta determinística do LLM fake via `sendMessage` (chat_id e
   texto exatos), o `getMe` de verificação acontece, o provider fake é chamado
   e o high-water `consumed` avança.
2. **Fail-closed**: um remetente fora de `allowed_user_ids` não vira turno —
   sem `sendMessage`, sem chamada ao LLM fake; `consumed` avança porque o
   update é aceito no poll e *ignorado* pelo cinto estreito.

Encerramento: marcador de drenagem (`stop_telegram_inbound`) encerra o loop; o
router é fechado (worker de persistência e clientes) no fim do teste.

## Validação

- `tests/test_telegram_local_harness.py`: verde na suíte normal (offline,
  sem opt-in).
- Suíte local completa: 2.697 testes + 5.801 subtestes, 39 pulados, 1
  deselecionado (runtime_live).
- `ruff check`/`ruff format` limpos.

Nenhuma credencial real é criada nem usada; os servidores fake vivem por
função, em loopback, e são encerrados em `finally`.