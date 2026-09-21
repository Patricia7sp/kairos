# Paridade inbound/outbound dos canais — Slack e webhook genérico de entrada

Item 2 (conectores de canais): os quatro canais configuráveis hoje
(Telegram, WhatsApp, Slack, webhook) ficam com **entrada e saída**. O Slack só
entregava (adapter incoming webhook); o webhook só postava (adapter de
endpoints). Este recorte fecha cada lacuna com o mesmo fail-closed dos canais
existentes e 100% offline (fakes, nenhuma credencial real, nenhuma rede).

## Contratos

### Slack inbound — Events API por webhook

- Rota pública `POST /api/inbound/slack` (em `_OPEN_PATHS`; segurança do canal).
- `slack.inbound`: `enabled`, `allowed_user_ids` (listas de user IDs `U…`;
  vazia = ninguém fala), `experiences` (padrão ligado).
- Segredo de entrada no cofre: `signing_secret` (campo novo de
  `INBOUND_SECRET_KEYS`, gravável pelo painel/API e visível em `campo_secreto`).
- Verificação real: `X-Slack-Signature` `v0` (HMAC-SHA256 de
  `v0:<timestamp>:<corpo cru>` com o Signing Secret) comparada em tempo
  constante **e** anti-replay (|agora − `X-Slack-Request-Timestamp`| ≤ 300 s).
- Handshake: `url_verification` devolve o `challenge` no corpo (assinado como
  qualquer entrega); `event_callback` com `event.type == "message"` sem
  `subtype` vira turno assíncrono; responde pelo adapter entrante no canal da
  conversa (`slack:{channel}`).
- Erros: sem segredo/desabilitado → 503; assinatura/janela divergente → 401.

### Webhook inbound — ingestão por token

- Rota pública `POST /api/inbound/webhook` (em `_OPEN_PATHS`).
- `webhook.inbound`: `enabled`, `allowed_sources` (nome de fonte; vazia =
  ninguém fala), `experiences`.
- Segredo de entrada no cofre: `ingest_token` (header
  `X-Kairos-Webhook-Token`; comparação em tempo constante).
- Corpo `{"text", "source?", "reply_url?", "id?"}`; fonte = corpo ou header
  `X-Kairos-Webhook-Source`, contra `allowed_sources` (fail-closed).
- Resposta volta pelo adapter webhook para `reply_url` (se vier) ou o endpoint
  padrão (`webhook:`); `id` opcional dá idempotência.
- Erros: sem token/desabilitado → 503; token divergente → 401.

### Ambos (princípios AGENTS.md)

- Sem adapter de envio o turno **não roda** — nada de efeito fingido.
- Dedupe em memória por id (Slack: `event_id`; webhook: `id` opcional).
- Aprovação de ferramenta não tem botões: recusa honesta aponta o painel.

## Arquivos

- `kairos_gateway/adapters/config.py`: `slack.inbound`/`webhook.inbound` no
  schema (`_FIELDS`, `_OPTIONAL_FIELDS`, `_INBOUND_SCHEMAS`,
  `_INBOUND_ALLOWLIST_ITEM`, `_INBOUND_OPTIONAL_DEFAULTS`, `default_config`) +
  `INBOUND_SECRET_KEYS` (signing_secret/ingest_token).
- `kairos_gateway/slack_inbound.py` (novo, espelha `whatsapp_inbound.py`),
  `kairos_gateway/webhook_inbound.py` (novo).
- `kairos_web/inbound_api.py`: rotas `POST /api/inbound/{slack,webhook}`;
  `kairos_web/server.py`: `_OPEN_PATHS`.
- `kairos_web/messaging_api.py`: `InboundSecretBody` aceita `signing_secret` e
  `ingest_token` (merge/drop no cofre).
- Testes: `tests/test_slack_inbound.py`, `tests/test_webhook_inbound.py`
  (unitários espelhando o WhatsApp), `tests/test_inbound_api.py` (rotas +
  segredo merge/drop), `tests/test_messaging_config.py` (schema novo).
- Docs: `docs/uso-por-canal.md`, `docs/functional-progress.md`.

## Validação

- Novos testes: 35 unitários + 11 de rota/config (fakes, loopback, nada real).
- Suíte local completa: **2.753 testes + 5.801 subtestes**, 39 skipped, 1
  deselected (runtime_live) — verde.
- `ruff check` e `ruff format --check` limpos.