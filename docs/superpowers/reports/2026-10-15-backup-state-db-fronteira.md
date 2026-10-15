# Correção da fronteira de backup SQLite

Base: `d730822`, branch `fix/backup-state-db-fronteira`.
Spec: [fronteira do backup](../specs/2026-10-14-backup-state-db-fronteira-design.md).

## Implementação e limites

A entrada `state.db` é observada sem seguir links e mantida por descritor.
Cada banco arquivado vem da API SQLite de backup, incluindo WAL confirmado.
Identidade do banco e cadeia do home são revalidadas durante a captura.
O destino solicitado só é substituído após concluir o arquivo temporário;
falhas preservam um destino anterior e tentam remover o parcial desta execução.
Falha de limpeza gera logging estruturado e mantém retorno de erro.

`--output` não pode substituir o banco ou seus sidecars no próprio home.
Após decisão pela ausência, mudanças nos timestamps do diretório recusam
conservadoramente a captura; isso inclui alterações de namespace alheias ao
banco. Não há snapshot conjunto de todos os arquivos nem garantia contra
manipulação hostil pelo mesmo UID.

## Evidências desta retomada

- RED na base: execução isolada dos dois testes de symlink/criação concorrente
  com o módulo de backup do commit base: 2 falhas esperadas. A corrida também
  pode omitir o banco inteiro quando ele surge após a enumeração.
  Log local: `/tmp/kairos-backup-baseline-red.log`.
- RED→GREEN adicional: destino WAL retornava sucesso e era substituído;
  agora banco/WAL/SHM/journal são recusados antes da captura.
- Revisão independente somente leitura por GPT-6 Luna (high): encontrou
  criação seguida de remoção após a decisão ausente entre verificações.
  Reprodução falhou antes da correção e passou após observação conservadora
  de mtime/ctime do diretório. Sem outros achados concretos reportados.
- `.venv/bin/python -m pytest -q tests/test_cli_backup_boundary.py tests/test_skill_adoption_backup.py`:
  **25 passed, 10 subtests passed**, 26,36 s, exit 0.
  Log local: `/tmp/kairos-backup-final-tests.log`.
- `.venv/bin/ruff check .`: exit 0; `.venv/bin/ruff format --check .`:
  647 arquivos, exit 0; `git diff --check`: exit 0.
- `umask 0022 && scripts/ci.sh`: em andamento;
  log local `/tmp/kairos-backup-boundary-ci-20261015.log`.

CI remoto e merge ainda não atestados. Não há declaração de deploy.

## Diagnóstico dos gates gerais

A primeira execução integral terminou com exit 1: 3.846 testes passaram,
2 falharam, 16 foram pulados, 31 deselecionados e 6.317 subtestes passaram.
As falhas foram `test_real_binary_initialize_start_and_resume_compatibility_probe`
e `test_pinned_codex_retains_lock_descriptor_after_abrupt_host_death`: o PATH
selecionava o Codex do host, incompatível com o pin do projeto. Com o diretório
`0.154.0-x86_64-unknown-linux-musl/bin` no início do PATH, os dois testes
passaram (3,07 s), sem alterações de runtime. Log:
`/tmp/kairos-backup-runtime-pinned.log`.

Ruff, shellcheck, recall (20 testes), `uv lock --check`, hadolint e
`docker build --check` passaram na execução integral. Os três frontends
foram inicialmente pulados por falta de node_modules. Depois de comparar
os hashes SHA-256 dos três lockfiles com os do checkout principal (iguais),
foram usados os node_modules já instalados por links locais ignorados pelo
Git. TypeScript e Vitest passaram: ui-tui 17, web 204, desktop 18 testes
(total 239); log `/tmp/kairos-backup-frontends.log`.

O primeiro build real concluiu, mas sua integração falhou por permissões
restritivas do checkout (fontes 0600/diretórios 0700): UID 10000 não conseguia
ler os pacotes copiados. Os arquivos versionados foram normalizados para
0644/0755 conforme o índice Git, com diretórios 0755; nenhum arquivo de
credencial foi selecionado. O build repetido gerou a imagem
`sha256:106ca1f2cebd5130e387e3ba8aa2d4dc157eca357332d0d0c4c25f9492d66dd7`.
A integração repetida passou 23 testes e 23 subtestes, pulando somente os
7 testes que exigem `kairos:stub`. A stub foi construída para a validação
final sem esses skips. Logs: `/tmp/kairos-backup-image-rebuild.log`,
`/tmp/kairos-backup-image-tests.log`, `/tmp/kairos-backup-stub-build.log`.

Ambiente: Python 3.11.16, Ruff 0.16.4, uv 0.12.5.
As falhas iniciais permanecem registradas; o exit 1 inicial não foi
reinterpretado como execução integral verde.

Validação completa da imagem real + stub: **30 passed, 26 subtests passed**,
182,22 s, sem skips, exit 0. Log `/tmp/kairos-backup-real-image-final.log`.
Uma nova execução integral de `scripts/ci.sh` com PATH pinado e todos os
pré-requisitos disponíveis foi iniciada; log
`/tmp/kairos-backup-boundary-ci-final.log`. Resultado ainda pendente neste commit.
