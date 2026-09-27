# T-28 — mail / email / lembretes por canais

Plano do lote em `feat/t28-email-lembretes`, base `main`.

## O que a spec diz (T-28 é costura, não receita única)

A spec não tem um "T-28 de email": o rótulo agrupa **três eixos** que estão
espalhados por dados que a Kairos já consome:

1. **`email` como plataforma de entrega** — existe no enum do
   `_reversa_sdd/data-dictionary.md` (`_KNOWN_DELIVERY_PLATFORMS`, ADR de
   plataformas) mas não há contrato/adapter documentado; o legado resolvia com
   SMTP no adapter e dependia de `EMAIL_HOME_ADDRESS` (não listada na spec).
2. **Lembrete = job de agenda `once`** (`data-dictionary.md`: entradas
   `"30m"`, `"2h"`, `"2026-02-03T14:00"` → `kind=once`, `run_at` ISO) — o
   scheduler Kairos já executa `once`, só falta a superfície que parseia o
   texto e cria o job.
3. **Entrega por canais via obrigação durável** (`providers-gateway/
   requirements.md`): métrica assíncrona é gravada em `delivery_ledger` e só
   vira `delivered` com confirmação do adapter — exatamente o ledger Kairos.

Recorte deliberado: **email entra como canal de saída (SMTP)**, gated e com
recusa honesta; **fonte de entrada (IMAP) NÃO é portada** — a spec é silenciosa
sobre ela e o cinto estreito não justifica um novo ingress sem contrato. O
blueprint `important-mail` mantém o texto honesto ("explique como conectar uma
e pare").

## Entregas

### 1. Email como plataforma de entrega (stdlib, gated)

- `kairos_gateway/adapters/email_adapter.py` — `EmailAdapter` (smtplib +
  `email.message.EmailMessage`, sem dependência nova):
  - alvo `email:destino`; `verify()` conecta e faz EHLO real (nunca envia);
  - `send()` mapeia resultado honesto: `auth`/`destino_rejeitado`/
    `destino_invalido` → permanente; `rede`/`smtp` → transitório (reentrega);
  - SMTP STARTTLS (config `tls`), usuário = `from_addr`, senha no cofre.
- `kairos_gateway/adapters/config.py`:
  - `_PLATFORMS += "email"`, `_FIELDS["email"]` (`enabled`, `smtp_host`,
    `smtp_port` int, `tls` bool, `from_addr`, `address_default`,
    `subject_default`), `default_config` e `SECRET_KEYS["email"] =
    "smtp_password"`; validador ganha ramo `int`.
- `kairos_gateway/adapters/__init__.py`: entrada `email` em `PLATFORMS`
  (needs_secret, `from_addr`/`address_default`/`smtp_host`/`smtp_port`/`tls`
  como campos), e `build_platform_adapters` monta só com enabled + segredo.
- Superfícies herdam automaticamente: `kairos gateway list/status`,
  `PUT/DELETE /api/messaging/email`, credential, test e `POST /messaging/send`
  (web), e — pós-merge do #83 — o servidor MCP stdio (`platforms_list` e
  `messages_send`).
- CLI `kairos email status|config|test` (padrão `kairos slack`): campos
  não-secretos, segredo "ausente — defina via Integrações (web)".
- CLI `kairos gateway send TARGET TEXTO` — *standalone sender* (o `hermes
  send` do legado): mesmo fluxo do painel (obrigação no ledger + claim +
  adapter.send + confirm/release/abandon), recusa honesta (exit 69) quando a
  plataforma não está entregável.

### 2. Lembrete de propósito geral

- `kairos_cron/remind.py` — `parse_when(valor, agora)` → `{"kind": "once",
  "run_at": iso}`; formatos do `data-dictionary`: `30m`/`2h`/`1d`, ISO com
  fuso, `HH:MM` (hoje; amanhã se já passou — hora local do processo).
- Comando `kairos remind QUANDO MENSAGEM... [--deliver TARGET]`:
  `JobStore.create` (once, `times=1`), aparece em `kairos cron list` e tem
  ocorrência no ledger; sem `--deliver` a lembrança fica só na conversa do job
  (honesto), com `--deliver` sai pela obrigação durável.

### 3. Entrega por canais com teste real (não shape-only)

- `tests/test_cron_blueprints.py`: `test_create_with_delivery` deixou de
  afirmar só o shape — cria o job `important-mail` com `deliver=email:…`,
  habilita SMTP num sink de teste e prova a cadeia
  **ledger → claim → adapter.send (SMTP real) → confirm** via um
  `GatewayService` com o `EmailAdapter` real (o sink é um servidor SMTP mínimo
  em socket real, stdlib, no próprio teste — sem mock do caminho).
- `tests/test_email_adapter.py` (novo): sink SMTP no socket real; envio
  entrega o corpo, `verify()` conecta sem enviar, alvo/credencial ausentes
  recusam honesto.
- `tests/test_messaging_config.py`: o conjunto de plataformas ganha `email`.

## Fora de escopo (dívidas conscientes)

- **Fonte de entrada de email (IMAP/POP3)** — spec silenciosa; `important-mail`
  continua honesto.
- **`home_target_set` generalizado por plataforma** — a observabilidade
  completa do "habilitada mas sem alvo home" fica com a dívida; para email o
  `address_default` cobre o alvo home.
- **`kairos gateway setup`** continua `NOT_IMPLEMENTED` (69 honesto); email
  configura-se por `kairos email config` + cofre/web.

## Validação

Suíte completa + `ruff check`/`ruff format --check` + `scripts/ci.sh --fast`.

## Registro

D-T28.1–5 em `docs/decisoes.md`; `docs(progress)` pós-merge, no padrão.

## Estado (fim do lote)

Tudo acima entregue, com três ajustes de execução:

- **`kairos_gateway/direct_send.py` (novo)** centralizou a cadeia `obrigação no
  ledger → claim → adapter.send → confirm/release/abandon` usada pelo painel
  (`POST /api/messaging/send`) e pelo `kairos gateway send` — um único caminho
  de entrega, não dois. Target malformado = `ValueError` (CLI USAGE 2 / web
  422); plataforma não entregável = `DeliveryTargetError` (CLI 69 honesto /
  web 400).
- **A prova real da cadeia inteira** (job → turno → ledger → `GatewayService`
  → `EmailAdapter` real) vive em `tests/test_cron_delivery_local_harness.py`
  (novo teste com `SmtpSink`), junto ao harness Telegram existente;
  `test_cron_blueprints.test_create_with_delivery` continua afirmando o
  shape do job (o que ele cobre) e o caminho real é o harness.
- **`kairos email test` sem alvo e sem canal entregável recusa com código de
  falha** (mensagem "nada foi enviado") — testar e-mail desabilitado não finge
  conexão; `verify()` só conecta quando `enabled` + `smtp_host` +
  segredo no cofre existem.