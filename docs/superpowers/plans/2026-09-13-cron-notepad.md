# Notepad durável por job de agendamento

Continuação autorizada do inventário funcional: cada job tem um KV persistente
entre as execuções agendadas (cursors, watermarks, listas), injetado no prompt e
escrito pela CLI — sem tool de modelo. Base: PR #33 integrada (`5528dc4`).
Contrato espelhado do legado (`cron/notepad.py` da base `hermes-agent`).

## Contrato

- Armazenamento no `state.db` do Kairos: migração **v4** (tabela `cron_notepad`,
  chave primária `(job_id, key)`, índice por job). `SCHEMA_VERSION` sobe de 3
  para 4; a tabela entra em `CANONICAL_TABLES`; o reparo canônico nunca a toca
  por linhas.
- Injeção: antes do turno, o bloco é anexado ao início do prompt com o cabeçalho
  literal `## Job notepad (persistent across runs)` e a instrução de uso via CLI
  (`kairos cron notepad <id> set <key> <value>`, além de get/delete/list).
  Notepad vazio injeta **string vazia** — prompts de jobs que não usam o bloco
  permanecem byte-idênticos. Excluir o job limpa o notepad de forma best-effort,
  nunca bloqueando a exclusão; volume fresco sem `state.db` não materializa banco.
- Limites documentados, com `ValueError` e ausência de escrita parcial:
  `MAX_KEY_CHARS` 128, `MAX_VALUE_BYTES` 16 KiB por valor (UTF-8),
  `MAX_JOB_TOTAL_BYTES` 64 KiB por job somando chave+valor. O bloco é chaveado
  por id e não exige que o job exista no momento da escrita.
- Superfície: CLI `cron notepad <id> [get|set|delete|list] [key] [value]`
  (padrão `list`); API `GET /api/cron/jobs/{id}/notepad`, `GET/PUT/DELETE
  /api/cron/jobs/{id}/notepad/{key}` e `DELETE .../notepad` (limpar); painel
  mostra o bloco de notas por job com salvar/remover anotações.

## Implementação e validação

1. Backend TDD: persistência e upsert, isolamento entre jobs, limites sem
   escrita parcial, render vazio/cabeçalho literal, injeção no scheduler com e
   sem notepad, remoção limpa o bloco, CLI real de ponta a ponta.
2. API e UI compartilham contrato; vitest do painel (salvar e remover anotação);
   atualizações de `test_cli_surface` (subcomando `notepad`) e
   `test_local_diagnostics` para `SCHEMA_VERSION` 4.
3. Sweep completo (apenas as duas reprovações esperadas do Codex), `ci.sh
   --fast`, atualização de manual/progresso e plano antes da publicação. Aceite
   não usa a conta nem os prompts da usuária.