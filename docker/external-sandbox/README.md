# Sandbox externa experimental

Este protótipo valida comandos reais do Codex 0.154.0 em containers descartáveis
usando Docker padrão. Não habilita o runtime em produção nem precisa de novos
perfis AppArmor. O launcher roda no host; o worker não recebe controle do Docker.

## Executar

Na raiz deste checkout, com Docker local já autorizado:

```bash
docker build -f docker/external-sandbox/Dockerfile --target worker -t kairos:external-sandbox .
uv sync --frozen --extra dev
uv run python -m kairos_runtime.experimental --project /caminho/projeto-de-teste -- python -c 'print(40 + 2)'
```

O padrão protege o projeto contra escrita. `--writable` permite escrever na
cópia descartável. O comando roda em `/workspace`; o resultado JSON contém
stdout, stderr, código de saída, imagem, evidência de kernel e confirmação de
remoção do container. Arquivos gerados desaparecem com o worker e nunca são
copiados automaticamente ao projeto original. Não use este protótipo para
produzir arquivos que precise conservar.

Erro de comando (exit code diferente de zero), timeout ou cancelamento também
descarta o worker inteiro. Se o daemon não responder e a remoção não puder ser
confirmada, a CLI informa o nome com limpeza pendente, sem declarar sucesso.
Reestabeleça o Docker e inspecione/remova **somente esse worker**; não use prune.
O teste exige Linux amd64, AppArmor docker-default e cgroups v2; foi validado no
Docker 29.7.2 deste host. Ambientes que não atendam à atestação são recusados.

```bash
KAIROS_EXTERNAL_SANDBOX_TEST=1 uv run pytest -q tests/test_external_sandbox_integration.py
uv run pytest -q tests/test_external_sandbox.py
```

Os testes não autenticam o Codex, não fazem chamadas de modelo e não montam
volumes de produção. O teste de rede tenta TCP para um IP diretamente, para
não confundir falha de DNS com bloqueio de saída.

## O que isola

- Um snapshot por worker, sem mounts do host, sockets ou volumes herdados.
- Rede `none`, rootfs read-only, usuário 10000, capabilities zeradas,
  no-new-privileges, AppArmor docker-default e seccomp ativo.
- Cgroups: 512 MiB de memória, sem swap adicional, 1 CPU e 128 processos.
- Diretórios temporários em tmpfs com limites próprios, removidos ao encerrar.
- Read-only por ownership de root dos arquivos e diretórios importados;
  o processo do agente não consegue reverter isso com chmod ou rename.
- Configuração Docker vazia e endpoint Unix fixo; proxies e contextos pessoais
  não são herdados. Nenhuma variável de autenticação é enviada ao worker.

O projeto de entrada precisa ser autorizado e adequado para cópia. O importador
rejeita links/hardlinks/arquivos especiais e limita conteúdo a 64 MiB/10.000
entradas. Exclui `.git`, `.worktrees`, caches e `.env*`. Essas exclusões não são
um detector geral de segredos: credenciais gravadas dentro de código continuam
sendo conteúdo do projeto. Não há leitura do banco/cofre real nos testes.

`externalSandbox` declara que o isolamento é responsabilidade do ambiente.
Por isso só é enviado após conferir o container e as proteções do kernel.
Docker compartilha o kernel do host; esta validação não equivale a isolamento
por VM nem prova ausência de vulnerabilidades no kernel.

## Dev Container

```bash
docker build -f .devcontainer/Dockerfile -t kairos:devcontainer .
```

Abra este checkout com **Dev Containers: Reopen in Container**. A configuração
usa um volume dedicado `kairos-dev-<id>` inicializado com uma cópia do código da
imagem. Python, uv, Node e dependências Python/frontend já estão instalados.
O original no host não é montado; alterações no volume não voltam automaticamente
para a branch. Em rebuilds, um volume existente conserva sua cópia antiga:
exporte o trabalho que queira guardar e crie um novo volume para atualizar a base.

O Dev Container é um ambiente de desenvolvimento, com rede para ferramentas;
não é o worker atestado. O launcher Docker permanece no host, pois nenhum socket
Docker é montado no ambiente de edição. Configurações pessoais do editor podem
compartilhar autenticação Git: desative esse compartilhamento no editor se
precisar de uma sessão sem credenciais. Os testes de isolamento usam somente
o launcher dedicado, sem o editor.

Dentro dele, `scripts/test-devcontainer.sh` executa a suíte Python com as
dependências instaladas, incluindo compilação nativa. As verificações de
`docker compose config`, `docker build --check` e imagens reais ficam no host;
o script informa suas exclusões ao pytest, sem simular um daemon Docker.

## Integração posterior

O host, o adaptador e o Compose de produção não foram alterados. Antes de usar
este desenho no Web/CLI são necessários um broker de workers com interface
restrita, roteamento por sessão, persistência/retomada e exportação revisável de
alterações. Também é preciso separar o tráfego/autenticação do modelo do código
do agente; liberar rede geral no worker não satisfaz o desenho aprovado.

O protocolo fixado aceita `externalSandbox` em `command/exec` e `turn/start`.
`thread/start.sandbox` oferece apenas read-only, workspace-write e
danger-full-access. Um teste de comando bem-sucedido não prova integração de
turnos, aprovação, cancelamento e retomada do adaptador existente. Esses fluxos
precisam de testes próprios antes de habilitar o backend.

Fontes utilizadas: [Dev Containers](https://code.visualstudio.com/docs/devcontainers/containers),
[Docker seccomp](https://docs.docker.com/engine/security/seccomp/),
[Docker proxy](https://docs.docker.com/engine/cli/proxy/),
[command/exec no Codex fixado](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/app-server-protocol/src/protocol/v2/command_exec.rs).
