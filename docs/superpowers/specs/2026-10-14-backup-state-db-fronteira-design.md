# Backup: fronteira do home e consistência de state.db sob corrida

Status: especificação aprovada pelo usuário em 2026-10-14, incluindo a
clarificação de aparecimento do banco antes do ponto único de snapshot.
Base: `main` em `d730822` (merge da #104). Correção independente do B1 — branch
própria a partir da `main`, sem depender do worktree `removal_design`.

## Objetivo e origem

Fechar duas falhas apontadas na revisão pós-merge da PR #104, ainda presentes
em `kairos_cli/backup.py`. Ambas permitem que `kairos backup` reporte sucesso
num arquivo que viola a fronteira do home ou que não restaura o que promete.

Rastreabilidade: comentários da revisão
[#104 r4267112756](https://github.com/Patricia7sp/kairos/pull/104#discussion_r4267112756)
(symlink) e
[#104 r4267112766](https://github.com/Patricia7sp/kairos/pull/104#discussion_r4267112766)
(corrida WAL); código em `kairos_cli/backup.py:14-41`; princípio
"sem efeito real é pior que ausência" e "fail closed" em `AGENTS.md`;
garantia de backup registrada em `docs/decisoes.md`, D-CLI.SKILL.PROVENANCE.

Não há evidência de perda ou vazamento ocorrido; ambas as condições são
latentes (symlink deliberado em `state.db` ou corrida estreita de criação
durante o backup).

## Problemas confirmados

**P1 — symlink em `state.db` cruza a fronteira do home.**
`database.resolve()` (`backup.py:23`) segue o link: um `state.db` que aponte
para fora do home é lido e arquivado como `state.db` regular com sucesso
reportado. O laço do tar não filtra o caso: symlink-arquivo passa no filtro
de `is_file()` (`backup.py:36`) e é o único cujo **conteúdo** é copiado de
fora (outros arquivos-symlink entram como links pelo `tarfile`). O mesmo
caminho abrindo um FIFO bloquearia o processo.

**P2 — corrida entre a checagem de existência e o laço perde o WAL.**
Se `state.db` não existe no instante de `database.exists()` (`backup.py:20`)
mas é criado antes do laço (`backup.py:38`), `snapshot` não existe e o
arquivo **vivo** entra no tar. `state.db-wal` é excluído incondicionalmente
(`backup.py:34`); páginas confirmadas residentes no WAL ficam de fora e o
backup retorna 0. Restauração desse arquivo é silenciosamente incompleta.

## Contrato corrigido

Contratos observáveis de `_archive_home` / `run_backup`, limitados às
verificações discretas descritas abaixo. O descritor não vincula o handle
interno do SQLite ao inode observado; a corrida ABA residual não é coberta.

1. **`state.db` regular, sem seguir links.** A entrada `<home>/state.db` é
   validada por `lstat` (sem resolver). Ausente durante toda a observação →
   o tar segue sem `state.db` e o backup pode concluir (home novo é legítimo).
   Presente e não-regular em uma verificação — symlink, FIFO, socket, device —
   → **recusa integral**: exit 1, stderr com mensagem própria e sanitizada
   (sem caminho do alvo, sem conteúdo), sem envelope JSON de sucesso, sem
   nenhuma cópia do alvo em nenhum arquivo. Symlink é recusado
   independentemente do alvo apontar para dentro ou fora do home; não há
   decisão por resolução de caminho.
2. **Toda entrada `state.db` no tar é um snapshot SQLite consistente.**
   Obtido pela API `backup()` do `sqlite3` no ponto em que o laço alcança a
   entrada, incluindo páginas confirmadas residentes no WAL. O arquivo vivo
   nunca entra no tar no lugar do snapshot.
3. **Transição observada depois do ponto de snapshot é recusa.** Um banco que surge
   antes desse ponto pode entrar no backup pela API SQLite, desde que seja
   regular e estável em identidade nas verificações da captura. Se já havia banco na
   observação inicial, desaparecimento ou troca de identidade observados recusam. Depois
   da decisão no ponto único, aparecimento de banco antes ausente,
   desaparecimento ou troca detectados até o encerramento do backup recusam por inteiro
   (exit 1). Escritas SQLite e checkpoints no mesmo inode continuam permitidos.
   O arquivo vivo nunca substitui o snapshot, mesmo se o banco só aparecer
   depois da enumeração inicial dos arquivos.
4. **Sidecars nunca entram no tar.** `state.db-wal`, `state.db-shm` e
   `state.db-journal` continuam excluídos incondicionalmente; com o item 2,
   seu conteúdo já está refletido no snapshot.
5. **Diagnóstico sanitizado.** A recusa de fronteira usa mensagem própria e
   distinta de falha de leitura/corrupção (opera consegue distinguir
   configuração inválida de banco ilegível), sem expor caminho privado,
   target de symlink nem conteúdo — mesmo padrão do teste de banco ilegível
   já existente.
6. **Falha não deixa resíduo por sucesso.** Em qualquer recusa, o arquivo de
   archive parcial desta execução (temporário do `mkstemp` ou caminho de
   `--output`) é removido; o comando nunca imprime caminho de archive junto
   de exit 1.

Comportamentos preservados: excludes (`__pycache__`, `.cache`,
`.pytest_cache`), skips de diretórios-symlink, contagem de arquivos no JSON,
`chmod` do snapshot herdado do `state.db` original, recusa de banco ilegível
já coberta pelo teste existente, e o checkpoint concorrente já coberto por
`test_public_backup_retains_committed_ledger_during_concurrent_checkpoint`.

## Decisões de implementação

- Ponto único de decisão: a validação de tipo (`lstat`) roda antes de abrir o
  tar (falha barulhenta sem gerar archive), e o snapshot é tirado no momento
  em que o laço alcança `state.db`, com revalidação de identidade imediatamente
  antes — se a entrada mudou, `FilesystemError`/`sqlite3` propaga e o backup
  aborta. Reutilizar `kairos_filesystem.descriptors` (padrão `O_NOFOLLOW` +
  `same_entry`) onde couber; não inventar disciplina nova.
- A corrida de P2 não é coberta por teste de tempo: o teste usa o mesmo seam
  já estabelecido no repo — `patch.object(tarfile.TarFile, "add", ...)`
  disparando a criação do banco durante o laço — e afirma o invariante
  (linha confirmada no WAL presente no `state.db` arquivado), não o timing.
- Exit code: 1, como o resto do comando `backup`. Não introduzir 77/69 neste
  caminho.

## Testes obrigatórios (RED→GREEN)

Arquivo novo `tests/test_cli_backup_boundary.py`, `unittest.TestCase`, sem
leitura de `.py`, fixtures reais de home/tempfile. Os casos 1–3 falham no
código atual (RED) e passam após a correção (GREEN).

1. **Symlink recusado sem vazamento.** `state.db` é symlink para um SQLite
   válido fora do home contendo sentinela; backup → exit 1, stdout vazio,
   stderr sem sentinela e sem o caminho do alvo, nenhum archive restante, e
   nenhuma cópia do conteúdo alvo em lugar algum do `root`.
2. **FIFO recusado sem bloquear.** `state.db` é FIFO → exit 1 dentro do
   timeout do teste (o código atual bloqueia no `sqlite3.connect`).
3. **Corrida determinística de WAL.** Home com `config.json` e sem banco;
   seam em `TarFile.add` cria `state.db` com WAL retido (conexão keeper
   aberta impede o checkpoint final) ao arquivar o primeiro arquivo. Assertar
   exit 0, `state.db` no tar com `PRAGMA integrity_check` ok e a linha
   confirmada presente; sidecars ausentes. No código atual a linha falta
   (arquivo vivo sem WAL) — RED.
4. **Home novo sem banco.** Sem `state.db` em nenhum ponto → exit 0, tar sem
   `state.db`, contagem do JSON coerente com membros do tar.
5. **Regressão.** `uv run pytest -q tests/test_skill_adoption_backup.py`
   inteiro permanece verde (roundtrip, checkpoint concorrente, banco
   ilegível, linhagem A/U).

## Compatibilidade e fora de escopo

- Comando de restauração não existe no Kairos (a descrição do grupo `backup`
  em `commands.py` promete mais do que há); esta correção não cria um —
  "restauração" aqui é extrair o tarball e abrir o `state.db`.
- Hardlink de `state.db` (`nlink > 1`) continua aceito. Pode ser outro nome
  para um arquivo também acessível fora do home; regularidade e identidade
  não comprovam origem nem confinamento do conteúdo.
- Checksum/manifesto do archive, criptografia e lock de home durante backup
  ficam fora.
- Relação com o B1: a Tarefa 2 do plano
  `docs/superpowers/plans/2026-10-14-politica-organizacional-b1.md`
  (no worktree `removal_design`) já toca
  `_archive_home` para a família de proteção. Esta correção entra **antes**
  (branch própria, pequena, independente); o plano B1 absorve o comportamento
  já corrigido e não precisa mudar.

## Entrega e gates

- Commits em português, conventional commits, ex.: `fix(cli): recusa backup
  de state.db não regular e snapshot consistente sob corrida`.
- Atualizar o parágrafo de backup em `docs/decisoes.md`
  (D-CLI.SKILL.PROVENANCE) citando a recusa de fronteira e a consistência
  sob corrida, com os comentários da PR #104 como origem.
- Gates: RED→GREEN documentado nos testes novos; suíte de backup existente
  verde; `uv run ruff check .` e `uv run ruff format --check .`; antes de
  merge, `scripts/ci.sh` e jobs remotos verdes (`AGENTS.md`).
- Revisão independente do diff pequeno recomendada (mecanismo de corrida é o
  ponto de atenção).

## Refinamentos da implementação em 2026-10-15

- `--output` recusa os nomes do banco e seus sidecars no próprio home,
  evitando substituir WAL/SHM/journal de uma conexão ainda aberta.
- Após decidir pela ausência de banco, a captura conserva também mtime/ctime
  do diretório do home. Mudança de namespace recusa conservadoramente o
  backup, inclusive criação seguida de remoção entre duas verificações
  quando houver mudança observável desses timestamps.
  Isso pode recusar outras criações/remoções concorrentes no home; uma nova
  tentativa com namespace estável é necessária. Não é monitoramento contínuo
  nem fronteira contra manipulação hostil pelo mesmo UID.

## Revisão de limites em 2026-10-16

- **ABA confirmada; limitação residual aceita para este recorte.** SQLite
  resolve `/proc/self/fd/N` para um nome e abre seu próprio handle. O
  descritor mantido pelo comando só fixa a identidade das verificações,
  feitas antes/depois da conexão, nos callbacks de 64 páginas e ao final.
  Uma substituição por symlink durante a resolução do nome, restaurada após
  a abertura e antes do próximo check, pode copiar um banco externo com
  sucesso. Isso exige escrita no home e interleaving preciso; a garantia
  cobre entradas inválidas e divergências observadas, não confinamento
  contra um escritor concorrente com essas permissões. O relatório registra
  a reprodução determinística. Fechar essa janela exige vínculo atestado
  com o arquivo realmente usado pela conexão; procurar apenas um fd com o
  inode esperado é insuficiente, pois o observador já mantém esse fd aberto.
- **Sidecars:** WAL/SHM/journal são acessados pelo nome pelo SQLite, sem
  atestação de identidade pelo comando. A validação de formato/checksum
  feita pelo SQLite não equivale a verificar origem ou confinamento. A
  exclusão desses arquivos do tar não impede sua leitura durante o snapshot.
- **Ausência:** timestamps do diretório são um detector conservador de
  mudança, não um contador de eventos. Granularidade e suporte do kernel e
  filesystem podem ocultar criação seguida de remoção no mesmo tick.
  [Multigrain timestamps](https://cdn.kernel.org/doc/html/latest/filesystems/multigrain-ts.html)
  reduzem essa janela onde suportados; não há garantia universal por versão
  do kernel. Outras alterações no nível imediato do home também recusam e
  podem exigir nova tentativa com namespace estável.
- **Plataforma:** captura de `state.db` depende de Linux com
  `/proc/self/fd` acessível e das operações de descritores usadas aqui.
  Não há fallback de snapshot por caminho no macOS ou sem procfs; o comando
  retorna erro, sem publicar o archive.
- **Saída:** o archive publicado é um arquivo novo de `mkstemp`, modo 0600
  no máximo (a umask pode restringir mais). Ao substituir `--output`, dono,
  modo e demais metadados do destino anterior não são preservados. A publicação exige criar o
  temporário e renomeá-lo no diretório pai; poder escrever somente no
  arquivo anterior não basta. Falhas continuam com diagnóstico sanitizado.
- **Alias do destino:** a exclusão de caminhos de saída usa `absolute()`,
  sem canonicalizar symlinks. Um destino anterior acessível dentro do home
  por outro caminho pode entrar como membro do tar. Não há deduplicação por
  identidade de arquivo nesta correção.
