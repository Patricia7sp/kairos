# Remoção segura de plugins — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Executar `plugins remove NOME --yes`, preservando dados e diagnosticando retiradas incompletas.

**Architecture:** Operações por descritores validam toda a árvore antes e depois da retirada. Um registro privado durável identifica a tentativa; a exclusão parcial preserva evidências e retorna erro. Helpers de árvore e lock serão compartilhados com o plano de skills, sem dependência de plugins ou SQLite.

**Tech Stack:** Python 3.11, biblioteca padrão, Linux com mount ID observado e rename sem substituição, unittest executado por pytest/uv.

**Spec:** `docs/superpowers/specs/2026-10-10-remocao-segura-design.md`, aprovada em 2026-10-10.

## Global Constraints

- Sem `--yes`, recusar antes de criar lock ou modificar arquivos; exit 77.
- Plugins: `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, recusando também `..`.
- Inventário: 256 MiB, 10000 entradas, profundidade 32; leitura em streaming.
- Recusar symlinks, hardlinks de arquivos, arquivos especiais, mounts/bind mounts e troca de identidade; falta de garantia: exit 69.
- Privados: arquivos 0600, diretórios 0700. Nenhum import de plugin, hook, Git ou subprocesso de plugin.
- Preservar `plugin-data/`, configuração, demais instalações e fontes bundled; sem reinício implícito.
- CI completa local e remota verde antes do squash merge; backup com restauração conferida antes do deploy.

## Review Focus

- `.git` como arquivo aponta para fora: remover apenas o arquivo; teste na tarefa 2.
- Processo morre depois do último unlink e antes do resultado: não inferir sucesso pela ausência; teste na tarefa 2.
- Bind mount no mesmo dispositivo: recusar pela identidade de montagem, não por `st_dev`; teste na tarefa 1.
- Outro processo substitui um descendente entre inventário e cleanup: preservar o substituto e informar resíduo; teste na tarefa 2.
- Plugin tem nome interno diferente e manifesto inválido: selecionar pelo filho imediato sem importar código; teste na tarefa 2.

## Coordenação e entregas

Este plano constitui uma entrega independente. O plano `2026-10-10-remocao-skills.md` depende somente dos helpers da tarefa 1; pode começar seu journal em paralelo depois de fixadas as interfaces. Um agente implementa helpers/plugins, outro implementa journal de skills, e o coordenador integra CLI/sync. Nenhum agente edita o mesmo arquivo em paralelo. Cada tarefa recebe revisão independente de contrato e qualidade, com correções antes da dependente. Método multiagentes já escolhido pelo usuário; implementação aguarda revisão dos planos.

### Tarefa 1: Árvore segura e lock reutilizável

**Files:** Create `kairos_filesystem/__init__.py`, `kairos_filesystem/contract.py`, `kairos_filesystem/descriptors.py`, `kairos_filesystem/tree.py`, `kairos_filesystem/lock.py`, `tests/test_tree_io.py`; Modify `kairos_skills/mutation_io.py`, `kairos_skills/mutation_lock.py`; regressão `tests/test_skill_mutation_io.py`. O pacote genérico não importa skills/plugins/ledger.

**Interfaces:** Em `contract.py`, produzir `FilesystemError(kind: str, message: str)` e dataclasses congeladas `TreeLimits(total_bytes: int, file_bytes: int, entries: int, depth: int)`, `TreeEntry(path: str, kind: str, mode: int, size: int, sha256: str | None, device: int, inode: int, mount_id: int)` e `TreeCapture(entries: tuple[TreeEntry, ...], contents: dict[str, bytes] | None)`. Caminho vazio representa a raiz; `kind` é `directory|file`. Em `tree.py`, produzir `capture_tree(parent_fd: int, name: str, *, limits: TreeLimits, include_contents: bool) -> TreeCapture`, `verify_tree(parent_fd: int, name: str, expected: TreeCapture) -> None`, `restore_tree(parent_fd: int, name: str, snapshot: TreeCapture) -> TreeCapture`, `delete_verified_tree(parent_fd: int, name: str, expected: TreeCapture) -> None`. Em `lock.py`, produzir context manager `filesystem_lock(home: Path, name: str, *, timeout: float = 5.0)`; `skill_mutation_lock` conserva assinatura e nome `.skills-write.lock`. Extrair `open_directory/open_fd/check_chain/same_entry/rename_no_replace` de `mutation_io` para `descriptors.py`, preservando assinaturas; os adaptadores antigos traduzem `FilesystemError` para `SkillMutationError`.

- [ ] Preparar ambiente isolado com `uv sync --python 3.11 --extra dev`; conferir `uv run python --version` como Python 3.11. Rodar `uv run pytest -q tests/test_skill_mutation_io.py tests/test_skill_mutation_sync.py` para estabelecer a base dos helpers extraídos. Nenhuma nova dependência prevista; se necessária, justificar e atualizar uv.lock junto do pyproject.
- [ ] Escrever testes `TreeIOTests` usando TemporaryDirectory e operações reais: captura binário/oculto/diretório vazio, restauração com modos ordinários e novo inode, ausência de conteúdo em inventário streaming, recusa link/hardlink/FIFO, limite inclusivo e limite+1 para bytes/entradas/profundidade. Assertivas: bytes e modos restaurados iguais; árvores externas intactas; destino ocupado não alterado.

  Assertivas centrais após capturar `before` e restaurar/capturar `after`:

  ```python
  self.assertEqual(before.contents, after.contents)
  self.assertEqual([(e.path, e.mode) for e in before.entries],
                   [(e.path, e.mode) for e in after.entries])
  self.assertNotEqual((before.entries[0].device, before.entries[0].inode),
                      (after.entries[0].device, after.entries[0].inode))
  ```

- [ ] Acrescentar `test_bind_mount_identity_rejected`, `test_mount_observation_unavailable`, `test_replaced_child_preserved`, `test_acl_xattr_and_special_modes_rejected`, `test_lock_deadline_and_unsafe_entry`. Observar mount ID por `/proc/self/fdinfo`; testar classificação com identidade observada injetada e exercitar bind mount real no gate Docker privilegiado quando disponível. Uma recusa por falta de privilégio não conta como teste real de bind mount.
- [ ] Rodar `uv run pytest -q tests/test_tree_io.py`; exigir falha ligada às interfaces ausentes ou ao contrato não implementado, sem alterar expectativas para esconder o problema.
- [ ] Implementar os contratos acima. Hash SHA-256 e leitura de 64 KiB; verificar limites antes de acumular bytes, mount ID do alvo contra o pai e de cada descendente contra a raiz, `st_nlink`, metadados antes/depois e cadeia por descritores. Modos especiais/ACL/xattrs não vazios recusados. Timestamps detectam edição durante leitura; ctime pré-rename não é identidade congelada. Restaurar com criação exclusiva, validar manifesto antes de I/O e fsync arquivos/diretórios/pais. Cleanup reconfere identidade imediatamente antes de cada ação e para na divergência. Extrair somente o mecanismo de lock, mantendo prazo total máximo 5 s e proteção entre threads/processos.
- [ ] Rodar `uv run pytest -q tests/test_tree_io.py tests/test_skill_mutation_io.py tests/test_skill_mutation_service.py tests/test_skill_mutation_sync.py`; exigir todos os testes elegíveis passando e nenhuma escrita fora das árvores autorizadas.
- [ ] Commit: `feat(filesystem): valida árvores e locks para remoção segura`.

### Tarefa 2: Serviço e CLI de remoção de plugins

**Files:** Create `kairos_plugins/removal.py`, `kairos_plugins/removal_records.py`, `kairos_cli/plugin_mutations.py`, `tests/test_plugin_removal.py`, `tests/test_cli_plugin_removal.py`; Modify `kairos_cli/main.py`, `kairos_cli/handlers.py`, `docs/decisoes.md`. Regressão `tests/test_plugins.py`, `tests/test_plugin_hooks.py`. `removal_records.py` é responsável pela validação/leitura/escrita durável das provas privadas.

**Interfaces:** Consumir tarefa 1. Produzir `PluginRemovalResult(name: str, operation_id: str, removed: bool, requires_restart: bool = True)` e `PluginRemovalError(kind: str, message: str, *, operation_id: str | None = None, retired: bool = False)`. `remove_plugin(home: Path, name: str, *, confirmed: bool) -> PluginRemovalResult`; `run_plugin_removal(home: Path, args: argparse.Namespace) -> int`. Persistir `<home>/.plugins-retired/ID/metadata.json`, alvo `ID/tree` e resultado exclusivo `result.json`; metadata versionada contém ID UUID, nome e `TreeCapture.entries`, sem bytes/conteúdo ou caminhos externos.

Em `removal_records.py`, definir `write_metadata(operation_fd: int, name: str, operation_id: str, capture: TreeCapture) -> None`, `read_metadata(operation_fd: int) -> tuple[str, str, TreeCapture]`, `write_terminal(operation_fd: int, *, state: str) -> None`, `read_terminal(operation_fd: int) -> str | None`. Estado terminal permitido `removed|aborted`; leitura valida ID contra diretório, nome, limites/inventário e versão antes de confiar. Escritas exclusivas com fsync, sem substituir provas existentes.

- [ ] Escrever `test_remove_broken_plugin_without_import`, `test_preserves_data_config_other_and_bundle`, `test_git_file_does_not_follow_pointer`: criar plugin cujo módulo geraria arquivo sentinela se importado; manifesto quebrado/nome interno diferente; exigir alvo ausente, sentinela ausente e preservação byte a byte dos demais dados.

  Com `target`, `sentinel` e `data` criados no home temporário:

  ```python
  result = remove_plugin(home, target.name, confirmed=True)
  self.assertTrue(result.removed)
  self.assertFalse(target.exists())
  self.assertFalse(sentinel.exists())
  self.assertEqual(data.read_bytes(), b"dados preservados")
  ```

- [ ] Escrever `test_confirmation_before_any_io`, `test_bad_name`, `test_partial_cleanup_reports_retired_id`, `test_replaced_descendant_preserved`, `test_absent_name_without_proof_is_error`. Testar códigos 77/2/1, JSON de sucesso apenas com efeito concluído, stderr sanitizado e resíduo conhecido preservado.
- [ ] Escrever testes em subprocesso `test_death_before_rename`, `test_death_after_rename`, `test_death_during_cleanup`, `test_death_before_terminal_result`. Interromper fronteiras reais com sincronização do subprocesso; nova chamada confirmada consulta metadata conhecida: aborta tentativa ainda instalada, informa resíduo ou conflito, e não retoma cleanup automaticamente. Ausência de ambas sem resultado terminal nunca vira sucesso.
- [ ] Rodar `uv run pytest -q tests/test_plugin_removal.py tests/test_cli_plugin_removal.py`; exigir falhas de comportamento/interface antes da implementação.
- [ ] Implementar validação antes de I/O; lock `.plugins-write.lock`; captura streaming; metadata exclusiva durável antes de rename; rename para `ID/tree`, reconferência e cleanup por descritores; resultado terminal uma vez. Não limpar registros desconhecidos. Traduzir erro parcial para stderr estruturado com `retirado: true` e `residuo_id`; sucesso JSON `nome`, `removido`, `requer_reinicio: true`. Ausência sem prova é erro; repetição com resultado terminal íntegro e sem nova instalação pode devolver evidência histórica. Mover imports de `cmd_plugins` para dentro do ramo list; remove não chama loader/configuração.
- [ ] Integrar parser `plugins remove NOME --yes`, manter outros subcomandos e JSON global existentes. Registrar limitação de callbacks ativos e evidências de erro parcial na ajuda/decisões.
- [ ] Rodar `uv run pytest -q tests/test_plugin_removal.py tests/test_cli_plugin_removal.py tests/test_plugins.py tests/test_plugin_hooks.py tests/test_tree_io.py`; exigir todos os testes elegíveis passando. Executar gates e entrega abaixo antes de merge.
- [ ] Commit: `feat(plugins): remove instalações preservando dados e resíduos`.

## Gate de entrega de cada PR

- [ ] Revisão independente da branch inteira; `git diff --check`; executar `scripts/ci.sh` completo, incluindo imagem real, stub, integrações opt-in sem credenciais e teste real de bind mount. Registrar o que foi executado e qualquer indisponibilidade; não contar skips como cobertura.
- [ ] Publicar PR com título/descrição em português; esperar todos os jobs de verificação verdes. Corrigir falhas antes do squash merge. Não afirmar suporte aos caminhos `runtime_live` não exercitados.
- [ ] Conferir backup consistente da produção com restauração e hashes, saúde e ausência de operações pendentes; squash merge; inspecionar `HEAD~1..HEAD` para remoções acidentais; acompanhar Komodo/deploy na main.
- [ ] Smoke HTTP e CLI na imagem publicada com home temporário: plugin quebrado removido, dados preservados, recusa sem confirmação e saída parcial honesta; verificar versão/fontes instaladas e registrar evidências. Nenhum dado real de plugin é removido no smoke.
