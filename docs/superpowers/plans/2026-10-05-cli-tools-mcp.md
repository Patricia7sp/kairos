# Ferramentas na CLI e despacho MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Executar ferramentas do chat pela CLI com consentimento e corrigir o despacho MCP pelo serviço compartilhado.

**Architecture:** A CLI traduz opções, eventos e decisões humanas; o InteractionService continua responsável por execução e autorização. Os nomes autorizados vêm dos schemas apresentados ao modelo, congelados por turno.

**Tech Stack:** Python 3.11, argparse, asyncio, SQLite, pytest.

**Spec:** Diagnóstico e roadmap aceitos nesta conversa; decisões D-PC.1 e D-RT.4 em `docs/decisoes.md`; referências `hermes-cli/requirements.md` e `mcp/requirements.md` em `_reversa_sdd`.

## Global Constraints

- Sem efeito real é pior que ausência; fail closed.
- Nenhuma nova ferramenta core, dependência ou mudança de credenciais.
- Ferramentas gerais exigem `--tools`; busca continua opt-in independente.
- Aprovação humana por chamada. Sem TTY ou em JSON, recusar mutações imediatamente.
- Sessões Codex recusam `--tools`, preservando seu protocolo próprio.
- Autenticação, workspace, scheduler e skills pertencem aos próximos recortes da etapa 1/roadmap.

## Review Focus

- Ferramenta omitida por indisponibilidade nunca executa uma chamada inventada.
- Mudança do registry no meio do turno não amplia o conjunto de schemas.
- Negação, EOF, JSON e pipe não executam mutações nem ficam aguardando 90 segundos.
- Cancelamento e expiração enquanto o terminal aguarda resposta não bloqueiam o event loop.
- Saída JSON continua parseável; erro de ferramenta/recusa resulta em status não zero.

## Task 1: Schemas e autorização MCP

**Files:** `kairos_integration/interaction_service.py`, `kairos_integration/chat_tools.py`, `kairos_web/tools_api.py`, `tests/test_chat_tool_approval.py`, `tests/test_tools_inventory.py`.

**Interfaces:** Consome `AdapterRequest.tools` e `registry`; produz conjunto imutável de nomes autorizados e inventário coerente.

- [x] Escrever testes de chamada MCP aprovada com efeito real, omissão por requisito e congelamento entre rodadas.
- [x] Rodar os testes e observar falha de autorização do MCP.
- [x] Derivar autorização dos schemas iniciais e reutilizá-los entre rodadas; alinhar inventário MCP.
- [x] Rodar `uv run pytest -q tests/test_chat_tool_approval.py tests/test_tools_inventory.py tests/test_mcp_tool_integration.py`; esperado: todos passam.

## Task 2: Tools e aprovações na CLI

**Files:** `kairos_cli/main.py`, `kairos_cli/handlers.py`, `kairos_cli/chat.py`, novo `kairos_cli/tool_approval.py`, `tests/test_cli_chat.py`, novo `tests/test_cli_tools.py`, `docs/guia-cli-comandos.md`.

**Interfaces:** `run_chat(..., tools: bool = False)` repassa ao envelope. O terminal resolve `tool_approval_request` por `decide_tool_approval`, sem executor paralelo ao core.

- [x] Escrever testes de CLI com serviço real, provedor controlado e ferramentas que leem/escrevem arquivos temporários.
- [x] Demonstrar falha antes da opção/ponte existir.
- [x] Implementar `--tools`, renderização e aprovação assíncrona com recusa segura; rejeitar em sessão runtime antes de compor recursos.
- [x] Rodar `uv run pytest -q tests/test_cli_chat.py tests/test_cli_tools.py tests/test_chat_tool_approval.py`; esperado: todos passam.
- [x] Documentar exemplos e limites e executar `scripts/ci.sh --fast`, incluindo a suíte unitária geral. Registrar falhas preexistentes e exclusões.
- [x] Revisar o diff separadamente. Entregar alterações locais revisáveis, sem merge/deploy.

## Registro de execução

Baseline: checkout limpo `c2a69b1`; suíte completa já executada na análise: 3039 passaram, duas falhas de versão Codex (0.160.0 instalado; 0.154.0 exigido).

Execução nesta sessão, com worktree isolada. O aceite do usuário autoriza iniciar a implementação; não haverá novo gate de aprovação do plano. Commits e integração serão separados da implementação local.

TDD: três regressões do despacho reproduzidas antes da correção; teste MCP executa o servidor stdio de fixture pelo pipe. Doze cenários CLI/inventário falharam antes da implementação. Depois da implementação e do caso de EOF, 72 testes direcionados passaram.

Ruling: escopo inicial opt-in com `--tools` e recusa em JSON/pipe — preserva o comportamento anterior e a aprovação por chamada; automações mutadoras não interativas ainda exigem o futuro contrato de autorização.

Ruling: worktree em `.worktrees/tools-mcp` — o validador MCP existente rejeita `/c` até em caminhos de arquivo; renomear o diretório evita misturar essa correção com a entrega. O registro Git da worktree foi reparado após bloqueio parcial do sandbox.

Revisão inicial: leitura própria em passagem separada, sem ferramenta de subagente disponível naquela etapa. A revisão cobre paridade, omissão por indisponibilidade, mudanças entre rodadas, efeitos em arquivos e protocolo de saída.

Diagnóstico durante a validação geral: `test_unavailable_sandbox_never_falls_back_to_host` criava um `pytest.MonkeyPatch` sem restaurá-lo. Reprodução isolada junto dos novos testes: quatro falhas por executor contaminado. Corrigido usando a fixture `monkeypatch`, sem alterar o comportamento de produção. Os 88 testes direcionados, incluindo sandbox, passaram depois da correção.

Validação final (`scripts/ci.sh --fast`): 3055 passaram, 16 pulados, 26 deselecionados e 5840 subtestes passaram. Persistem somente as duas falhas da baseline: `test_real_binary_initialize_start_and_resume_compatibility_probe` e `test_pinned_codex_retains_lock_descriptor_after_abrupt_host_death`, ambas por incompatibilidade do Codex instalado. Ruff check/format, shellcheck, recall (20 testes), lock e TypeScript passaram. Frontends: TUI 17, Web 204 e desktop 18 testes passaram. `git diff --check` e help do executável passaram. Log: `/tmp/kairos-tools-mcp-ci.log`.

Limites da verificação: `--fast` não executou imagem/integração Docker; `runtime_live` não foi executado. Provedor controlado nos testes, efeitos reais em arquivos temporários e servidor MCP stdio de fixture; nenhuma publicação externa ou chamada com credenciais reais. O gate não está verde e não houve commit, merge nem deploy. Checkout original preservado; alterações na branch `feat/cli-tools-mcp`, worktree `.worktrees/tools-mcp`.

Recorte subsequente de autenticação CLI/Web implementado e verificado:
[registro e limites](../../cli-auth-progress.md). Restrição de workspace
continua pendente.

Preparação do PR autorizada pelo usuário: branch reaplicada sobre a `main`,
preservando separado o PR #89. Revisão independente posterior encerrou cinco
apontamentos com regressões reais: erro de bash, primeira escrita parcial
no keyring, proteção contra segredos em todo o pool, migração do formato
`key` e colisões de referências. Registro de testes e limites no documento
de autenticação acima. Squash-merge condicionado ao CI local e remoto verde.
