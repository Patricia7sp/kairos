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
`Execute` na Stack alvo. Aqui o deploy usa menor privilégio: um *service user*
`kairos-ci` com permissão `Execute` na stack `kairos` (Execute inclui o `Read`
do poll do `GetUpdate` sobre a mesma stack), e o par Key+Secret
`kairos-ci-deploy` foi gerado **para esse service user**
(`CreateApiKeyForServiceUser`), não para o admin. A chave do admin usada na
primeira entrega foi **removida** depois que a troca foi validada por um deploy
de pipeline.

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
stack responde — o smoke faz isso.

> Use `https://kairos.taild36cf5.ts.net/api/health` nos dois smokes. A porta
> `127.0.0.1:9119` serve para diagnóstico local do backend no host; consultá-la
> não verifica o DNS, o certificado nem o encaminhamento do Service.

Implementação em uso (criada e validada no Core):

- **Action `kairos-smoke`** (script TypeScript/deno que roda no **Core** =
  host `hermesserver`): faz `fetch` em `https://kairos.taild36cf5.ts.net/api/health`,
  exige `HTTP 200` e `{"status":"ok"}`, e lança erro caso contrário.
- **Procedure `kairos-smoke-deploy`**: um estágio com `RunAction kairos-smoke`.
  Em sucesso deriva `Complete`/`success=true`; em falha, `Failed` com o log do
  fetch — exatamente o tipo de sinal que o Update do job `deploy` carrega.
- **Smoke pós-deploy automático na stack (em uso):** a stack `kairos` tem
  `post_deploy` (`SystemCommand`, `shell_mode: true`) que executa no **host**
  (periphery) imediatamente após o `Compose Up` — um laço **shell-only**
  (curl + `case`, até 60s) contra o mesmo endereço HTTPS. Uma resposta com
  `status=ok` permite ao Update terminar `Complete`/`success=true`;
  se não responder, o Update termina `success=false` com o motivo no estágio
  `Post Deploy`. O job `deploy` do CI depende apenas do poll do Update — o smoke
  passa a ser condição do próprio deploy. A procedure acima continua disponível
  para disparo manual.

> Armadilha já corrigida: o `post_deploy` roda no **container do periphery**, que
> **não tem `python3`** nem `jq` garantido. Fique em shell puro (curl + `case`/`grep`).

Para disparar na mão (ou após deploy) — CLI/API:

```sh
curl -fsS -X POST $HOST/execute/RunProcedure \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $JWT" \
  -d '{"procedure":"kairos-smoke-deploy"}'
```

Para verificar o mesmo caminho a partir do host, passe a URL do Service ao script:

```sh
KAIROS_HEALTH_URL=https://kairos.taild36cf5.ts.net/api/health bash scripts/smoke-deploy.sh
```

## Migração da Web para HTTPS privado

