# Diagnóstico consolidado: estado real das superfícies

Este documento compara o que está **de fato implementado e testado** nas
superfícies do Kairos — terminal (CLI), aplicação web, canal de entrada
Telegram e integração WhatsApp — e onde elas divergem. Ele não repete o
README antigo nem herda conclusões não verificadas: cada linha abaixo tem
origem em código e testes do repositório.

Atualizado em 17/09/2026, sobre `main` (`0f62438`) + lote de canal de entrada WhatsApp.

## Método

- **Fato:** coberto por teste verde e caminho executável real (sem efeito
  fingido). **Proposta:** ainda não implementado; registrado para decisão.
- Nenhum comando é declarado "completo" sem caminho externo validado. Onde há
  limite (ex.: conta externa, token de terceiro), ele está explícito.
- Ferramentas mutadoras, aprovação e política valem igualmente em todas as
  superfícies; a diferença está no transporte e no disparo.

## Superfícies e transporte

| Superfície | Transporte | Composição | Canal de entrada |
|---|---|---|---|
| Terminal | `kairos run` / `kairos chat` | `InteractionService` | stdin / one-shot |
| Web | FastAPI + SPA | `InteractionService` | HTTP/WS da própria sessão |
| Telegram | long-poll `getUpdates` | `InteractionRouter` | mensagens do bot |
| WhatsApp | Meta Graph (Cloud) | `InteractionRouter` | webhook `GET/POST /api/inbound/whatsapp` |

Todas as superfícies de conversa passam pelo **mesmo router** de interação
(`kairos_integration`). O que muda é o envelope: `source`, `web_search`,
`tools` e `conversation_id`.

## Estado por capacidade

| Capacidade | Terminal | Web | Telegram | WhatsApp |
|---|---|---|---|---|
| Conversa com modelo | fato | fato | fato | fato |
| Busca web no turno | `--web-search` | interface | ligada (`web_search=True`) | ligada (`web_search=True`) |
| Ferramentas core | fato | fato | fato (`tools=True`) | fato (`tools=True`) |
| Aprovação de ferramenta | terminal | modal na UI | keyboard inline | aviso honesto (sem botões, aponta o painel) |
| Memória de longo prazo | `kairos memory status/off` | leitura | herdada | herdada |
| Experiências (aprendizado) | ligadas por padrão (`--no-experiences` desliga) | — | `inbound.experiences` (padrão ligado) | `inbound.experiences` (padrão ligado) |
| Gestão de experiências | `kairos memory experiences` | tela **Experiências** (listar/confirmar/rejeitar/invalidar/registrar) | — | — |
| Envio de mensagem | — | `/api/messaging/send` | `TelegramAdapter` | `WhatsAppAdapter` |
| Config não-secreta | `kairos telegram config` | `/api/messaging/{platform}` | idem via CLI | `kairos whatsapp config|status` |
| Teste de conexão | `kairos telegram test` | `/api/messaging/{platform}/test` | verifica `getMe` | adapter verifica; CLI aciona |

## Terminal (CLI)

- `kairos run "..."` e `kairos chat --session ID` usam o mesmo serviço canônico;
  `run` cria a sessão `cli-default` quando não recebe `--session`.
- **Experiências:** a injeção por turno está ligada por padrão (terminal e
  Telegram); `--no-experiences`/`"experiences": false` desligam. O contexto entra
  no **conteúdo do turno atual**, nunca no system prompt — reescrever o prefixo
  invalidaria o cache por conversa (Lei 1 em `kairos_integration.surfaces`).
- **Telegram:** `kairos telegram config|test|status|run|stop`. O token do bot
  mora no cofre de plataforma, gravado pela tela de Integrações (ou
  `POST /api/messaging/telegram/credential`) — `kairos auth add` não chama esse
  cofre; `test` não finge envio (imprime "nada foi enviado") e `status` lê o
  watermark real.
- **Memória:** `kairos memory status|off` opera o toolset `memory`
  (`MEMORY.md`/`USER.md`); `kairos memory experiences list|add|confirm|reject|
  invalidate|record` opera o aprendizado (`kairos_memory`).

## Web

- Rotas de mensageria: `GET /api/messaging`, `PUT /api/messaging/{platform}`,
  `POST/DELETE /api/messaging/{platform}/credential`,
  `POST /api/messaging/{platform}/test`, `GET/POST
  /api/messaging/webhook/endpoints`, `DELETE
  /api/messaging/webhook/endpoints/{name}`, `POST /api/messaging/send`.
- `update_platform` faz merge preservando as chaves existentes
  (`{**doc[platform]}.update(body.config)`), de modo que editar um campo não
  apaga os demais. Chaves/segredos não retornam nas respostas.

## Telegram (canal de entrada)

- Long-poll com **offset persistido** em `inbound-telegram.json` e idempotência
  por `update_id` (`telegram:{update_id}`).
- **Fail-closed:** `allowed_user_ids` vazio significa que ninguém é atendido.
  Mensagens de remetente fora da lista são ignoradas.
- Aprovação de ferramenta por keyboard inline (`ka:{approval_id}:allow|deny`),
  resolvida com `answerCallbackQuery` + `decide_tool_approval`.
