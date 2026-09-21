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
`kairos_tools/memory.py`, `kairos_tools/git.py`, `kairos_tools/calendar.py`):

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
| `git` | status/diff/log/commit (toolset gated por repositório) | sim (nos mutadores) |
| `calendar` | agenda local `.ics` (toolset gated por `calendar.source`) | sim (`add`/`rm`) |

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
| ~~Calendário~~ **implementado — fonte local (opção 1)** | ferramenta gated por toolset + aprovação por subcomando (etapa 3) | mutadora / médio-baixo (sem rede externa) | arquivo `.ics` / diretório local (`calendar.source`) | — | sim (em `add`/`rm`) | `kairos_cron` entrega; **lembretes proativos** ficam como evolução à espera de uso |

#### Calendário / lembretes — decisão fechada: fonte local (opção 1)

Decisão da usuária em 2026-09-21: **opção 1 — fonte local**. A ferramenta
`calendar` foi entregue via PR #77 (plano datado em
`docs/superpowers/plans/2026-09-21-calendario-icnfonte-local.md`): lê e altera a
agenda de um arquivo `.ics` (ou diretório de `.ics`, somente leitura) apontado
por `calendar.source` em `<home>/config.yaml`, com toolset gated — a ferramenta
só entra no request quando a fonte existe, mantendo o cinto estreito. Mutadores
(`add`/`rm`) exigem aprovação por turno; `today`/`range` fluem.

Escopo honesto do v1: RRULE é lido e **marcado, não expandido**; datetimes
flutuantes assumem fuso local do sistema, sinalizado por evento; arquivo
corrompido é erro nomeado fail-closed (nenhum parcial silencioso); leituras
exigem `limite` explícito (1–200). Zero conta de terceiros, zero rede.

Fica em aberto só a outra metade da candidata — **lembretes proativos** (entregar
avisos pelo `kairos_cron` a partir do calendário). Ela não avança por plano:
regride apenas com uso real observado (critério 1 de decisão). As opções 2
(Google Calendar API) e 3 (adiar) foram descartadas ao escolher a fonte local.

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
