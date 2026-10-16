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

## Histórico de evidências em 2026-10-15

Esta seção preserva os resultados relatados na sessão anterior. Os logs
antigos em `/tmp` não existem na retomada de 2026-10-16; não servem como
comprovação atual do gate. A validação do ajuste está na seção de retomada.

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
- A execução `umask 0022 && scripts/ci.sh` foi iniciada na sessão anterior;
  seu resultado final não é verificável pelo log hoje ausente
  `/tmp/kairos-backup-boundary-ci-20261015.log`.

Esses registros históricos não atestam CI remoto, merge ou deploy do ajuste.

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
Houve outra execução integral iniciada com PATH pinado, associada ao log
`/tmp/kairos-backup-boundary-ci-final.log`. Como esse log também desapareceu,
seu resultado não foi revalidado nesta retomada; o gate atual exige uma
execução nova, registrada separadamente abaixo.

## Análise da revisão em 2026-10-16

A revisão do usuário confirmou um limite ausente da garantia escrita:
o descritor de observação não é o handle interno do SQLite. A opção adotada
neste ajuste é documentar a corrida ABA residual, conforme a alternativa
oferecida na revisão, mantendo o escopo cooperativo. A ABA não foi corrigida.

Reprodução determinística em fixtures temporárias, Python 3.11.16,
SQLite 3.53.1, Linux 7.0.0-38-generic:

1. Criar dois bancos reais com a mesma tabela: o do home contém `inside`;
   o externo contém `outside-sentinel`. Fechar as conexões de preparação.
2. Executar o comando público em subprocesso com um shim `LD_PRELOAD`.
   O shim chama o `readlink` original e, quando o resultado de resolução do
   descritor é o nome do banco do home, renomeia esse banco para retenção e
   instala um symlink no nome original apontando ao banco externo.
3. Deixar o SQLite resolver e abrir o banco externo pelo `open64` real.
   Depois dessa abertura, antes de retornar ao SQLite, remover o symlink e
   devolver o banco retido ao nome original.
4. Ler o `state.db` arquivado por uma conexão SQLite real e comparar o inode
   do banco do home com o inicial.

Resultado observado: `exit_code=0`, `original_inode_restored=true`,
`external_content_copied=true`. A instrumentação força apenas a ordem dos
syscalls; conexão, verificações do comando e cópia SQLite são reais. É uma
prova local do limite, não um teste de regressão que aprove esse resultado.
Os scripts temporários originais eram `/tmp/kairos-backup-aba-probe.{c,py}` e
a biblioteca `/tmp/kairos-backup-aba-probe.so`; desapareceram entre sessões.
O apêndice conserva o código da sonda para nova execução, sem depender
desses arquivos e sem adicioná-la à suíte de regressão.
Uma tentativa inicial de trocar o nome somente no
`open64`, depois de resolvido, foi recusada neste ambiente; isso não elimina
a janela anterior, reproduzida acima.

