# Diagnóstico consolidado: estado real das superfícies

Este documento compara o que está **de fato implementado e testado** nas
superfícies do Kairos — terminal (CLI), aplicação web, canal de entrada
Telegram e integração WhatsApp — e onde elas divergem. Ele não repete o
README antigo nem herda conclusões não verificadas: cada linha abaixo tem
origem em código e testes do repositório.

Atualizado em 16/09/2026, sobre `main` (`99cb7f1`) + PR #64.

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
| WhatsApp | Meta Graph (Cloud) | — | **não existe** (só saída) |

Todas as superfícies de conversa passam pelo **mesmo router** de interação
(`kairos_integration`). O que muda é o envelope: `source`, `web_search`,
`tools` e `conversation_id`.

## Estado por capacidade

| Capacidade | Terminal | Web | Telegram | WhatsApp |
|---|---|---|---|---|
| Conversa com modelo | fato | fato | fato | — |
| Busca web no turno | `--web-search` | interface | ligada (`web_search=True`) | — |
| Ferramentas core | fato | fato | fato (`tools=True`) | — |
| Aprovação de ferramenta | terminal | modal na UI | keyboard inline | — |
| Memória de longo prazo | `kairos memory status/off` | leitura | herdada | — |
| Experiências (aprendizado) | ligadas por padrão (`--no-experiences` desliga) | — | `inbound.experiences` (padrão ligado) | — |
| Gestão de experiências | `kairos memory experiences` | — | — | — |
| Envio de mensagem | — | `/api/messaging/send` | `TelegramAdapter` | `WhatsAppAdapter` |
| Config não-secreta | `kairos telegram config` | `/api/messaging/{platform}` | idem via CLI | CLI é placeholder |
| Teste de conexão | `kairos telegram test` | `/api/messaging/{platform}/test` | verifica `getMe` | adapter verifica, CLI não |

## Terminal (CLI)

- `kairos run "..."` e `kairos chat --session ID` usam o mesmo serviço canônico;
  `run` cria a sessão `cli-default` quando não recebe `--session`.
- **Experiências:** a injeção por turno está ligada por padrão (terminal e
  Telegram); `--no-experiences`/`"experiences": false` desligam. O contexto entra
  no **conteúdo do turno atual**, nunca no system prompt — reescrever o prefixo
  invalidaria o cache por conversa (Lei 1 em `kairos_integration.surfaces`).
- **Telegram:** `kairos telegram config|test|status|run|stop`. O token vive no
  cofre (`kairos auth add --provider telegram --api-key <token>`); `test` não
  finge envio (imprime "nada foi enviado") e `status` lê o watermark real.
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
- **Entrada:** não existe. Não há polling nem webhook de recebimento.
- **CLI `kairos whatsapp config|test`:** placeholder explícito — não altera
  configuração nem promete envio ("Nenhum canal configurado; nada foi
  enviado."). A configuração real é feita pela tela de integrações da web
  (ou `PUT /api/messaging/whatsapp`).

## Divergências conhecidas

1. **WhatsApp CLI × web:** a tela web configura e testa de verdade; o comando
   de terminal não. A assimetria é conhecida e não finge sucesso.
2. **Experiências:** expostas no terminal e no Telegram; a web ainda não tem
   tela para listar/confirmar — as experiências são gerenciadas só pela CLI.
3. **Busca web no Telegram:** sempre ligada; no terminal é opt-in
   (`--web-search`).
4. **Teste de conexão:** o `TelegramChannel.verify`/`WhatsAppAdapter.verify`
   existem, mas a CLI do WhatsApp não os aciona; a CLI do Telegram aciona
   `getMe`.

## O que este documento não afirma

- Não certifica geração bem-sucedida de nenhum modelo específico nem
  autenticação de contas de terceiros.
- Não afirma canal de entrada WhatsApp, webhook de entrada Telegram,
  tela web de experiências nem paridade total entre as CLIs.
- Números de teste não são congelados aqui; a fonte é a suíte local e o CI.

## Referências

- `kairos_gateway/inbound.py`, `kairos_cli/telegram.py`,
  `kairos_cli/memory.py`, `kairos_web/messaging_api.py`.
- `kairos_gateway/adapters/whatsapp.py`, `kairos_gateway/adapters/telegram.py`.
- `tests/test_telegram_inbound.py`, `tests/test_experiences.py`,
  `tests/test_cli_chat.py`, `tests/test_messaging_api.py`.
- `docs/uso-por-canal.md` (passo a passo) e `docs/plano-ferramentas.md`
  (proposta de expansão).