- Saída chunkada em ≤4000 caracteres; indicator de digitação; drenagem via
  marcador (`kairos telegram stop`) com shutdown gracioso.
- Comandos textuais: `/start` (ajuda) e `/status`.
- **Limite:** só long-poll. Webhook de entrada não é usado.

## WhatsApp

- **Saída real:** `kairos_gateway/adapters/whatsapp.py` envia texto pela API
  Cloud (Meta Graph v21.0), com `verify()` que confirma número/`phone_number_id`
  sem despachar mensagem.
- **Entrada (webhook da Cloud API, `kairos_gateway/whatsapp_inbound.py`):**
  `GET /api/inbound/whatsapp` (apertão de mão: devolve `hub.challenge` só com
  `hub.mode=subscribe` + Verify Token correto) e `POST /api/inbound/whatsapp`
  (valida `X-Hub-Signature-256` HMAC-SHA256 do corpo cru com o App Secret e
  acusa `EVENT_RECEIVED`). A rota é pública (a Meta não tem sessão); a
  segurança é a verificação do próprio canal — sem `app_secret`/`verify_token`
  no cofre ou assinatura divergente, recusa (503/401), nada é processado.
- O remetente é autenticado por `whatsapp.inbound.allowed_phone_numbers`
  (E.164, normalizado em dígitos); lista vazia = ninguém (fail-closed). O turno
  roda assíncrono após o ack, com dedupe por `wamid` (`whatsapp:{wamid}`) e
  resposta chunkada em ≤4000 caracteres; aprovação de ferramenta é aviso
  honesto apontando o painel (não há botões no WhatsApp).
- Segredos de entrada: `POST/DELETE /api/messaging/whatsapp/inbound-secret`
  gravam/removem `app_secret` e `verify_token` no cofre **preservando** o
  `access_token` (merge). O estado aparece em `GET /api/messaging`
  (`platforms[whatsapp].inbound.campo_secreto`).
- **CLI `kairos whatsapp config|status|test`:** `config`/`status` gravam e leem
  os campos não-secretos em `messaging.json` (`enabled`, `phone_number_id`,
  `number_default`) e reportam a presença do token no cofre — sem ler o segredo.
  `test` aciona `WhatsAppAdapter.verify` (confirma token e número sem despachar
  mensagem) e nunca alega envio ("nada foi enviado"). O segredo continua
  gravado pela tela de integrações (ou `POST /api/messaging/whatsapp/credential`).

## Slack

- **Saída real:** `kairos_gateway/adapters/slack.py` envia pela incoming webhook
  (alvo `slack:#canal`), construído com `enabled` + URL no cofre.
- **CLI `kairos slack config|status|test`:** `config`/`status` operam os campos
  não-secretos em `messaging.json` (`enabled`, `channel_default`) e reportam a
  presença da URL no cofre. `test` valida a **forma** da URL — a API entrante
  do Slack não tem verificação sem envio — e nunca alega envio.

## Divergências conhecidas

1. **Slack/WhatsApp CLI × web:** a web configura e testa de verdade (para o
   Slack o teste é envio real); a CLI opera o mesmo `messaging.json`,
   aciona `verify()` sem enviar (whatsapp) ou valida só a forma da URL
   (slack). Nem uma nem outra finge sucesso.
2. **Experiências:** expostas no terminal e no Telegram, e agora com tela no
   painel (listar/confirmar/rejeitar/invalidar/registrar resultado) além da CLI.
3. **Busca web no Telegram:** sempre ligada; no terminal é opt-in
   (`--web-search`).
4. **Teste de conexão:** `TelegramChannel.verify` (a CLI do telegram aciona
   `getMe`); `WhatsAppAdapter.verify` (a CLI aciona); o Slack só valida a forma.
5. **Webhook CLI × gateway:** o CLI `kairos webhook` passou a ler o mesmo
   `messaging.json` que o gateway e a API usam (antes lia um `webhooks.json`
   próprio que nada mais consumia).

## O que este documento não afirma

- Não certifica geração bem-sucedida de nenhum modelo específico nem
  autenticação de contas de terceiros (ex.: aceite real do subscribe da Meta em
  produção depende de URL pública exposta ao WhatsApp).
- Não afirma webhook de entrada Telegram nem paridade total entre as CLIs.
- Números de teste não são congelados aqui; a fonte é a suíte local e o CI.

## Referências

- `kairos_gateway/inbound.py`, `kairos_gateway/whatsapp_inbound.py`,
  `kairos_cli/telegram.py`, `kairos_cli/memory.py`,
  `kairos_web/messaging_api.py`, `kairos_web/inbound_api.py`.
- `kairos_gateway/adapters/whatsapp.py`, `kairos_gateway/adapters/telegram.py`.
- `tests/test_telegram_inbound.py`, `tests/test_whatsapp_inbound.py`,
  `tests/test_inbound_api.py`, `tests/test_experiences.py`,
  `tests/test_cli_chat.py`, `tests/test_messaging_api.py`.
- `docs/uso-por-canal.md` (passo a passo) e `docs/plano-ferramentas.md`
  (proposta de expansão).
