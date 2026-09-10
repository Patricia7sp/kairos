# Agent Runtime Docker em produção

Este guia descreve como preparar, ativar, verificar e reverter o perfil Compose
`agent-runtime`. Ele é um procedimento operacional: sua existência não registra
que a implantação tenha sido executada.

O perfil acrescenta um broker confiável ao stack. A aplicação e o broker usam o
mesmo volume `kairos-data`, o mesmo UID 10000 e o mesmo socket Unix em
`/opt/data/run/runtime.sock`. Somente o broker recebe `/var/run/docker.sock`.
Cada sessão roda em um worker sem rede, sem mounts do host e sem credenciais; o
projeto permitido é copiado do bind somente leitura do broker para o worker.

## Pré-requisitos e registro anterior

Use um checkout e imagens já validados. Antes de construir ou trocar qualquer
container, registre em um local privado o commit, o ID da imagem em execução e
o nome real do volume de dados:

```sh
git rev-parse HEAD
docker inspect kairos --format 'image={{.Image}}'
docker inspect kairos --format '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{.Name}}{{end}}{{end}}'
docker image inspect kairos:local --format '{{.Id}}'
```

Preserve a imagem anterior com uma tag local de reversão antes de um build
substituir `kairos:local`:

```sh
docker image tag ID_DA_IMAGEM_ANTERIOR kairos:rollback-DATA_UTC
```

O diretório inicial autorizado deve ser sintético, dedicado ao aceite e sem
segredos. Ele precisa existir no host antes de o Compose interpretar o stack;
`create_host_path: false` faz a ativação falhar se o caminho não existir. Não use
uma raiz ampla nem um projeto de trabalho nesta primeira ativação.

```sh
install -d -o 10000 -g 10000 -m 0750 /srv/kairos-runtime-project
chown -R 10000:10000 /srv/kairos-runtime-project
chmod -R u+rX /srv/kairos-runtime-project
test -d /srv/kairos-runtime-project
```

O broker precisa atravessar todos os subdiretórios e ler todos os arquivos como
UID 10000. Ao preencher o projeto sintético, mantenha essa propriedade e acesso;
o bind somente leitura impede que o broker altere o conteúdo no host.

Descubra o GID numérico do socket Docker no host. `KAIROS_DOCKER_GID` precisa
receber esse valor, que pode ser diferente do fallback `983` do Compose:

```sh
test -S /var/run/docker.sock
stat -c '%g' /var/run/docker.sock
```

O broker roda como `10000:10000`; `group_add` acrescenta o GID acima para que
esse processo consiga abrir o socket. Não altere permissões do socket e não o
monte na aplicação.

## Backup consistente

Faça o backup antes de editar `config.yaml` ou recriar containers. Encerre ou
aguarde todos os turnos e pare as duas superfícies que escrevem no volume. A
parada do broker usa SIGINT e até 90 segundos para concluir a limpeza e remover
workers. A parada não garante um novo checkpoint de um turno ativo; somente os
checkpoints já concluídos estão preservados:

```sh
docker compose --profile agent-runtime stop runtime-broker kairos
docker ps -a --filter 'name=^/kairos-worker-' --format '{{.Names}}'
```

Não prossiga enquanto a segunda consulta listar um worker. Investigue os logs e
reinicie o broker para que a recuperação durável faça a limpeza; não remova
workers registrados manualmente.

Resolva o nome do volume a partir do container parado e copie o volume completo
para um destino novo, privado e fora do próprio volume:

```sh
DATA_VOLUME=$(docker inspect kairos --format '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{.Name}}{{end}}{{end}}')
test -n "$DATA_VOLUME"
BACKUP_DIR=/srv/backups/kairos/DATA_UTC
install -d -m 0700 "$BACKUP_DIR"
docker run --rm --entrypoint /bin/tar \
  --mount "type=volume,src=$DATA_VOLUME,dst=/source,readonly" \
  --mount "type=bind,src=$BACKUP_DIR,dst=/backup" \
  kairos:rollback-DATA_UTC \
  -C /source -czf /backup/kairos-data.tar.gz .
sha256sum "$BACKUP_DIR/kairos-data.tar.gz"
```

Esse arquivo inclui `state.db`, eventuais auxiliares `-wal`/`-shm`,
`docker-sessions/manifest.sqlite`, todos os blobs, `config.yaml` e o perfil
dedicado `codex-runtime`. Portanto, trate-o como dado sensível e não o anexe a
issues, logs ou ao repositório.

Extraia uma cópia em outro diretório privado e execute `PRAGMA integrity_check`
em `state.db` e, quando existir, em `docker-sessions/manifest.sqlite`. Confira
também que todos os blobs referenciados pelo manifest estão presentes. A cópia
só é um backup aceito depois dessas verificações e de um teste de restauração em
um volume separado, com a mesma versão de código e imagens. Nunca teste a
restauração sobre `kairos-data`.

