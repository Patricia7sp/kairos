# Runtime com sessões Docker e login ChatGPT

Backend opt-in em `feat/runtime-external-sandbox`. O broker roda em um ambiente
de desenvolvimento confiável com acesso ao Docker. Cada sessão executa em um
worker separado, sem rede externa, mounts do host ou credenciais. A aplicação
de produção não precisa receber o socket Docker.

O dev container serve para desenvolver e testar o código. O worker é outra
fronteira: UID 10000, rootfs somente leitura, `network=none`, capabilities
removidas, `no-new-privileges`, seccomp e AppArmor padrão, CPU/memória/PIDs
limitados. A criação verifica esses controles antes de iniciar o Codex.

## Preparar uma instalação de teste

No checkout desta branch, construa a imagem:

```sh
docker build -f docker/external-sandbox/Dockerfile -t kairos:external-sandbox .
```

Crie um `KAIROS_HOME` privado, separado dos projetos autorizados. Exemplo de
`config.yaml` desse diretório:

```yaml
agent_runtime:
  enabled: true
  backend: docker
  codex_binary: /home/operador/.local/bin/codex
  docker_image: kairos:external-sandbox
  model: gpt-5.5
  max_workers: 2
  broad_access_enabled: false
  allowed_directories:
    - /home/operador/projetos/exemplo
```

O broker exige Codex 0.153.4. A imagem é resolvida para um digest imutável em
cada criação. Para fixar também entre sessões, configure `docker_image` com o
ID `sha256:...` da imagem validada. `broad_access` é recusado neste backend.
Diretórios que contenham o broker, ou estejam dentro dele, também são recusados.

Inicie em um terminal dedicado:

```sh
KAIROS_HOME=/caminho/privado/kairos-lab uv run kairos runtime serve
```

Em outro terminal, com o mesmo `KAIROS_HOME`:

```sh
kairos runtime status --json
kairos runtime login --method chatgptDeviceCode
```

