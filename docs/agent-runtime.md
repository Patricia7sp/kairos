# Agent Runtime Codex

O Agent Runtime é um host local compartilhado para o Codex App Server. Ele é
opcional e vem desabilitado. Os providers existentes continuam disponíveis
quando o runtime está desabilitado ou indisponível; consulte o estado separado
em `kairos runtime status` ou `GET /api/runtime/status`.

## Container

A imagem amd64 instala o pacote standalone oficial do Codex CLI 0.153.4 em
`/usr/local/lib/codex` e expõe `/usr/local/bin/codex`. O build verifica o
SHA-256 antes de extrair e mantém os recursos do pacote, incluindo `bwrap`,
`zsh`, `rg` e `codex-code-mode-host`. O serviço s6 `runtime` roda como o usuário
`kairos` e executa `kairos runtime serve` no mesmo `KAIROS_HOME=/opt/data` dos
demais serviços.

O `CODEX_HOME` dedicado fica em `/opt/data/codex-runtime`, criado pelo host com
permissão 0700. Não monte credenciais do operador nessa pasta. O container não
usa `privileged`, não recebe o socket Docker e não amplia a política quando o
sandbox fica indisponível. Quando a feature está habilitada em container, o
host executa primeiro `codex sandbox /bin/true`, com timeout e ambiente
sanitizado. Somente um probe bem-sucedido permite iniciar o App Server. Falha
ou timeout mantém o status em `unavailable`, com o diagnóstico público estático
`sandbox do runtime indisponível`; a saída bruta do sandbox não é publicada nem
registrada. Os providers e o restante do Kairos continuam funcionando.

Na plataforma Docker usada no aceite local de 2026-09-06, o `bwrap` empacotado
não conseguiu criar o namespace de usuário. A imagem se comportou como previsto
e manteve o runtime indisponível. Esse resultado não comprova um runtime pronto
em container: antes de ativá-lo em outro ambiente, execute a classe
`RealImageTests` e confirme que o probe passa sem adicionar `privileged`,
capabilities, socket Docker ou uma política de sandbox mais ampla.

## Ativação

Edite `/opt/data/config.yaml` no volume persistente:

```yaml
agent_runtime:
  enabled: true
  codex_binary: /usr/local/bin/codex
  codex_version: 0.153.4
  allowed_directories:
    - /projects/example
  broad_access_enabled: false
```

Cada diretório permitido precisa existir no container e ser montado
explicitamente em `compose.yaml`, preferencialmente somente leitura quando essa
for a política desejada:

```yaml
services:
  kairos:
    volumes:
      - /srv/projects/example:/projects/example:rw
```

Não monte uma raiz ampla para contornar a allowlist. Uma sessão
`workspace_write` pode escrever apenas no projeto canônico cadastrado e mantém
rede e diretórios temporários fora da política. `broad_access` exige
`broad_access_enabled: true` e consentimento explícito na criação da sessão.

Depois de alterar a configuração, reinicie somente a instalação de teste e
confirme:

```console
kairos runtime status --json
kairos runtime session create --cwd /projects/example --sandbox workspace_write --json
```

Se o status retornar `unavailable`, consulte primeiro os logs do serviço s6 e
valide a capacidade de namespaces da plataforma. Não habilite `broad_access`
como fallback. A mensagem pública deliberadamente não reproduz o stderr do
`bwrap`, que pode conter detalhes locais.

Autenticação por API key solicita o segredo de forma interativa e não o aceita
como argumento. Os modos ChatGPT imprimem o fluxo oficial para o operador:

```console
kairos runtime login --method apiKey
kairos runtime login --method chatgpt
kairos runtime login --method chatgptDeviceCode
kairos runtime logout
```

## Verificação local

O smoke fake não usa rede, login ou turnos pagos:

```console
uv run pytest -q tests/test_runtime_e2e.py
```

O aceite real é deliberadamente excluído da suíte normal e do CI. Ele cria um
`KAIROS_HOME` descartável e dois projetos temporários, autentica com uma chave
dedicada fornecida apenas para o teste, executa turnos, reinicia host e filho e
tenta logout inclusive no caminho de cleanup:

```console
KAIROS_RUNTIME_LIVE=1 \
KAIROS_RUNTIME_LIVE_API_KEY='chave-dedicada' \
uv run pytest -q -m runtime_live tests/test_runtime_live.py
```

Use `KAIROS_RUNTIME_LIVE_CODEX` para apontar outro binário 0.153.4. Nunca rode
esse teste com autenticação pessoal herdada. A ausência da chave dedicada é um
skip explícito, não um aceite.
