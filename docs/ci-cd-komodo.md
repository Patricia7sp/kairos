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

Uma API key do Komodo é **um par** `Key` + `Secret` (formato `K_…_K` /
`S_…_S`), criado em *Settings → API Keys → New API Key*. O `Secret` é mostrado
**uma única vez** — copie os dois. A autenticação REST usa os headers
`X-Api-Key` e `X-Api-Secret` **juntos**; sem o secret a chamada é recusada
("missing X-API-SECRET").

A chave não é "por stack" nem "por template": ela autentica **como o usuário
para o qual foi criada** (herdando as permissões dele), e `DeployStack` exige
`Execute` na Stack alvo. Uma chave do **admin** já cobre tudo; para menor
privilégio, crie um *service user* com `Execute` apenas na stack `kairos` (e
`Read` para o poll do update) e gere a chave para ele.

> Não confunda com a **chave de onboarding** que o Komodo mostra ao conectar um
> servidor (o `public key` do Core, `X-API-SIGNATURE`+`X-API-TIMESTAMP`). Esse é
> um handshake de assinatura para parear o Periphery — **não** é uma chave REST e
> não serve para o pipeline.

A stack `kairos` **já é um recurso cadastrado** neste Komodo (server
`hermesserver`, repo `Patricia7sp/kairos@main`). Não é preciso criar nada — só a
chave.

Validação **só-leitura** (host, auth e acesso à stack, sem publicar nada):

```sh
HOST=http://SEU-CORE
# 1) autentica e devolve os dados do Core
curl -fsS -X POST "$HOST/read/GetCoreInfo" \
  -H 'Content-Type: application/json' \
  -H "X-Api-Key: $KOMODO_API_KEY" \
  -H "X-Api-Secret: $KOMODO_API_SECRET" \
  -d '{}'
# 2) resolve a stack kairos (precisa Read; admin tem)
curl -fsS -X POST "$HOST/read/ListStacks" \
  -H 'Content-Type: application/json' \
  -H "X-Api-Key: $KOMODO_API_KEY" \
  -H "X-Api-Secret: $KOMODO_API_SECRET" \
  -d '{"query":{},"options":{"pagination":{"page":0,"per_page":50}}}'
```

Um 401/403 aponta para par key+secret errado ou usuário sem acesso — corrija
antes de seguir. O passo único que só valida num deploy real é o *trigger*
`DeployStack` (não há dry-run); ele é exatamente o que `komodo-deploy.sh`
dispara.

### 2. Acesso do runner ao Core (obrigatório)

Os runners `ubuntu-latest` do GitHub **não alcançam** rede privada (a stack e o
Core publicam só em `100.87.25.101`, Tailscale). O `KOMODO_HOST` precisa ser uma
URL que o runner atinja:

- **Opção A:** expor o Core por um ingress/reverse proxy público com TLS (ex.:
  Tailscale Funnel + domínio), servindo as rotas `/execute` e `/read` — mantém o
  job no runner do GitHub.
- **Opção B (em uso neste projeto):** rodar um self-hosted runner dentro do
  tailnet e apontar `KOMODO_HOST` para `http://100.87.25.101:9120`.

Por isso o job `deploy` do CI usa `runs-on: self-hosted`. **Registre o runner**
para esta repo (`settings → Actions → Runners → New self-hosted runner`) antes
de esperar o deploy — sem runner registrado o job fica pendurado em `Queued`.
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

- **Procedure (recomendado):** crie uma *production procedure* que rode no host
  (`hermesserver`) com o processo `bash /etc/komodo/stacks/kairos/scripts/smoke-deploy.sh`
  (o caminho do clone que o Periphery mantém), e a execute após um deploy
  terminado. O smoke responde no host em `127.0.0.1:9119`; para usar o endereço
  Tailscale, rode com `KAIROS_HEALTH_URL=http://100.87.25.101:9119/api/health`.
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
KOMODO_HOST=http://100.87.25.101:9120 \
KOMODO_API_KEY=SUA_CHAVE \
KOMODO_API_SECRET=SUA_CHAVE_SECRET \
KOMODO_STACK=kairos \
bash scripts/komodo-deploy.sh
```

## Estado verificado (2026-09-19)

Validado contra o Core real, **sem** executar o deploy:

- A stack `kairos` é recurso registrado (server `hermesserver`, repo
  `Patricia7sp/kairos@main`, status `running`) — nada a criar.
- API key `kairos-ci-deploy` criada e testada somente-leitura (`GetCoreInfo`,
  `ListStacks`, `GetUpdate`): autentica como o admin e resolve a stack.
- Formato do poll (`GetUpdate` → `status: Complete`, `success: True`,
  `_id.$oid`) conferido contra o Core real — casa com o `komodo-deploy.sh`.
- Segredos `KOMODO_API_KEY`, `KOMODO_API_SECRET`, `KOMODO_HOST` e a variável
  `KOMODO_STACK=kairos` configurados no repositório.
- `KOMODO_HOST=http://100.87.25.101:9120` (Opção B) — **exige o self-hosted
  runner dentro do tailnet**; registre-o antes do primeiro `workflow_dispatch`.

Ainda não verificado (exige runner registrado e primeiro disparo real): o
*trigger* `DeployStack` (não há dry-run) e o smoke pós-deploy no host.

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