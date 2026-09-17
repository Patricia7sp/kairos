# Uso por canal

Passo a passo operacional de cada superfície do Kairos, com limites e
dependências externas explícitos. Reflete o código de `main` + PR #64; onde algo
não existe, está dito.

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
- **Limite:** não há tela para listar/confirmar experiências; use a CLI.

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
- **Limite:** só long-poll; webhook de entrada não existe.

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
- **CLI:** `kairos whatsapp config|test` são placeholder explícito — não alteram
  configuração nem prometem envio.
- **Entrada:** não existe. Não há polling nem webhook de recebimento.
- **Dependências externas:** conta Meta Business, `phone_number_id` e token de
  acesso. Sem eles o adapter não é construído.

## Ferramentas por canal

Todas as superfícies de conversa compartilham o toolset core. Ferramentas
mutadoras exigem aprovação por turno; a política de delegação
(`kairos_tools/policy.py`) continua valendo. Detalhes e candidatas de expansão
em `docs/plano-ferramentas.md`.

## Limites e dependências (resumo)

| Item | Estado |
|---|---|
| Telegram inbound | fato (long-poll, fail-closed) |
| Telegram webhook de entrada | não existe |
| WhatsApp saída | fato (adapter) |
| WhatsApp entrada | não existe |
| WhatsApp CLI | placeholder explícito |
| Tela web de experiências | não existe |
| Segredo de plataforma | só via web/API (`save_platform_secret`) |
| `kairos auth add` | não implementado (retorna não-implementado) |

## Referências

- `docs/diagnostico-consolidado.md` — estado real e divergências.
- `docs/plano-ferramentas.md` — proposta de expansão.
- `kairos_cli/telegram.py`, `kairos_gateway/inbound.py`,
  `kairos_web/messaging_api.py`, `kairos_cli/memory.py`.
