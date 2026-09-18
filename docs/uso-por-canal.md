# Uso por canal

Passo a passo operacional de cada superfície do Kairos, com limites e
dependências externas explícitos. Reflete o código de `main` (PRs #64 e #66);
onde algo não existe, está dito.

## Pré-requisitos comuns

- Python 3.11 via `uv`. Bootstrap: `uv venv --python 3.11 && uv pip install -e ".[dev]"`.
- Um `KAIROS_HOME` (perfil) com modelo selecionado (`kairos model set ...`) e
  credenciais de provedor (`kairos login --provider <p> --api-key <k>`).
- O runtime é opcional para o Chat por modelo; sessões de *agent runtime* o
  exigem.

## Terminal

```bash
kairos run "resuma o README"                 # one-shot, sessão cli-default
kairos chat --session minha-conversa         # interativo; 'sair'/Ctrl+C encerra
kairos chat --session s1 --web-search "..."  # liga busca web neste turno
kairos chat --session s1 "..."               # experiências ativas entram por padrão
kairos chat --session s1 --no-experiences "..."  # desliga neste turno
kairos run --provider openrouter --model acme/chat "..."   # override do turno
```

- `--provider` e `--model` andam juntos; só um dos dois é erro de uso.
- Experiências ativas relevantes são injetadas **no conteúdo do turno atual**
  por padrão; sem match, nada muda (ver `docs/diagnostico-consolidado.md`).
  Use `--no-experiences` para desligar.
- `--json` emite NDJSON versionado (protocolo 1) para script.

Gestão de memória e experiências:

```bash
kairos memory status            # MEMORY.md / USER.md
kairos memory off               # limpa os dois arquivos
kairos memory experiences list [--all]
kairos memory experiences add --trigger T --correction C [--observation O] \
    [--scope S] [--confidence 0.9] [--status ativa|candidata|invalida]
kairos memory experiences confirm|reject|invalidate <id>
kairos memory experiences record <id> [--success]
```

## Web

```bash
kairos web            # sobe o servidor e abre o navegador (ou --no-browser)
```

- Conversa, escolha de modelo, ajustes, uso/custo e exportação de transcrição.
- **Integrações** é a tela que configura e testa canais. É o caminho real para
  gravar segredos de plataforma (equivalente a
  `POST /api/messaging/{platform}/credential`).
- Rotas de mensageria: `GET /api/messaging`,
  `PUT /api/messaging/{platform}`, `POST|DELETE
  /api/messaging/{platform}/credential`, `POST /api/messaging/{platform}/test`,
  `GET|POST /api/messaging/webhook/endpoints`, `POST /api/messaging/send`.
- **Experiências:** o painel tem a tela **Experiências** (listar, confirmar,
  rejeitar, invalidar e registrar resultado); a CLI continua equivalente
  (`kairos memory experiences`).

## Telegram

### Configurar o segredo (cofre)

O bot token é gravado pela **tela de Integrações** (ou pela API):

```bash
# via API (servidor web no ar), com o token do @BotFather:
curl -s -X POST http://127.0.0.1:9119/api/messaging/telegram/credential \
  -H 'content-type: application/json' -d '{"secret":"<BOT_TOKEN>"}'
```

Não há comando de CLI que grave esse segredo (`kairos auth add` não chama o
cofre de plataforma e retorna não-implementado).

### Configurar campos não-secretos

```bash
kairos telegram config --enabled true --inbound-enabled true \
    --allowed-ids 11111111,22222222 --poll-interval 2 --chat-default 11111111
```

- `--allowed-ids` é a **allowlist**: vazio significa **ninguém** (fail-closed).
- `poll-interval` aceita 0.5–300 s.

### Verificar, rodar e parar

```bash
kairos telegram test     # chama getMe; não envia mensagem ("nada foi enviado")
kairos telegram status   # enabled, autorizados, watermark, drenagem, token
kairos telegram run      # long-poll em primeiro plano
kairos telegram stop     # grava o marcador de drenagem; o loop encerra com calma
```

### Comportamento do canal

- Mensagens de remetentes autorizados viram turnos no `InteractionRouter`
  (`conversation_id=telegram:{chat_id}`, idempotência por `telegram:{update_id}`).
- Aprovação de ferramenta chega como teclado inline (Aprovar/Negar).
- `/start` mostra a ajuda; `/status` confirma que o canal está ativo.
- Respostas longas são fatiadas em ≤4000 caracteres.
- **Dependências externas:** token do bot e IDs numéricos autorizados. Sem
  token, `run` falha fechado; sem allowlist, nada é atendido.
- **Transporte:** o canal tem dois modos em `telegram.inbound.mode`:
  - `poll` (padrão): long-polling com `kairos telegram run` (primeiro plano);
  - `webhook`: o servidor web atende `POST /api/inbound/telegram` e o turno
    roda fora do `run` — o long-poll fica desativado (a Bot API recusa
    `getUpdates` enquanto webhook ativo).
- **Limite:** webhook exige URL HTTPS pública e o servidor web rodando.

#### Webhook de entrada (`inbound.mode = webhook`)

```bash
# 1. segredo do webhook no cofre (merge preserva o token do bot)
curl -s -X POST http://127.0.0.1:9119/api/messaging/telegram/inbound-secret \
  -H 'Content-Type: application/json' \
  -d '{"webhook_secret_token":"<secreto>"}'

# 2. habilita e aponta o Telegram para a URL pública do servidor de vocês
kairos telegram config --inbound-enabled true --mode webhook
kairos telegram webhook https://<host>/api/inbound/telegram
```

- A rota é pública (`_OPEN_PATHS`), sem sessão — a segurança é do canal: o
  header `X-Telegram-Bot-Api-Secret-Token` tem de conferir com o segredo do
  cofre (comparação em tempo constante). Sem segredo ou divergente → 503/401,
  nada processado.
- No modo `poll` o `POST /api/inbound/telegram` recusa (503, fail-closed); o
  `kairos telegram run` recusa no modo `webhook`.
- O turno roda assíncrono (ack `ok` imediato); a idempotência por
  `telegram:{update_id}` segura retransmissão. Auth do remetente é a mesma
  allowlist do long-poll.
- `kairos telegram webhook-off [--drop-pending]` cancela o webhook e volta ao
  modo `poll`; `kairos telegram status` mostra `inbound.mode`, `webhook_url` e
  a presença do segredo.

### Experiências no canal (ligadas por padrão)

Cada turno recebido é prefixado com as experiências ativas relevantes (mesma
regra do terminal); sem match, nada muda. Para desligar:

```bash
kairos telegram config --inbound-enabled true ...   # ajuste os campos
# no messaging.json, use "experiences": false no bloco telegram.inbound
```

Candidatas e inválidas nunca são injetadas.

## WhatsApp

- **Saída real:** adapter da API Cloud (Meta Graph v21.0). Configure
  `phone_number_id` e número padrão pela tela de Integrações (`PUT
  /api/messaging/whatsapp`) e o token via
  `POST /api/messaging/whatsapp/credential`; envie com `POST /api/messaging/send`
  ou pelo painel.
- **CLI:** `kairos whatsapp config|status` gravam e leem os campos não-secretos
  em `messaging.json` (`enabled`, `phone_number_id`, `number_default`) e
  reportam a presença do token no cofre; `test` verifica token e número pela
  API Cloud sem enviar ("nada foi enviado").
- **Entrada (webhook da Cloud API):** `GET/POST /api/inbound/whatsapp`.
  Requisitos no `messaging.json` (`whatsapp.inbound`):
  `enabled: true` e `allowed_phone_numbers` (E.164, só remetentes desta lista
  falam com o agente — lista vazia = ninguém) e no cofre os segredos de
  entrada: `app_secret` (valida `X-Hub-Signature-256`) e `verify_token`
  (apertão de mão `hub.challenge`), gravados por
  `POST /api/messaging/whatsapp/inbound-secret` (merge preserva o `access_token`).
  O subscribe da Meta aponta para `https://<host>:443/api/inbound/whatsapp`
  (rota pública, sem sessão — a segurança é a verificação do próprio canal).
  Sem segredo ou com assinatura divergente, o webhook recusa (503/401) —
  nada é processado. O turno roda assíncrono (ack `EVENT_RECEIVED` imediato) e
  responde ao remetente; aprovação de ferramenta não tem botões no WhatsApp —
  a recusa é honesta e aponta para o painel.
- **Dependências externas:** conta Meta Business, `phone_number_id`, token de
  acesso, `app_secret` + `verify_token` do app, e **URL pública** para o
  webhook. Sem elas nada responde (adapter não construído; webhook recusa).

## Slack

```bash
kairos slack config --enabled true --channel-default '#ops'
kairos slack status   # enabled, canal padrão, presença da URL no cofre
kairos slack test     # valida a forma da URL; nunca envia ("nada foi enviado")
```

- A URL do incoming webhook é gravada pela tela de Integrações (ou
  `POST /api/messaging/slack/credential`); a CLI não altera segredos.
- A API entrante do Slack não tem verificação sem envio: `test` confirma só a
  forma da URL; validar a conexão de verdade é o envio de teste (painel).
- **Dependências externas:** workspace Slack com incoming webhook criado num
  canal. Sem `enabled` + URL no cofre, o adapter não é construído.

## Ferramentas por canal

Todas as superfícies de conversa compartilham o toolset core. Ferramentas
mutadoras exigem aprovação por turno; a política de delegação
(`kairos_tools/policy.py`) continua valendo. Detalhes e candidatas de expansão
em `docs/plano-ferramentas.md`.

## Limites e dependências (resumo)

| Item | Estado |
|---|---|
| Telegram inbound | fato (long-poll ou webhook, fail-closed) |
| Telegram webhook de entrada | fato (rope pública `POST /api/inbound/telegram`, `mode: webhook`) |
| WhatsApp saída | fato (adapter) |
| WhatsApp entrada | fato (webhook Cloud API, fail-closed) |
| WhatsApp CLI | config/status reais; test via `verify()` sem envio |
| Slack CLI | config/status reais; test valida só a forma da URL |
| Tela web de experiências | existe (Painel → Experiências) |
| Segredo de plataforma | só via web/API (`save_platform_secret`) |
| `kairos auth add` | não implementado (retorna não-implementado) |

## Referências

- `docs/diagnostico-consolidado.md` — estado real e divergências.
- `docs/plano-ferramentas.md` — proposta de expansão.
- `kairos_cli/telegram.py`, `kairos_gateway/inbound.py`,
  `kairos_web/messaging_api.py`, `kairos_cli/memory.py`.