Preserve também proprietário e permissões na cópia: `docker-sessions/` e
`docker-sessions/blobs/` devem ser privados (`0700`), e o manifest deve ser
`0600`, pertencentes ao UID/GID do runtime (`10000:10000` neste stack).
Na restauração validada em 2026-09-10, `tarfile.data_filter` omitiu os modos
dos diretórios e a extração criou `0755`; os arquivos eram idênticos, mas o
registry recusava abri-los. Ao usar esse filtro para o backup privado, mantenha
suas verificações e reponha o modo do diretório no `TarInfo` filtrado com
`filtered.replace(mode=member.mode & 0o777)`. Ajuste o proprietário na cópia e
abra os checkpoints com o UID do runtime antes de aprovar a restauração.

Uma verificação mínima dos bancos da cópia extraída pode ser feita com a
biblioteca SQLite do Python:

```sh
python3 - "$BACKUP_DIR/copia-extraida" <<'PY'
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
databases = [root / "state.db", root / "docker-sessions" / "manifest.sqlite"]
for database in databases:
    if not database.exists():
        continue
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"{database.relative_to(root)}: {result}")
    if result != "ok":
        raise SystemExit(1)
PY
```

Para validar os archives e seus digests, use `SessionRegistry.archives` como
descrito em [Runtime com sessões Docker e login ChatGPT](docker-session-runtime.md#backup-e-restauração-do-estado).

## Imagens e configuração

Construa a imagem dos workers e registre seu ID imutável. O broker usa o daemon
do host, portanto esse ID precisa existir nesse mesmo daemon:

```sh
docker build -f docker/external-sandbox/Dockerfile -t kairos:external-sandbox .
docker image inspect kairos:external-sandbox --format '{{.Id}}'
docker compose --profile agent-runtime build kairos runtime-broker
```

Antes de cada implantação ou aceite, confirme com `docker image inspect` que o
ID configurado em `agent_runtime.docker_image` existe no daemon do broker.
O status `ready` sozinho não comprova a presença dessa imagem; o healthcheck
do broker também inspeciona o ID ou a tag configurada e reprova se não existir.
Se ela tiver sido removida, reconstrua-a, valide os testes Docker, registre o
novo ID na configuração e reinicie o broker sem turnos ativos. Preserve uma
tag operacional para a imagem e confira o ID novamente após limpezas do Docker.

Edite `/opt/data/config.yaml` pelo procedimento administrativo do volume,
preservando as demais chaves. Use o caminho visto pelo broker, e fixe
`docker_image` no ID que acabou de ser validado:

```yaml
agent_runtime:
  enabled: true
  backend: docker
  codex_binary: /usr/local/bin/codex
  docker_image: sha256:ID_IMUTAVEL_DA_IMAGEM_DO_WORKER
  model: gpt-5.5
  max_workers: 2
  broad_access_enabled: false
  allowed_directories:
    - /projects/current
```

`codex_version` não é uma chave desse backend. O Codex CLI 0.153.4 está fixado
nas imagens do broker e do worker e sua compatibilidade é verificada pelo
runtime.

O backend Docker recusa `broad_access`. O bind do projeto no broker é somente
leitura mesmo para uma sessão `workspace_write`: a escrita acontece apenas na
cópia isolada do worker e só entra no checkpoint, nunca no diretório original.

## Ativação conjunta

Configure estas variáveis no ambiente persistente do stack, por exemplo no
ambiente administrado pelo Komodo:

```dotenv
COMPOSE_PROFILES=agent-runtime
KAIROS_RUNTIME_EXTERNAL=1
KAIROS_DOCKER_GID=GID_NUMERICO_DO_SOCKET
KAIROS_RUNTIME_PROJECT=/srv/kairos-runtime-project
```

As duas primeiras variáveis formam uma única mudança. `COMPOSE_PROFILES` cria o
broker externo; `KAIROS_RUNTIME_EXTERNAL=1` mantém o serviço s6 interno vivo mas
ocioso. Ativar apenas o perfil deixaria dois hosts disputando o mesmo banco,
lock e socket. Ativar apenas `KAIROS_RUNTIME_EXTERNAL` deixaria o runtime sem
host.

Antes de subir, confira a renderização sem publicar sua saída, pois outros
valores do stack podem ser sensíveis:

```sh
docker compose config --services
docker compose config --format json | jq -e '
  .services.kairos.environment.KAIROS_RUNTIME_EXTERNAL == "1" and
  .services["runtime-broker"].group_add != null'
```

Suba a aplicação e o broker com as imagens já construídas:

```sh
docker compose up -d --no-build kairos runtime-broker
```

O broker não publica portas. Sua dependência aguarda o healthcheck da aplicação;
a única porta externa continua sendo a porta do dashboard já declarada no
serviço `kairos`.

## Saúde e aceite limitado

Confirme primeiro os dois healthchecks e a rota pública de saúde:

```sh
docker compose ps kairos runtime-broker
docker inspect kairos --format '{{.State.Health.Status}}'
docker inspect kairos-runtime-broker --format '{{.State.Health.Status}}'
curl -fsS http://100.87.25.101:9119/api/health
```

O healthcheck do broker abre o socket Unix e exige `enabled=true`, `state=ready`
e uma inspeção bem-sucedida da imagem definida em `agent_runtime.docker_image`,
com o UID/GID do broker. Assim, um GID incorreto, daemon indisponível ou imagem
removida reprova a saúde mesmo com registry vazio. Configuração inválida,
desabilitada ou com backend diferente de `docker` também reprova.
Os diagnósticos da consulta são descartados. Valide também o runtime pela CLI executada na aplicação, que deve
usar o socket compartilhado do broker:

```sh
docker compose exec --user 10000:10000 kairos \
  kairos runtime status --json
```

O resultado esperado inclui `enabled: true`, `state: ready`,
`/projects/current` em `authorized_projects` e apenas os sandboxes `read_only` e
`workspace_write`. Consulte logs sem imprimir ambiente ou configuração:

```sh
docker compose logs --tail 100 runtime-broker
```

O login dedicado persiste em `/opt/data/codex-runtime`. Se a conta ainda não
estiver autenticada, inicie explicitamente um dos fluxos ChatGPT pela CLI e
siga somente as instruções exibidas no terminal:

```sh
docker compose exec --user 10000:10000 kairos \
  kairos runtime login --method chatgptDeviceCode
```

Não copie um perfil pessoal e não registre códigos, tokens ou o conteúdo do
diretório de autenticação. Depois da autenticação, um smoke sem turno de modelo
confirma broker, imagem do worker, cópia do projeto e CLI:

```sh
docker compose exec --user 10000:10000 kairos \
  kairos runtime session create \
  --cwd /projects/current --sandbox read_only --session aceite-producao --json
docker compose exec --user 10000:10000 kairos \
  kairos runtime session end --session aceite-producao --json
```

Um turno real deve continuar limitado ao projeto sintético e ser registrado no
aceite de produção separadamente, com commit, IDs das três imagens, referência
do backup, resultado da CI e limites conhecidos. Os comandos acima, sozinhos,
não comprovam acesso ao modelo nem um deploy concluído.

## Autorizar o repositório real após o aceite sintético

O broker oferece uma segunda montagem somente leitura em `/projects/kairos`.
Defina `KAIROS_RUNTIME_KAIROS_PROJECT=/caminho/absoluto/do/kairos` no ambiente
persistente do stack. Sem essa variável, a montagem reutiliza a origem de
`/projects/current`, preservando as instalações que só têm o projeto sintético.
A montagem sozinha não autoriza sessões: acrescente `/projects/kairos` à lista
`agent_runtime.allowed_directories` em `config.yaml`, mantendo o projeto
sintético se ele ainda deve estar disponível.

Antes de ativar, valide o snapshot com o UID 10000 e a mesma imagem do broker.
O código inteiro deve ser legível, sem links, arquivos especiais ou hardlinks
nos caminhos incluídos, e caber nos limites de 64 MiB e 10 mil entradas.
As exclusões de `.git`, `.venv`, `.worktrees`, `node_modules` e `.env*` não são
um detector geral de segredos. Não amplie permissões de arquivos privados para
forçar a aprovação do snapshot.

Se o workspace tiver metadados locais privados, use uma exportação limpa dos
arquivos versionados (`git archive COMMIT`) em diretório dedicado, registrando
o commit e os hashes. Aponte a montagem para essa exportação e preserve-a
durante o aceite. Isso torna explícita a versão disponível para o runtime;
novos commits exigem uma nova exportação e atualização do caminho do stack.

Com backup validado e sem turnos ativos, recrie o broker pelo Compose para
aplicar o mount e carregar a configuração. Confirme a origem somente leitura
em `docker inspect`, a lista de projetos pela CLI e o healthcheck. Faça o aceite
pela Web primeiro em `read_only`, depois em `workspace_write`. Neste último,
a escrita ocorre na cópia isolada; abra o checkpoint, revise o diff e confira
que o repositório original ficou intacto antes de aplicar qualquer alteração.

## Reversão sem perda de dados

Para reverter, encerre turnos, pare primeiro o broker com o perfil ainda ativo e
confirme que não restaram workers:

```sh
docker compose --profile agent-runtime stop runtime-broker
docker ps -a --filter 'name=^/kairos-worker-' --format '{{.Names}}'
```

No ambiente persistente do stack, remova `agent-runtime` de
`COMPOSE_PROFILES` e defina `KAIROS_RUNTIME_EXTERNAL=0`. Reverta somente a seção
`agent_runtime` de `config.yaml` para a configuração registrada antes da
ativação. Em seguida, recoloque a tag da aplicação anterior e recrie apenas a
aplicação sem build:

```sh
docker compose stop kairos
docker image tag kairos:rollback-DATA_UTC kairos:local
docker compose up -d --no-build kairos
docker compose ps kairos
curl -fsS http://100.87.25.101:9119/api/health
```

Não execute `docker compose down -v`, não apague `kairos-data` e não restaure o
arquivo anterior automaticamente: `state.db` e os checkpoints podem conter
dados criados depois da ativação. Se a imagem anterior não aceitar o schema
atual, não faça downgrade; mantenha o volume intacto e use uma imagem compatível
ou uma correção adiante. Uma restauração do backup é uma operação separada, deve
ter destino novo e precisa reconciliar explicitamente quaisquer dados mais
recentes.
