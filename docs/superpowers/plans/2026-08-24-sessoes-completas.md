# Sessões completas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completar a página de Sessões com tags persistidas, paginação, organização reversível e exportação JSON/Markdown.

**Architecture:** A tabela `session_tags` normaliza tags sem alterar as colunas canônicas de `sessions`. O endpoint de listagem monta filtros parametrizados e devolve metadados de paginação; o PATCH atualiza flags e tags em uma transação. A view mantém o detalhe selecionado enquanto recarrega páginas e filtros.

**Tech Stack:** Python 3.11, FastAPI, SQLite, JavaScript ES modules, CSS existente, pytest, Vitest e TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-24-sessoes-completas-design.md`

## Global Constraints

- Nenhuma ação da interface pode apagar fisicamente sessões ou mensagens.
- Tags devem ser normalizadas, limitadas a 20 por sessão e 32 caracteres por tag.
- Toda mudança comportamental começa com teste falhando e termina com teste verde.
- Consultas SQL devem usar parâmetros para valores fornecidos pelo usuário.
- A interface deve preservar escape de conteúdo e navegação por teclado.

### Task 1: Persistência de tags

**Files:**
- Modify: `kairos_state/schema.py` — tabela e índice `session_tags`.
- Modify: `tests/test_schema.py` — existência, FK e idempotência.

**Interfaces:**
- Produces table `session_tags(session_id TEXT, tag TEXT, PRIMARY KEY(session_id, tag))` with cascade.

- [x] Escrever testes falhando para tabela, chave composta e cascade.
- [x] Rodar `uv run pytest tests/test_schema.py -q -k session_tags` e confirmar falha.
- [x] Adicionar DDL idempotente e índice de tag.
- [x] Rodar o teste novamente e confirmar aprovação.

### Task 2: API de tags, filtros e paginação

**Files:**
- Modify: `kairos_web/server.py` — query paginada, `tag_counts`, tags na linha e PATCH.
- Modify: `tests/test_web.py` — contratos de listagem, tags, limites e ocultação.

**Interfaces:**
- `list_sessions(limit=50, offset=0, q="", status="todas", tag="")` devolve `total`, `offset`, `limit`, `has_more` e `tag_counts`.
- `update_session(session_id, payload)` aceita flags booleanas e `tags: list[str]`.

- [x] Adicionar testes de busca paginada, filtro por tag, normalização e ocultação.
- [x] Executar testes direcionados e confirmar RED; após instalar `httpx2`, o `TestClient` foi validado fora do sandbox, que bloqueia o `socketpair` usado pelo asyncio.
- [x] Implementar SQL parametrizado e atualização transacional de tags.
- [x] Executar smoke direcionado e a suíte `tests/test_web.py`, confirmando GREEN.

### Task 3: Cliente e controles da interface

**Files:**
- Modify: `kairos_web/ui/js/api.js` — query params e PATCH.
- Modify: `kairos_web/ui/js/views/sessoes.js` — controles, tags, paginação e ações.
- Modify: `kairos_web/ui/styles/views.css` — layout responsivo.
- Modify: `tests/test_web.py` — contratos estáticos da interface.

- [x] Escrever contratos estáticos para tag, paginação e Markdown e confirmar RED.
- [x] Implementar controles com debounce, estados de carregamento e preservação de seleção.
- [x] Implementar edição de tags e ações reversíveis.
- [x] Implementar exportação Markdown como texto baixável.
- [x] Rodar sintaxe JS, testes estáticos e confirmar GREEN.

### Task 4: Verificação integrada

**Files:**
- Modify: `README.md` — documentar os filtros e o formato de exportação, se a seção existente comportar a adição.

- [x] Rodar `npm test` e `npm run typecheck`.
- [x] Rodar `uv run python -m py_compile kairos_web/server.py` e `git diff --check`.
- [x] Rodar smoke test async com banco temporário para paginação, tags e ocultação.
- [x] Rodar scanner de segurança e registrar limitações conhecidas.
