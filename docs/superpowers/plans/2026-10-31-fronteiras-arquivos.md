# Triagem das fronteiras de arquivos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para executar sequencialmente. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Classificar três fluxos Sonar com evidência comportamental e corrigir as falhas reproduzidas no carregamento de limites.

**Architecture:** Preservar os caminhos autorizados existentes. O loader abre o arquivo uma vez, sem bloqueio por FIFO, valida o descritor regular e produz diagnósticos sem conteúdo da configuração. Triagem de conteúdo versus caminho e de metadados versus home usa os serviços reais.

**Tech Stack:** Python 3.11, stdlib, PyYAML e pytest já fixados; sem dependências novas.

**Spec:** `docs/decisoes.md`, D-RT.3 e D-CLI.WS; `../hermes-agent/_reversa_sdd/tools/requirements.md` (referência relativa ao checkout principal, limites da execução). Correção do contrato existente, dentro da triagem autorizada nesta conversa.

## Global Constraints

- Configuração ausente conserva os padrões existentes; configuração presente inválida recusa.
- Aliases de configuração para arquivo regular continuam aceitos; não transformar seleção explícita do home pelo operador em escape de modelo.
- Nenhum conteúdo, chave arbitrária ou caminho do arquivo aparece nos diagnósticos e tracebacks normais de validação.
- Não criar promessa de isolamento contra outro processo do mesmo UID ou contra todas as trocas dos pais do home.
- Curadoria adiada; nenhuma classificação remota Sonar, alteração de produção ou merge integra este recorte.
- Reutilizar o CI da main `08b213f` como baseline integral; repetir os checks afetados e executar CI completo sobre o estado final.

## Review Focus

- Diretório, FIFO e link quebrado no lugar de config.yaml não podem virar ausência/default nem bloquear a execução.
- Erros de YAML/UTF-8 e chaves desconhecidas não podem expor valores ou trechos do arquivo.
- Um alias válido para configuração regular deve preservar os limites selecionados.
- Um caminho escrito no conteúdo de um arquivo não pode substituir o destino da edição.
- Um campo `path` de um agendamento não pode substituir o caminho do JobStore nem o arquivo de lock.

### Task 1: Loader fail-closed e diagnósticos privados

**Files:** `kairos_runtime/execution.py`, `tests/test_execution.py`.

**Interfaces:** `load_execution_limits(home: Path) -> ExecutionLimits` e `parse_execution_limits(raw: object) -> ExecutionLimits`, sem mudança de assinatura. Falhas continuam `ValueError` nomeadas.

- [x] Escrever `test_load_config_presente_nao_regular_recusa` para diretório, link para diretório, link quebrado e ciclo; esperar ValueError.
- [x] Escrever `test_load_fifo_recusa_sem_bloquear`, em subprocesso com timeout, e `test_load_diagnostico_nao_expoe_config` para YAML inválido, chave desconhecida e UTF-8 inválido; verificar mensagem e traceback sem canário.
- [x] Escrever positivo de alias regular e integração `ChatBashSandbox` recusando configuração presente inválida.
- [x] Executar os novos testes e observar RED antes de alterar produção.
- [x] Abrir com descritor não bloqueante, validar arquivo regular antes da leitura, fechar em todos os desfechos e usar diagnóstico seguro com causa suprimida. Remover eco da chave arbitrária do parser.
- [x] Executar `tests/test_execution.py` e `tests/test_chat_sandbox.py`; esperar aprovação.
- [x] Commit: `fix(runtime): recusa configurações inválidas e protege diagnósticos`.

### Task 2: Provas dos fluxos e fechamento da triagem

**Files:** `tests/test_security_file_boundaries.py`, relatório em `docs/superpowers/reports/2026-10-31-fronteiras-arquivos.md`, checkpoints de continuidade.

**Interfaces:** Consome o loader corrigido e os contratos existentes de `ToolRegistry.dispatch` e `JobStore.get`. Não muda políticas de workspace ou autoria.

- [x] Exercitar edição com conteúdo contendo caminhos externos, com e sem workspace explícito, e comprovar destino original e sentinela externa preservados.
- [x] Exercitar JobStore reaberto a partir de JSON com campo `path` externo, NUL ou link cíclico; comprovar leitura do job correto e ausência de efeito no alvo externo.
- [x] Classificar S2083, S8707 e S6549 separadamente, distinguindo os fluxos não reproduzidos das falhas associadas efetivamente corrigidas.
- [x] Atualizar o fechamento do #125 e registrar curadoria como decisão posterior.
- [ ] Revisão independente da branch, CI local completo e preparação de PR em rascunho. Informar o estado real do CI/Sonar remoto, sem antecipar aprovação.

**Validation:** testes direcionados e `scripts/ci.sh` completo com Codex fixado, sem exportação Sonar local nem provedores reais.
