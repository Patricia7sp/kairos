# MCP de terceiros — ferramentas externas no toolset (passo 4/P3)

**Decidido em 22/09/2026** (decisão da usuária, junto com a do passo 3):
avançar a candidata P3 "Ferramentas por MCP de terceiros" do
`docs/plano-ferramentas.md`.

## Contexto: o que já existe

- `kairos_mcp/client.py` é a port da Tarefa 12: `MCPServerConfig`,
  `validate_server_config` (anti-abuso nas duas pontas), `namespaced_tool_name`
  (namespace `mcp__<servidor>__<ferramenta>`, delimitador **duplo** da spec),
  `SchemaCache` (manifesto em disco, registra **sem spawnar** o servidor
  stdio), `truncate_mcp_text_result` (teto rígido acima do spillover). A
  dependência do pacote `mcp` é **opcional — módulo no-op sem ele**.
- `kairos_mcp/server.py` é o servo MCP que o Kairos **oferece** (conversas) —
  nada a ver com consumir servidores de terceiros.
- O registry já tem toolset com `requirement` gated (`calendar`, `git`) e o
  formato `ToolEntry`/`register` para nomes dinâmicos.

**O que NÃO existe hoje:** o runtime consumidor — nada spawna um servidor MCP
de terceiros, faz `initialize`/`tools/list`, registra as ferramentas nem
despacha `tools/call`. Por isso o passo 4 é o runtime do cliente + a porta de
entrada no turno.

## Objetivo

Servidores MCP configurados pela usuária em `<home>/config.yaml` têm suas
ferramentas **registradas e chamáveis no turno**, com cinto estreito mantido:
nenhuma terceira entra sem estar configurada e sem passar pelo mesmo gate de
disponibilidade dos toolsets próprios.

## Decisões deste lote (confirmadas pela usuária em 22/09/2026)

1. **Cliente stdio em stdlib, zero dependência nova.** O protocolo MCP stdio é
   JSON-RPC 2.0, 1 mensagem objetiva por linha (UTF-8, `\n`). `subprocess.Popen`
   + um leitor/escritor de linhas JSON em `kairos_mcp/runtime.py` bastam —
   consistente com o "pacote `mcp` opcional" da Tarefa 12 (o pacote continua
   não exigido). Transportes http/sse **recusados de vez neste lote** (recusa
   barulhenta, zero ferramentas): o runtime é stdio puro e a
   `AUTHENTICATION_RATIONALE` já condiciona o remoto a revisão **antes** de
   existir.
2. **Config:** `mcp_servers.<nome>` **top-level** em `<home>/config.yaml`
   (ref. spec MCP-02), campos `{transport, command, args, url, env}` →
   `MCPServerConfig`, validado por `validate_server_config` (as duas pontas).
   `env` = variáveis do subprocesso. Config com forma inválida ⇒ **recusa no
   boot com erro nomeado** (fail-closed), não "aceita apesar de".
3. **Registro sem boot pesado:** no boot, `register_mcp_tools()` carrega o
   `SchemaCache` de `<home>/mcp/` e registra manifestos já sincronizados **sem
   spawn**. Cache ausente ⇒ sincroniza **uma vez no registro** (spawn,
   `initialize`, `tools/list`, grava manifesto) — exceto sob pytest ou com
   `KAIROS_MCP_OFFLINE=1` (aí cache ausente ⇒ zero ferramentas, nada spawna).
   `sync_mcp_servers(home)` é o caminho **explícito** do on-ramp (testes e ops
   chamam direto).
4. **Toolset `mcp`: gated e honesto.** `requirement` barato (sem rede, sem
   spawn): há ao menos um `mcp_servers` válido configurado? Sim ⇒ ferramentas
   do toolset ficam disponíveis. Servidor configurado que falha na
   sincronização ⇒ **registra zero ferramentas desse servidor** (nada exposto,
   nada inventado) e loga — nunca "ferramenta que responde erro sempre". Nome
   via `namespaced_tool_name` **delimitador duplo** `mcp__<servidor>__<ferramenta>`
   (spec MCP-02; com um único `_` o separador colidiria com nomes de ferramenta).
5. **Porta no Chat:** allowlist estática de 11 nomes + **prefixo `mcp__`** para
   as dinâmicas (a disponibilidade ainda é gated pelo toolset `mcp` — o que o
   modelo vê é `get_definitions`, que omite toolset indisponível). **Toda**
   chamada `mcp__*` exige **aprovação por turno** (fail-closed: não dá para
   inferir mutação de um schema de terceiro; por padrão nada executa sem OK —
   reverte para fluir só com uso real observado e regra explícita).
6. **Despacho:** handler → `tools/call` com timeout e sessão **por chamada**
   (spawn → initialize → call → encerra; nunca zombie); resultado textual pelo
   `truncate_mcp_text_result` no teto rígido (acima do spillover — T-21);
   erro de protocolo / servidor morto ⇒ `{"error": ...}` nomeado, **nunca
   sucesso**.

## Escopo e limites (honestos)

- Fora do lote: streams/notificações sse, `roots`, sampling, progresso e
  prompts do MCP; custom transports; http/sse; `mcp.run`/CLI própria.
- Ferramentas terceiras **nunca** entram em `SANDBOX_ALLOWED_TOOLS` /
  `DELEGATE_BLOCKED_TOOLS` (política intacta).
- O pacote `mcp` não vira dependência obrigatória; `mcp_available()` continua
  informando (o cliente stdio do Kairos é independente dele).
- Cache corrompido = cache ausente (comportamento já do `SchemaCache`).

## Entrega

1. `kairos_mcp/runtime.py`: `McpRuntimeError`, `_Session` (spawn → `initialize`
   → `tools/list` paginado → `tools/call`), leitura crua do fd com `select` +
   `os.read` (sem double-buffer), dreno de stderr, encerramento limpo (terminate
   → kill → join); resposta a requisições do servidor com `Method not found`;
   **nunca** deixa zombie. API: `fetch_tool_manifests(config)` e
   `invoke_tool(config, name, arguments)`.
2. `kairos_tools/mcp_tools.py`: `register_mcp_tools(reg)` (toolset `mcp`,
   registro do cache, sync no cache ausente fora de pytest/offline),
   `sync_mcp_servers(home)` (on-ramp explícito com relatório), `server_configs`
   (origem da verdade), schema/handler por servidor; reuso de `<home>/config.yaml`.
3. `kairos_integration/chat_tools.py`: allowlist estática + prefixo `mcp__`;
   `needs_tool_approval` ⇒ `True` para `mcp__*`.
4. Testes: servidor MCP **fake real** (script stdio JSON-RPC em
   `tests/fake_mcp_server.py`) falado por pipe de verdade — nenhum mock do
   transporte; manifest→registro sem spawn (cache); sync explícito no servidor
   real; paginação; notificações/requisição do servidor; bloco não-textual;
   stderr drenado; `isError`/morte no `tools/call` ⇒ erro nomeado; aprovação
   `mcp__*` sempre; sem config ⇒ toolset ausente e zero ferramentas no prompt.
5. Docs: `docs/plano-ferramentas.md` (P3 sai da "adiar até necessidade" para
   entregue), planos datados, `docs/functional-progress.md` pós-merge.

## Gate

Suíte local inteira verde + Ruff + `uv lock --check` (sem mudança de
dependências) + `scripts/ci.sh --fast`; CI verde antes do merge (jobs conforme
`.github/workflows/ci.yml`); pós-merge sem remoções acidentais e registro
`docs(progress)`.