Conclua o código no endereço oficial informado pelo comando. Alternativamente,
`--method chatgpt` inicia o fluxo com callback no navegador. Nunca envie senha
ou token ao chat. A conta fica no `codex-runtime/auth.json` privado do broker;
o armazenamento em arquivo é forçado e o Codex gerencia a renovação. O backend
recusa login por API key. O procedimento oficial está em
[autenticação do Codex](https://learn.chatgpt.com/docs/auth).

O modelo inicial `gpt-5.5` simplifica o protocolo Responses da versão fixada;
o acesso e os limites da conta só podem ser confirmados após o login real.
Contas com roteamento FedRAMP são recusadas até suporte específico.

## Sessões e alterações

```sh
kairos runtime session create --cwd /caminho/do/projeto --sandbox workspace_write --json
```

Web e CLI usam o mesmo contrato Unix de sessões, turnos, eventos e aprovações.
O broker limita o número de workers simultâneos. Um worker recebe uma cópia
limitada do projeto, excluindo `.git`, `.worktrees`, `.venv`, `node_modules`,
caches e `.env*`. Isso não é um detector geral de segredos: somente projetos
adequados ao processamento pelo modelo devem entrar na allowlist.

As alterações ficam na cópia. Ao terminar um turno, o broker revoga o acesso ao
modelo, confirma o encerramento do Codex, suspende processos restantes, exporta
workspace e estado da thread, grava ambos atomicamente e remove o worker antes
de publicar a conclusão. Nenhuma alteração é aplicada automaticamente ao projeto
original. O modo `read_only` também usa workspace somente leitura no container.

A thread é materializada e salva já na criação da sessão, antes do primeiro
turno. Isso permite retomar em outro worker sem manter um container ocioso.
O relay aceita ferramentas locais; descoberta de ferramentas exige execução
`client`. Ferramentas hospedadas e namespaces de ferramentas são recusados.

Os checkpoints ficam em `docker-sessions/manifest.sqlite` e
`docker-sessions/blobs/<sha256>`. São arquivos tar validados, sem links ou
caminhos que escapem da raiz. Para obter uma cópia revisável sem extrair no host:

```python
from pathlib import Path
from kairos_runtime.docker_backend.registry import SessionRegistry

registry = SessionRegistry(Path("/caminho/privado/kairos-lab/docker-sessions"))
try:
    workspace, _home = registry.archives("ID_DA_SESSAO")
    # Use um destino novo. O arquivo pode conter dados privados do projeto.
    import os

    fd = os.open("/tmp/kairos-workspace.tar", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(workspace)
finally:
    registry.close()
```

Revise a listagem com `tar -tf /tmp/kairos-workspace.tar`. A aplicação das
alterações exige uma etapa explícita de revisão; evite extrair sobre o projeto.
Cada arquivo tar tem limite de 80 MiB, com até 64 MiB de arquivos e 10 mil
entradas. Não há coleta automática dos blobs antigos ou órfãos: monitore o uso
de disco do broker.

## Falhas e retomada

O nome do worker é registrado antes de criar o container. Ao reiniciar, o broker
remove e verifica os workers registrados antes de admitir novas tarefas.
Limpeza incerta mantém o runtime indisponível. A morte do processo de autenticação
também fecha a admissão até reiniciar o broker.

Uma thread pode retomar o último checkpoint confirmado em um novo worker.
Turnos abandonados não são reenviados: mudanças posteriores ao checkpoint podem
estar perdidas ou ter resultado incerto. A recuperação nunca assume que um
container morreu apenas porque o cliente `docker exec` encerrou.

## Testes sem chamadas pagas

```sh
uv run pytest -q tests/test_docker_registry.py tests/test_docker_model_relay.py \
  tests/test_docker_chatgpt_auth.py tests/test_docker_session_runtime.py tests/test_docker_host.py
KAIROS_EXTERNAL_SANDBOX_TEST=1 uv run pytest -q tests/test_docker_session_worker.py \
  tests/test_docker_sessions_integration.py
```

Os testes reais de container usam um transporte de modelo simulado. Login real,
acesso ao modelo da conta, CI remota e ativação em produção exigem evidências
separadas; não são comprovados por esse smoke offline.

O [aceite local de 2026-09-07](superpowers/specs/2026-09-07-docker-sessions-acceptance.md)
registra separadamente o login ChatGPT concluído e um turno real via RuntimeClient
com resposta `KAIROS_OK`. A integração remota está no
[PR #13](https://github.com/Patricia7sp/kairos/pull/13); esse aceite local é um
registro anterior ao merge. Deploy e habilitação em produção são etapas separadas.
O [aceite do piloto](superpowers/specs/2026-09-07-docker-pilot-acceptance.md)
registra a integração, supervisão, backup e retomada pela API Web com modelo real.

## Broker supervisionado no piloto

Os arquivos em `docker/broker/` são unidades systemd do usuário para o checkout
`~/projetos/kairos` e o perfil `~/.local/share/kairos-docker-lab`. Ajuste os caminhos
se sua instalação for diferente. Instale as dependências com `uv sync --frozen`
no checkout validado e fixe `docker_image` no ID da imagem aceita. Pare qualquer
broker iniciado manualmente antes de habilitar a unidade; mantenha o perfil de
autenticação dedicado existente.

O usuário precisa ser membro do grupo `docker`. A unidade usa `sg docker` para
selecionar esse grupo mesmo quando o gerenciador systemd foi iniciado antes da
inclusão; isso não concede associação nova nem modifica o socket. Depois,
`setpriv --no-new-privs` inicia o broker com elevação adicional bloqueada. O check
de acesso ao socket impede uma partida aparentemente saudável sem acesso Docker.
Como algumas implementações de `sg` mantêm um processo pai, o launcher informa
ao systemd o PID que executará o broker antes do `exec`. A unidade `Type=notify`
com `NotifyAccess=all` aceita essa identificação do filho; assim o SIGINT chega
ao broker, preservando `KillMode=mixed`. A notificação confirma a entrega do
processo ao supervisor; a prontidão funcional continua sendo `runtime status`.
O `ExecStop` envia SIGINT ao PID informado e aguarda sua saída por até 85 segundos,
antes do limite global de 90 segundos. Essa espera explícita também cobre PIDs
adotados que não são filhos diretos do systemd; não encerra antecipadamente os
clientes Docker ou o processo de autenticação enquanto o broker faz cleanup.

```sh
mkdir -p ~/.config/systemd/user
cp docker/broker/kairos-docker-lab* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kairos-docker-lab.service kairos-docker-lab-disk.timer
KAIROS_HOME=~/.local/share/kairos-docker-lab .venv/bin/kairos runtime status --json
journalctl --user -u kairos-docker-lab.service -n 50
```

A unidade envia SIGINT ao broker para permitir cleanup antes de encerrar os
processos restantes. Reinícios por falha são limitados a três em cinco minutos.
`active` no systemd não substitui o status `ready` do runtime: falha de autenticação
ou recuperação pode manter o processo vivo e a admissão fechada.

O serviço inicia junto do gerenciador systemd do usuário. Sobrevivência ao logout
e inicialização antes do login dependem de `Linger=yes` no host; consulte
`loginctl show-user "$USER" -p Linger`. Não é necessário mudar isso para testar
na sessão atual. Não instale estas unidades dentro dos workers sem rede.

O check de disco recusa a inicialização com 2 GiB usados pelo perfil ou menos
de 1 GiB livre no filesystem. O timer repete o check a cada cinco minutos e
registra falhas no journal. Os limites podem ser ajustados por
`KAIROS_MAX_HOME_KIB` e `KAIROS_MIN_FREE_KIB` nas unidades. Trata-se de monitoramento
e bloqueio de partida, não de quota rígida: ele não interrompe um broker ativo
nem remove checkpoints. O backup deve ficar fora do perfil monitorado.

## Backup e restauração do estado

Faça a manutenção sem turnos ativos e com todas as superfícies que escrevem no
perfil paradas. Pare o broker com `systemctl --user stop kairos-docker-lab.service`
e confirme a ausência dos workers registrados. Mantenha-o parado durante a cópia
para preservar a relação entre `state.db`, manifest e blobs. Copie os arquivos
SQLite auxiliares `-wal`/`-shm` caso existam. Não copie apenas o manifest.

Guarde `config.yaml`, `state.db` e seus auxiliares, e o diretório completo
`docker-sessions` em um destino novo com permissões privadas. O perfil
`codex-runtime` contém credenciais e não faz parte desse backup de checkpoints;
restaurar em outra instalação exige um novo login dedicado. Blobs podem conter
conteúdo privado dos projetos. Proteja o backup como o diretório original.

Para testar a cópia, abra os dois bancos SQLite em modo somente leitura e
execute `PRAGMA integrity_check`; valide os archives referenciados usando
`SessionRegistry.archives` em uma cópia temporária. Teste a restauração com a
mesma versão do código e da imagem, em um novo perfil autorizado, preservando
os caminhos e identidades dos projetos. Não sobrescreva o perfil ativo para
testar e não faça downgrade de schema. Reinicie o broker original e confirme
`ready` depois da manutenção.

### Catálogo de versões e revisão de alterações

O broker descobre versões imutáveis em `agent_runtime.project_catalogs` somente
na inicialização, fora da leitura leve de configuração usada pelo healthcheck.
Cada versão usa `<catálogo>/<commit completo>/workspace` e um `project.json`
irmão verificado. Apenas esses workspaces exatos entram na autorização; o
catálogo e os subdiretórios não viram raízes autorizadas. As raízes existentes
em `allowed_directories` continuam válidas.

No Compose, defina `KAIROS_RUNTIME_PROJECT_CATALOG=/srv/kairos-project-versions`
para montar o catálogo somente leitura em `/projects/versions` no broker. Sem
essa variável, o mount reutiliza `KAIROS_RUNTIME_PROJECT` (ou o fallback
`/srv/kairos-runtime-project`); deixe `project_catalogs: []` até fornecer um
catálogo real. A aplicação Web não recebe esse mount nem o socket Docker.
Configuração correspondente no `config.yaml` do broker:

```yaml
agent_runtime:
  enabled: true
  backend: docker
  allowed_directories: [/projects/current, /projects/kairos]
  project_catalogs: [/projects/versions]
  broad_access_enabled: false
```

Após exportar uma versão ou alterar essa configuração, espere a fila e todos os
turnos ficarem ociosos e reinicie explicitamente apenas o broker. A nova versão
aparece no seletor Web como nome do projeto e commit curto. Sessões existentes
mantêm seu diretório, checkpoint e baseline originais; nenhuma tarefa interrompida
é repetida automaticamente.

Uma sessão concluída e ociosa oferece **Revisar alterações**. O broker compara o
último checkpoint confirmado com o arquivo inicial capturado antes de criar o
primeiro worker; alterações posteriores no diretório de origem não mudam essa
baseline. A tela apresenta commit de origem, ID da revisão, arquivos e diff como
texto. **Baixar pacote** salva o JSON completo e compacto. Iniciar outro turno ou
mudar de sessão invalida a prévia. Esse fluxo não aplica, executa ou publica código.

A mesma revisão está em `session.changes` no IPC e em
`GET /api/runtime/sessions/{session_id}/changes` na API autenticada. Trabalho
pendente retorna conflito; backend local retorna indisponível. Sessões antigas
sem baseline retornam `baseline_missing`, sem inferir sua origem a partir do
estado atual. Projetos sem manifesto têm `base_commit: null`. Pacotes maiores
que 512 KiB retornam `review_too_large`, sem truncamento; a mensagem IPC continua
limitada a 1 MiB. O pacote contém apenas arquivos do workspace, nunca o home ou
a autenticação do worker.