**Corte concluído em 22/11/2026 (UTC).** A stack usa `compose.yaml` e `compose.https.yaml`.
A [matriz de plataforma](superpowers/reports/2026-11-02-fase2-plataforma.md#corte-do-http-remoto-em-produção--22112026-utc) registra o resultado. O roteiro abaixo descreve a migração e sua reversão.
O destino escolhido é um [Tailscale
Service](https://tailscale.com/kb/1552/tailscale-services) próprio (`svc:kairos`),
com HTTPS em 443 no **nome do Service**. O HTTPS em 443 do **nome do nó** já
atende `127.0.0.1:8090`. Usar outra porta ou caminho nesse mesmo nome não
separa o cookie de sessão entre os dois serviços.

1. **Cadastrar somente o proxy.** O servidor é administrado por Tailscale SSH:
   sua identidade permanece a mesma. O projeto Compose `kairos-https` contém
   um único nó Tailscale em modo userspace, com socket e volume de identidade
   próprios. Ele não publica portas no host. O Service `svc:kairos` (`tcp:443`)
   e a tag `tag:kairos-host` já foram definidos no painel. **A tag é só para o
   proxy**, não para `hermesserver`. Conferir os grants efetivos: a regra atual
   `* → *` permite tráfego no tailnet, não restringe o acesso ao Mac.

   Na raiz do checkout no Linux, iniciar o projeto separado (não acrescentar
   este arquivo à lista de arquivos da stack principal):

   ```sh
   docker compose -p kairos-https -f compose.https-proxy.yaml up -d proxy
   ```

   Na primeira instalação, `NeedsLogin` é esperado. Pelo terminal privado do
   operador no servidor, executar **dentro do contêiner** e abrir no Mac o link
   de autenticação exibido. Não copiar esse link para a conversa. Não é preciso
   criar uma auth key. O volume `kairos-https_identidade` preserva o cadastro
   em reinícios/recriações; não refazer o login em cada deploy.

   ```sh
   docker exec -it kairos-https-proxy tailscale up --hostname=kairos-https-proxy --advertise-tags=tag:kairos-host --accept-dns=false --ssh=false
   ```

   Conferir em *Machines* que **kairos-https-proxy** está online com a tag e
   `hermesserver` segue sem ela. Antes de mudar a stack, registrar os arquivos,
   profiles e smokes atuais e o estado de Serve/Funnel do host.
2. **Abrir a transição.** Com a revisão que implementa `Secure` disponível
   na stack, selecionar nela estes três arquivos, nesta ordem, preservando o
   projeto, os profiles e o ambiente do Komodo. O projeto do proxy já deve ter
   criado a bridge interna `kairos-https-backend`. Só a aplicação se junta a
   ela, com o alias `kairos-web.invalid`; o broker continua na rede anterior.
   Conferir a composição antes de aplicar (Compose 2.24.4+ para `!override`):

   ```sh
   docker compose -f compose.yaml -f compose.https.yaml -f compose.https-transicao.yaml config --quiet
   ```

   Após o deploy, `docker port kairos 9119/tcp` deve listar somente
   `127.0.0.1:9119` e `100.87.25.101:9119`. O smoke antigo continua válido;
   conferir também `/api/health` em loopback **no host**. A autoridade HTTP
   legada exata é a única exceção a `Secure`. Para os demais hosts, login e
   logout usam `Secure` mesmo que o proxy encaminhe HTTP ao backend.
3. **Publicar no nome dedicado.** Antes de anunciar, confirmar que o proxy
   alcança o `/api/health` do Kairos pelo alias do Docker (HTTP 200, campos
   `app=kairos` e `status=ok`). `127.0.0.1` dentro do proxy é o próprio proxy.
   Conferir no painel que o Service está reservado para a stack, sem outro
   anunciante, e executar no **daemon do contêiner**:

   ```sh
   docker exec kairos-https-proxy wget -qO- http://kairos-web.invalid:9119/api/health
   docker exec kairos-https-proxy tailscale serve --service=svc:kairos --https=443 http://kairos-web.invalid:9119
   docker exec kairos-https-proxy tailscale serve status --json
   docker exec kairos-https-proxy tailscale funnel status --json
   ```

   Em *Services → kairos*, aprovar **kairos-https-proxy** se estiver pendente.
   Conferir o destino do Service, a rota anterior
   do nó para 8090 e a ausência de Funnel. Usar o **FQDN do Service** para
   validar o certificado, sem ignorar erros de TLS. Enquanto ambos os binds
   existem, apontar e executar o `post_deploy` **no periphery** e a action
   `kairos-smoke` **no Core** contra `https://<FQDN-do-Service>/api/health`.
   Ambos devem obter HTTP 200 e `status=ok` de seus próprios ambientes.
4. **Aceitar no Mac antes do corte.** Em janela privada, entrar pelo HTTPS,
   confirmar `/api/auth/me` autenticado e cookie `kairos_session` host-only,
   `Secure`, `HttpOnly` e `SameSite=Lax`. Verificar tráfego WSS, reconexão com
   ticket novo e recusa de reuso do primeiro ticket ainda dentro do TTL. Em
   janela combinada, confirmar que o logout remove o cookie e invalida um
   ticket ainda pendente: a revogação de tickets é global. Registrar resultados
   sem valores de cookies, tokens, tickets ou URLs que os contenham.
5. **Retirar o HTTP remoto.** Remover `compose.https-transicao.yaml` da stack
   no Komodo e reaplicar apenas `compose.yaml` e `compose.https.yaml`. Conferir
   com `docker port kairos 9119/tcp` que o único bind é `127.0.0.1:9119`.
   Com `KAIROS_WEB_HTTP_COOKIE_HOST=none`, o cookie de sessão é sempre `Secure`.
   Usar o HTTPS do Service para login e logout; login via HTTP não é suportado
   nem em loopback. O smoke local em `http://127.0.0.1:9119/api/health` continua
   disponível sem login. No Mac, conferir que uma nova conexão a
   `http://100.87.25.101:9119/api/health` não se estabelece (sem enviar
   credenciais), e repetir o login/reconexão no HTTPS. Confirmar os dois
   smokes, o serviço anterior em 443 e Funnel desativado. Registrar SHA, nome
   validado e resultados na [matriz E0](superpowers/reports/2026-11-02-fase2-plataforma.md).

**Reversão:** se o corte falhar e for necessário reabrir o acesso legado,
reaplicar a composição de transição. Verificar o acesso antigo e restaurar
os alvos anteriores dos dois smokes enquanto os dois binds existem. Para
desfazer a publicação, conferir que o Service ainda aponta para o destino
desta migração, executar `docker exec kairos-https-proxy tailscale serve drain svc:kairos`,
aguardar as conexões existentes e usar `docker exec kairos-https-proxy tailscale serve clear svc:kairos`.
Voltar à lista original de arquivos no Komodo, reaplicar e verificar binds,
smokes, Serve e Funnel contra o estado anterior. Só depois de desconectar a
aplicação da bridge, parar o projeto dedicado com `docker compose -p kairos-https -f compose.https-proxy.yaml down`
(sem `-v`, para preservar a identidade). O daemon Tailscale do host não participa da reversão.

## Como o deploy funciona

1. `push` em `main` (ou `workflow_dispatch` na `main`) com os 8 jobs verdes
   dispara o job `deploy`.
2. `scripts/komodo-deploy.sh` envia `DeployStack` e imprime o `update` id.
3. O script pole `GetUpdate` (intervalo `KOMODO_POLL_INTERVAL`, default 10s) até
   `Complete`. Em `Complete`+`success=true` saí 0; em falha, imprime os logs do
   Update (a causa está lá — o Update não tem campo `error`) e saí 1.
4. Smoke: a stack roda o `post_deploy` automaticamente — um laço shell-only no
   **host** (periphery) consulta `https://kairos.taild36cf5.ts.net/api/health` até 60s;
   só com `{"status":"ok"}` o Update termina `Complete`/`success=true` (falhou =
   `success=false` com o motivo no estágio `Post Deploy`). A procedure
   `kairos-smoke-deploy` (`RunAction kairos-smoke`, deno no Core) continua
   disponível para disparo manual.

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

## Estado verificado (2026-09-20)

- A stack `kairos` é recurso registrado (server `hermesserver`, repo
  `Patricia7sp/kairos@main`, status `running`) — nada a criar.
- **Runner self-hosted `hermesserver-kairos`** (labels `self-hosted,linux,kairos`,
  v2.337.0, `~/actions-runner`) registrado na repo e **online** no tailnet.
  Gerenciado como serviço systemd **de usuário**
  (`~/.config/systemd/user/actions.runner.kairos.service`, `systemctl --user
  enable --now`; `Linger=yes` no host garante o boot sem login); o auto-start por
  cron `@reboot` foi **removido**.
- API key do pipeline = par `kairos-ci-deploy` **do service user `kairos-ci`**
  (id `6aaf2b28…`, permissão `Execute` na stack `kairos`): autentica, resolve a
  stack e executa `DeployStack`, **sem privilégios de admin**. A chave do admin
  usada na primeira entrega foi deletada (`ListApiKeys` do admin = 0).
- Segredos `KOMODO_API_KEY`/`KOMODO_API_SECRET` = par do service user,
  `KOMODO_HOST=http://100.87.25.101:9120` (Opção B), variável
  `KOMODO_STACK=kairos`.
- **Deploy real pelo pipeline** (validação final, run `35479709833`): 10/10 jobs
  verdes; update `DeployStack` executado **por `kairos-ci`**
  (`6aaf2de5…`, `Complete`/`success=true`) com o estágio **`Post Deploy`**
  `success=true` (`smoke pos-deploy ok`).
- **Smoke pós-deploy automático validado** no caminho da stack: primeiro
  comando usava `python3` e falhou (periphery não tem python3); substituído por
  curl+`case` e revalidado. A procedure `kairos-smoke-deploy` (deno no Core)
  permanece validada (`Complete`/`success=true`; `smoke kairos ok:
  {"status":"ok",...}`).
- Deploys via CI em `6a8857a`, `0976e68` e no commit de docs seguinte — sempre
  com a stack terminando `deployed_hash == latest_hash`.

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
