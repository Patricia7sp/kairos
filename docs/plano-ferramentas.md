# Plano de expansão de ferramentas (proposta)

**Isto é uma proposta, não uma autorização de implementação.** Nenhuma
ferramenta abaixo foi criada por este documento. A decisão de priorizar, adiar
ou descartar cada linha pertence à usuária; implementar sem essa decisão viola a
disciplina do projeto.

Atualizado em 21/09/2026.

## Regra de footprint (do `AGENTS.md`)

O core é um **cinto estreito**: cada ferramenta/modelo novo paga custo em todo
request. A ordem obrigatória de preferência é:

1. **Estender código** existente (menor superfície).
2. **Comando CLI + skill** (efeito local, sem payload de modelo).
3. **Ferramenta gated por `check_fn`** (só entra quando o pré-requisito existe).
4. **Plugin / servidor MCP** (extensão fora do core).
5. **Ferramenta nova** no toolset (último recurso).

Uma candidata só sobe para a etapa 5 se as etapas 1–4 falharem. Ferramentas
mutadoras continuam sujeitas a **aprovação por turno** e à política de bloqueio
de delegação (`kairos_tools/policy.py`).

## Estado atual do toolset

Ferramentas core registradas hoje (`kairos_tools/builtin.py`,
`kairos_tools/memory.py`):

| Ferramenta | Efeito | Mutadora |
|---|---|---|
| `bash` | executa comando | sim |
| `read_file` | leitura | não |
| `write_file` | escrita | sim |
| `edit_file` | edição | sim |
| `list_dir` | listagem | não |
| `web_search` | busca | não |
| `search_files` | busca local | não |
| `patch` | patch fuzzy | sim |
| `web_extract` | extrai URL | não |
| `memory` | memória de longo prazo | sim |

Infra já disponível e reutilizável: aprovação por turno (`kairos_tools/approval.py`),
orçamento (`budget.py`), política (`policy.py`), paralelismo (`parallel.py`),
sandbox externa (worker Docker), MCP (`kairos_mcp`), cron (`kairos_cron`) e o
aprendizado por experiência (`kairos_memory`).

## Candidatas (proposta), por prioridade

A coluna **valor** é uma hipótese a validar com uso real — não uma promessa.

### P1 — baratas e de alto reúso

| Candidata | Como entregar | Categoria / risco | Dependências | Custo de contexto | Aprovação | Valor hipotético |
|---|---|---|---|---|---|---|
| ~~Tela web de experiências~~ **implementado** | estender web (etapa 1) | leitura / baixo | `kairos_memory` (existe) | nenhum | não | fechar a divergência #2 do diagnóstico |
| ~~`--experiences` por padrão no Telegram e chat~~ **implementado** | extensão (etapa 1) | leitura / baixo | `kairos_memory` | só quando há match | não | correções recorrentes sem reexplicar |
| ~~`git` (status/diff/log/commit)~~ **implementado** | ferramenta gated por `Toolset.requirement` + aprovação por subcomando (etapa 3) | mutadora / médio | repositório git | pequeno | sim (em `commit`/mutadores) | operar o próprio repo com trilha |

### P2 — dependem de decisão de produto

| Candidata | Como entregar | Categoria / risco | Dependências | Custo | Aprovação | Observação |
|---|---|---|---|---|---|---|
| ~~Canal de entrada WhatsApp~~ **implementado** | estender gateway (etapa 1/4) | entrada / médio-alto | Cloud API + webhook público | — | — | `GET/POST /api/inbound/whatsapp`, fail-closed |
| ~~Webhook de entrada Telegram~~ **implementado** | extensão do inbound (etapa 1) | entrada / médio | HTTPS público | — | — | `telegram.inbound.mode: webhook` + `POST /api/inbound/telegram` |
| ~~Canal de entrada Slack~~ **implementado** | estender gateway (etapa 1/4) | entrada / médio-alto | Events API + Signing Secret | — | — | `POST /api/inbound/slack`, assinatura `v0` + anti-replay, fail-closed |
| ~~Canal de entrada webhook~~ **implementado** | estender gateway (etapa 1/4) | entrada / médio | HTTPS público + token de ingestão | — | — | `POST /api/inbound/webhook`, token no cofre + fonte autorizada, fail-closed |
| Calendário / lembretes | ferramenta gated por `check_fn` (etapa 3) | mutadora / médio | provedor externo (decisão abaixo) | — | sim | `kairos_cron` já entrega; falta a **fonte** externa |

#### Calendário / lembretes — decisão de produto em aberto

`kairos_cron` já agenda e entrega; o que falta é a **fonte** (ler o calendário ou
disparar o lembrete). Opções concretas, da menor para a maior dependência
externa — nenhuma é implementada por este documento:

1. **Fonte local (CalDAV próprio ou arquivo `.ics` sincronizado)** — sem OAuth
   público, servidor de vocês (ex.: Radicale/Nextcloud) ou um arquivo empurrado
   pelo pipeline. Ferramenta gated por `check_fn` (etapa 3): só aparece quando a
   fonte existe. Zero conta de terceiros; aprovação por turno nos mutadores.
2. **Google Calendar API** — mais maduro, mas exige conta Google, OAuth de
   credencial, escopo de leitura/escrita e rota de refresh em produção; cada
   ação vira dependência externa autorizada explicitamente (token, conta).
3. **Adiar para P3** — manter só o agendamento por `kairos_cron` enquanto não há
   demanda observada; candidata só avança com uso real (critério 1 de decisão).

Se a opção 1 vencer, o caminho de entrega é a etapa 3 com `check_fn` no toolset:
a ferramenta só entra no request quando o pré-requisito (fonte de calendário
configurada) existe, mantendo o cinto estreito. A decisão pertence à usuária.

### P3 — avaliar só com necessidade concreta

| Candidata | Como entregar | Risco | Por que adiar |
|---|---|---|---|
| Execução de código isolada por sessão | sandbox externa (etapa 3/4) | alto | a sandbox já existe para runtime; abrir para turno comum amplia superfície |
| Ferramentas por MCP de terceiros | MCP (etapa 4) | variável | custo de contexto e confiança; instalar caso a caso |
| Geração de imagem/arquivo | ferramenta nova (etapa 5) | alto | footprint e dependência de terceiro; sem demanda registrada |

## Critérios de decisão

Uma candidata avança quando **todas** as condições valem:

1. Há demanda observada (não hipotética) e o efeito não é alcançável pelas
   etapas 1–4.
2. O footprint de contexto é conhecido e aceitável.
3. O caminho mutador tem aprovação e não amplia bloqueios de delegação.
4. Existe teste de comportamento (não snapshot) provando o caminho real.
5. Encaixa em uma superfície já existente, sem criar uma nova.

## O que este plano não faz

- Não implementa, não agenda e não estima prazo.
- Não promete paridade entre canais (ver `docs/diagnostico-consolidado.md`).
- Não decide por novas dependências externas: cada uma exige autorização
  explícita (token, conta, publicação de endpoint).
