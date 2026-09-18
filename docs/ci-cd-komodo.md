# CI/CD: deploy automático no Komodo

Guia do pipeline que leva a `main` verde até a stack rodando — o job `deploy`
do CI dispara o `DeployStack` via API REST do Komodo e um smoke pós-deploy
confirma a saúde da stack no host. É um procedimento operacional: sua existência
não registra que a implantação tenha sido executada.

## Arquitetura

```
push/merge em main ──> CI (8 jobs) ──verde──> job deploy
                                              │
        POST /execute/DeployStack (X-Api-Key) ▼
        POST /read/GetUpdate (poll até Complete+success)
                                              │
                                    Komodo Core (remoção)
                                              │
                                    periphery (host): git clone + docker compose build/up
                                              ▼
                        smoke pós-deploy: GET /api/health no host
```

Peças:

- **Job `deploy`** (`.github/workflows/ci.yml`): roda só na `main`
  (`push`/`workflow_dispatch`), atrás dos 8 jobs de verificação. Executa
  `scripts/komodo-deploy.sh`.
- **`scripts/komodo-deploy.sh`**: chama `POST /execute/DeployStack`
  `{"stack": "kairos"}` com `X-Api-Key` (+`X-Api-Secret` se existir) e faz
  **poll** de `POST /read/GetUpdate` até o Update terminar. Fecha com erro
  (fail-closed) se não houver segredo, se o Komodo recusar (401/403/...) ou se
  o Update terminar em falha ou timeout — nunca "reporta sucesso" sem o deploy
  ter terminado.
- **`scripts/smoke-deploy.sh`**: consulta `GET /api/health` (default
  `http://127.0.0.1:9119/api/health`) no host da stack e fecha com erro se o
  status divergir de `ok`, se o corpo não for JSON esperado ou se não houver
  resposta. Roda **no host** (action/procedure do Komodo), não no runner do
  GitHub.

Segredos passam por ambiente (`KOMODO_API_KEY`, `KOMODO_API_SECRET`), nunca por
argumento — o valor não aparece em `ps` e o script não o imprime.

## Setup único

### 1. Chave de API no Komodo

No Core, gere uma API key para um usuário com permissão de deploy na stack
`kairos`. A chave pode ser key-only ou key+secret; se tiver secret, ele vai no
`KOMODO_API_SECRET`.

Valide a chave e o alcance antes de configurar o GitHub:

```sh
curl -fsS -X POST https://SEU-CORE/execute/DeployStack \
  -H 'Content-Type: application/json' \
  -H "X-Api-Key: SUA_CHAVE" \
  -d '{"stack":"kairos"}'
```

A resposta é um `Update` com `_id.$oid` (o id de acompanhamento). Um 401/403
desta forma aponta para chave ou permissão erradas — corrija antes de seguir.

### 2. Acesso do runner ao Core (obrigatório)

Os runners do GitHub **não alcançam** rede privada (a stack publica só em
`100.87.25.101:9119`, Tailscale). O `KOMODO_HOST` precisa ser uma URL que o
runner atinja:

- **Opção A (recomendada):** expor o Core por um ingress/reverse proxy público
  com TLS (ex.: Tailscale Funnel + domínio), servindo as rotas `/execute` e
  `/read`.
- **Opção B:** rodar um self-hosted runner dentro do tailnet e apontar
  `KOMODO_HOST` para `http://100.87.25.101:9120` local.

Sem isso o job falha com erro de configuração barulhento — deploy sem gatilho
real não é fingido como sucesso.

### 3. Segredos e variável no GitHub

No *Settings → Secrets and variables → Actions* do repositório:

| nome | tipo | valor |
|---|---|---|
| `KOMODO_HOST` | secret | URL do Core alcançável pelo runner (sem barra final) |
| `KOMODO_API_KEY` | secret | a chave de API |
| `KOMODO_API_SECRET` | secret | o segredo de API em chaves key+secret (opcional) |
| `KOMODO_STACK` | variable | nome da stack; se ausente, o job usa `kairos` |

### 4. Smoke pós-deploy no Komodo

O job do GitHub valida **que o deploy terminou com sucesso**, mas não que a
stack responde — o smoke faz isso, e roda no host (a porta 9119 é Tailscale).
No Komodo:

- **Procedure (recomendado):** crie uma procedure que rode no host com o
  processo `bash <clone-do-repositorio>/scripts/smoke-deploy.sh` (no caminho
  do clone que o periphery mantém) e mande executá-la após um deploy terminado.
- **Action (em container):** use uma imagem com `curl` + `python3` (ex.
  `python:3.11-alpine` mais `apk add curl`), `network: host`, e aponte
  `KAIROS_HEALTH_URL` para `http://100.87.25.101:9119/api/health`.

O smoke também é executável na mão, de dentro do host:

```sh
bash scripts/smoke-deploy.sh                                   # default 127.0.0.1:9119
KAIROS_HEALTH_URL=http://127.0.0.1:9119/api/health bash scripts/smoke-deploy.sh
```

## Como o deploy funciona

1. `push` em `main` (ou `workflow_dispatch` na `main`) com os 8 jobs verdes
   dispara o job `deploy`.
2. `scripts/komodo-deploy.sh` envia `DeployStack` e imprime o `update` id.
3. O script pole `GetUpdate` (intervalo `KOMODO_POLL_INTERVAL`, default 10s) até
   `Complete`. Em `Complete`+`success=true` saí 0; em falha, imprime os logs do
   Update (a causa está lá — o Update não tem campo `error`) e saí 1.
4. O smoke (action/procedure) confirma a saúde da stack no host.

## Reprodução local e testes

O script é o mesmo que o CI chama; dispará-lo à mão com o ambiente do GitHub
reproduz o passo:

```sh
KOMODO_HOST=https://SEU-CORE \
KOMODO_API_KEY=SUA_CHAVE \
KOMODO_STACK=kairos \
bash scripts/komodo-deploy.sh
```

Os testes (`tests/test_komodo_deploy.py`) rodam os dois scripts de verdade
contra um servidor HTTP fake que imita a API do Komodo e o `/api/health` — não
tocam produção. Cobre: sucesso, falha do Update, timeout, chave inválida,
segredos ausentes, smoke ok/ruim/fora do ar.

## Falhas e rollback

| sintoma | onde procurar | ação |
|---|---|---|
| job `deploy` falhou no trigger | step do GHA: msg "Komodo recusou (HTTP …)" | chave/permissão no Core ou `KOMODO_HOST` inalcançável |
| job esperou e deu timeout | "não terminou em Ns" | Update no Komodo → logs do build/up; `docker compose ps` no host |
| Update falhou | script imprime os logs do Update | inspecione o último estágio (build de imagem, healthcheck de serviço) |
| smoke vermelho na procedure | saída do smoke no Komodo | subiu mas não responde? verifique a stack e o bind 9119 |

Rollback = reverter o commit na `main` e deixar o CI redeployar (ou disparar
`workflow_dispatch` na `main`). Como a stack está em modo Git, o deploy sempre
segue o HEAD da `main` — o rollback é um revert, não uma operação à parte.