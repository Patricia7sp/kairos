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
com resposta `KAIROS_OK`. CI remota, merge e deploy desta branch continuam pendentes.