Varrer `/proc/self/fd` à procura do inode esperado não resolve por si só:
o descritor do observador já satisfaz essa comparação mesmo durante o
desvio da conexão. Um endurecimento futuro precisa identificar e atestar o
arquivo da própria conexão e tratar sidecars, concorrência e reuso de fds.
SQLite oferece [controle do arquivo da conexão na API C](https://www.sqlite.org/c3ref/file_control.html);
isso não torna uma varredura de fds um vínculo automático com a conexão.
Comparar os conjuntos de descritores antes/depois de `sqlite3.connect`
e depois da cópia é uma hipótese futura sugerida pelo usuário, ainda sem
validação. Não se assume que o arquivo principal abra sempre no `connect`,
nem que ausência de outras threads garanta essa atribuição; WAL tardio e
reuso de descritores também precisam ser investigados.

A spec e `docs/decisoes.md` agora limitam a promessa a divergências
observadas e registram acesso por nome aos sidecars, granularidade dos
timestamps, dependência de Linux/procfs, hardlinks aceitos, aliases de saída,
modo no máximo 0600 (a umask pode restringir mais) e substituição dos
metadados do destino. O manual expõe os limites pertinentes ao operador.
A mensagem de recusa passou a
orientar conferir `state.db` e os diretórios do home e tentar novamente com
o home estável; repetir não corrige um symlink permanente ou uma configuração inválida.

A revisão não autoriza merge. Os checks do commit anterior não atestam
automaticamente o estado modificado.

Validação histórica inicial do ajuste: `.venv/bin/python -m pytest -q
tests/test_cli_backup_boundary.py tests/test_skill_adoption_backup.py`:
**25 passed, 10 subtests passed**, 20,77 s, exit 0. Ruff check e format check
de `kairos_cli/backup.py` passaram, assim como `git diff --check`.
A mudança de produção é somente a mensagem de erro. Naquele ponto a suíte
integral e os jobs remotos não haviam sido reexecutados para o ajuste;
esse registro não representa o estado da validação posterior.

## Retomada e evidências em 2026-10-16

Antes da segunda revisão, uma execução nova de `scripts/ci.sh`, com
`umask 0022` e Codex 0.154.0 no início do PATH, terminou com exit 0:
3.849 testes e 6.319 subtestes passaram; 16 skips e 31 deselecionados.
Ruff, shellcheck, recall (20 testes), lockfile, os três TypeScript e
239 testes Vitest passaram, assim como hadolint, estrutura e build da
imagem real. A integração passou 23 testes/23 subtestes com sete skips
por ausência da stub; após construir `kairos:stub`, somente essa etapa
foi repetida: **30 testes e 26 subtestes passaram**, sem skips, em 92,66 s.
Hadolint v2.12.0 também validou os dois Dockerfiles com exit 0.

Na retomada, os logs `ci.log`, `stub-build.log` e `validated-files.json`
com SHA-256 dos arquivos relevantes foram encontrados no worktree, fora do
índice Git, em `.superpowers/evidence/backup-20261016/`. O `integration.log`
citado anteriormente não foi encontrado e seu resultado acima permanece
histórico. A primeira tentativa dentro do sandbox foi bloqueada pelo
cache do uv somente leitura; não é contada como aprovação.

A segunda revisão alterou a mensagem de recusa para mencionar também os
diretórios do home. A execução iniciada então usava o log temporário
`/tmp/kairos-backup-review-20261016-ci.log`, ausente nesta retomada; seu
resultado não é atestado. A tentativa seguinte ficou registrada em
`.superpowers/evidence/backup-20261016/ci-review-final.log`, mas foi
interrompida durante a suíte e também não representa aprovação.

A sonda do apêndice foi extraída sem alterações para
`.superpowers/evidence/backup-20261016/aba/`, compilada com o comando
documentado e executada com o Python 3.11.16 do worktree, SQLite 3.53.1,
Linux 7.0.0-38-generic. Compilação e execução retornaram 0. Resultado:

```json
{"exit_code": 0, "original_inode_restored": true, "external_content_copied": true, "integrity_check": "ok", "reproduced": true}
```

Isso confirma a limitação residual, sem alterar o aceite do escopo cooperativo.

## Fechamento do ajuste em 2026-10-16

A retomada posterior executou novamente `scripts/ci.sh`, com `umask 0022`
e Codex 0.154.0 no início do PATH. A tentativa no sandbox não acessou o
cache do uv e não constitui aprovação. A execução com acesso autorizado
terminou todas as etapas; seu log persistido é
`.superpowers/evidence/backup-20261016/ci-resume-approved.log`:

- Suíte: **3.849 testes e 6.319 subtestes passaram**, 16 skips e
  31 deselecionados, em 329,72 s. Python 3.11.16, SQLite 3.53.1.
- Ruff check, shellcheck, recall (20 testes) e `uv lock --check`: aprovados.
- Os três TypeScript e 239 testes Vitest: aprovados.
- Estrutura e build da imagem: aprovados. Imagem
  `sha256:962259e4b2816d049118656d4ec48daa0ee5a26239a3ed1cdc19c049f8730be0`.
- Integração real + stub: **30 testes e 26 subtestes passaram**, sem skips,
  em 91,80 s.
- Hadolint v2.12.0 validou separadamente `Dockerfile` e `Dockerfile.stub`
  com exit 0.

O script terminou com **exit 1 somente por Ruff format**: o exemplo Python
do apêndice precisava de formatação. O formatador corrigiu esse bloco;
`.venv/bin/ruff format --check .` passou depois, com 648 arquivos, exit 0.
Não se declara uma execução única do script com exit 0. Os checks válidos
foram reutilizados conforme `AGENTS.md`: hashes de produção, testes,
dependências, configuração e Dockerfiles permaneceram idênticos aos da
execução. O registro está em
`.superpowers/evidence/backup-20261016/resume-inputs.json`. A correção de
formatação não alterou o comportamento da sonda.

A sonda foi compilada novamente com `cc -shared -fPIC -Wall -Wextra` e
executada pelo Python do worktree: exit 0, inode restaurado, conteúdo
externo copiado e `integrity_check=ok`. Esse resultado reproduz a limitação.

Revisão independente do diff dos cinco arquivos: GPT-6 Luna, raciocínio
high, contexto isolado e somente leitura; nenhum achado Critical/Important.
O revisor conferiu a precisão dos contratos e da sonda, sem executar a
suíte geral nem reproduzir a sonda por conta própria. Root fez essas
verificações e conferiu as evidências integradas.

Minor mantido: a orientação genérica de tentar novamente também aparece
para configuração persistentemente inválida. A mensagem manda conferir
`state.db` e os diretórios primeiro; manual e relatório esclarecem que
repetir não corrige essa configuração. Nenhuma garantia de segurança
depende dessa orientação. O registro detalhado da revisão permanece em
`.superpowers/evidence/backup-20261016/review-resume.md`.

Os jobs remotos do ajuste serão conferidos após o push. Resultados de
`c4bfaf2` não são atribuídos automaticamente ao novo commit. Merge e
deploy permanecem fora desta retomada.

## Apêndice: sonda ABA reproduzível

Sonda manual de diagnóstico, fora da suíte. Atua somente nos dois bancos
de uma fixture temporária criada pelo script Python. Exige Linux/procfs,
compilador C e um SQLite dinâmico que passe por `readlink` e `open64`.
Uma plataforma sem esses pontos de interposição pode não reproduzir a
janela; resultado negativo não comprova confinamento.

Salve os blocos abaixo como `aba-probe.c` e `aba-probe.py` num diretório
temporário. No worktree, usando seu Python 3.11 instalado com o projeto:

```bash
cc -shared -fPIC -Wall -Wextra -o /tmp/aba-probe.so /tmp/aba-probe.c -ldl
.venv/bin/python /tmp/aba-probe.py /tmp/aba-probe.so
```

O script imprime JSON e retorna 0 somente quando observa backup com exit 0,
identidade original restaurada e conteúdo externo arquivado. Esse retorno
confirma a limitação; não é aprovação de segurança nem teste de regressão.
Falhas de instrumentação/compilação são erros explícitos. Nenhuma entrada
do home real do operador é usada.

`aba-probe.c`:

```c
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int swapped = 0;

ssize_t readlink(const char *path, char *buffer, size_t size) {
    static ssize_t (*original)(const char *, char *, size_t) = NULL;
    if (!original) original = dlsym(RTLD_NEXT, "readlink");
    if (!original) _exit(93);
    ssize_t length = original(path, buffer, size);
    const char *database = getenv("KAIROS_ABA_DB");
    const char *external = getenv("KAIROS_ABA_EXTERNAL");
    const char *retained = getenv("KAIROS_ABA_RETAINED");
    if (!swapped && database && external && retained
            && length == (ssize_t)strlen(database)
            && !memcmp(buffer, database, (size_t)length)) {
        if (rename(database, retained) || symlink(external, database)) _exit(90);
        swapped = 1;
    }
    return length;
}

int open64(const char *path, int flags, ...) {
    static int (*original)(const char *, int, ...) = NULL;
    if (!original) original = dlsym(RTLD_NEXT, "open64");
    if (!original) _exit(93);
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, int);
        va_end(args);
    }
    const char *database = getenv("KAIROS_ABA_DB");
    const char *external = getenv("KAIROS_ABA_EXTERNAL");
    const char *retained = getenv("KAIROS_ABA_RETAINED");
    if (swapped && database && external && retained && !strcmp(path, external)
            && (flags & O_ACCMODE) == O_RDONLY) {
        unsetenv("KAIROS_ABA_DB");
        int fd = original(path, flags, mode);
        if (unlink(database) || rename(retained, database)) _exit(91);
        if (fd < 0) _exit(92);
        return fd;
    }
    return original(path, flags, mode);
}
```

`aba-probe.py`:

```python
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path

CHILD = """
import argparse
import asyncio
import sys
from pathlib import Path
from kairos_cli.backup import run_backup

raise SystemExit(asyncio.run(run_backup(
    Path(sys.argv[1]), argparse.Namespace(output=sys.argv[2], json=True)
)))
"""

if len(sys.argv) != 2:
    raise SystemExit("Uso: aba-probe.py /caminho/aba-probe.so")
library = Path(sys.argv[1]).resolve(strict=True)

with tempfile.TemporaryDirectory(prefix="kairos-aba-probe-") as temporary:
    root = Path(temporary)
    home = root / "home"
    home.mkdir()
    database = home / "state.db"
    external = root / "external.db"
    for path, value in ((database, "inside"), (external, "outside-sentinel")):
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE probe(value TEXT)")
            connection.execute("INSERT INTO probe VALUES (?)", (value,))
            connection.commit()
    expected = database.stat()
    archive = root / "backup.tar.gz"
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(home), str(archive)],
        env={
            **os.environ,
            "LD_PRELOAD": str(library),
            "KAIROS_ABA_DB": str(database),
            "KAIROS_ABA_EXTERNAL": str(external),
            "KAIROS_ABA_RETAINED": str(home / "held.db"),
        },
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        print(json.dumps({"exit_code": result.returncode, "reproduced": False}))
        raise SystemExit(1)
    with tarfile.open(archive) as bundle:
        member = bundle.extractfile("state.db")
        if member is None:
            raise RuntimeError("O backup não contém state.db.")
        snapshot = root / "snapshot.db"
        with member:
            snapshot.write_bytes(member.read())
    with closing(sqlite3.connect(snapshot)) as connection:
        copied = connection.execute("SELECT value FROM probe").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    observed = database.stat()
    restored = (observed.st_dev, observed.st_ino) == (expected.st_dev, expected.st_ino)
    reproduced = restored and copied == "outside-sentinel" and integrity == "ok"
    print(
        json.dumps(
            {
                "exit_code": result.returncode,
                "original_inode_restored": restored,
                "external_content_copied": copied == "outside-sentinel",
                "integrity_check": integrity,
                "reproduced": reproduced,
            }
        )
    )
    raise SystemExit(0 if reproduced else 1)
```
