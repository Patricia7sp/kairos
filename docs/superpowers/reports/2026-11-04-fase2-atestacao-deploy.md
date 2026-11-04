# E0.2 — evidência parcial de procedência e transporte do deploy

Coletas em 04/11/2026, após revisão do PR #132 no SHA `7b6346d`. Complementam a [matriz E0](2026-11-02-fase2-plataforma.md). As primeiras sondas foram sem sessão; após autorização explícita da usuária foram feitos login de teste, consultas de leitura e testes de tickets WebSocket. Nenhum redeploy, logout global, modificação de listener ou alteração de credenciais foi realizado. O gate E0 continua pendente.

## CI do SHA revisado

O [run 41758676083](https://github.com/Patricia7sp/kairos/actions/runs/41758676083) concluiu: testes, imagem/integração, sessões Docker, frontends, lint, recall, lock e Sonar com sucesso. Deploy e benchmark opt-in pulados conforme workflow. Isso vale para `7b6346d`, não antecipa os checks de commits documentais posteriores.

## Identidade observada e comparação de arquivos

| Serviço | ID de imagem em execução | Resultado |
| --- | --- | --- |
| Kairos | `sha256:cea250e02c30c4416f9a56c8f6db8d26fe0c5d80413835b5552e7e6f2917bb65` | healthy; labels OCI revision/source ausentes |
| Broker | `sha256:e96a421fbe833e598301650fa0c7d270e4bb016fabc18bb1aa288e76bf7123f4` | healthy; labels OCI revision/source ausentes |

A consulta `docker image inspect` do Kairos retornou os seguintes campos, registrados separadamente:

| Campo | Valor bruto observado |
| --- | --- |
| `Id` | `sha256:cea250e02c30c4416f9a56c8f6db8d26fe0c5d80413835b5552e7e6f2917bb65` |
| `RepoDigests` | `["kairos@sha256:cea250e02c30c4416f9a56c8f6db8d26fe0c5d80413835b5552e7e6f2917bb65"]` |
| `Created` | `2026-11-02T22:54:50.757062707Z` |

São campos distintos; o conteúdo retornado neste daemon não os torna conceitos intercambiáveis. Nenhum deles demonstra por si só o commit/receita que produziu o artefato. A revisão Git não está declarada e o deploy Komodo anterior não forneceu `commit_hash` no log consultado.

Foi comparado o conteúdo esperado do produto em `c6a083f51e12aaddeac8720185ecc55a27d010de` com os arquivos correspondentes em `/opt/kairos` dos dois containers. Método executado:

1. Enumerar `git ls-tree -rz` da referência; selecionar blobs dos diretórios `kairos_*`, `skills/`, `locales/`, `native/`, `docker/` e os arquivos `pyproject.toml`, `README.md`, conforme os inputs de aplicação copiados pelo Dockerfile.
2. Obter cada blob com `git cat-file blob` e calcular SHA256 por caminho. Foram selecionados 1.519 arquivos esperados.
3. Em cada container, executar somente Python da venv com `docker exec -i --user 10000:10000`, opções `-I -B`, recebendo a lista de caminhos e hashes por stdin. Recusar resolução fora de `/opt/kairos`; para symlink comparar o texto do link; para arquivo regular comparar bytes. Não importar aplicação, ler ambientes ou percorrer volumes de dados/credenciais.
4. Comparar os hashes e gerar o SHA256 do manifesto JSON `{caminho: sha256}`, ordenado por chave, separadores `(',', ':')`, sem espaços.

| Comparação | Esperados | Iguais | Ausentes | Diferentes | Exit |
| --- | ---: | ---: | ---: | ---: | ---: |
| Kairos versus fonte `c6a083f` | 1.519 | 1.519 | 0 | 0 | 0 |
| Broker versus fonte `c6a083f` | 1.519 | 1.519 | 0 | 0 | 0 |

SHA256 do manifesto esperado e dos dois manifestos observados: `9da24d53220ae7478f50ab256a6b844813ac333cc4370882d7d5adecf2609d39`.

**Limite da evidência:** correspondência dos arquivos esperados de aplicação, ampliando os três arquivos do H0 já comparados. Não detecta arquivos adicionais, não verifica dependências instaladas, bibliotecas/binaries gerados, camadas/base, flags/ambiente de build ou cadeia de promoção. Não é atestação integral da imagem nem prova criptográfica de procedência do build. O Dockerfile consultado instala com pip editável e não copia `uv.lock`; não inferir instalação frozen pelo lock do repositório. Fechar o vínculo integral requer evidência de build/artefato promovido, além de labels declarativas; não reclassificar a lacuna como resolvida por esses hashes.

### Checkout operacional e deriva de dependências confirmada

Os labels Compose de ambos os containers indicam projeto `kairos`, diretório `/etc/komodo/stacks/kairos` e `compose.yaml` nesse diretório. A consulta de HEAD desse checkout retornou `c6a083f51e12aaddeac8720185ecc55a27d010de`. O Git recusou inicialmente a propriedade diferente; o HEAD foi consultado com exceção de safe.directory apenas nesse comando de leitura, sem alterar a configuração global nem arquivos. Isso adiciona vínculo operacional com o checkout, sem atestar retrospectivamente o build.

Comparação de `importlib.metadata.distributions()` dos containers com `uv.lock`, normalizando nomes de distribuição: **16 versões divergentes em cada container**, além de `opentelemetry-api` e `tzdata` fora do lock. Pip e setuptools foram identificados separadamente como ferramentas do ambiente. Nomes com ponto, hífen e underscore foram normalizados; não tratar jaraco/backports como ausentes apenas pela grafia.

| Distribuição | Instalada em app/broker | Lock |
| --- | --- | --- |
| anyio | 4.16.0 | 4.14.2 |
| certifi | 2026.10.23 | 2026.7.22 |
| click | 8.5.0 | 8.4.2 |
| cryptography | 50.0.2 | 50.0.0 |
| fastapi | 0.162.0 | 0.141.1 |
| idna | 3.20 | 3.19 |
| importlib-metadata | 10.1.0 | 9.0.0 |
| more-itertools | 11.2.1 | 11.1.0 |
| pycparser | 3.12 | 3.0 |
| pydantic | 2.14.2 | 2.13.4 |
| pydantic-core | 2.50.2 | 2.46.4 |
| python-dateutil | 4.0.0 | 2.9.0.post0 |
| starlette | 1.10.0 | 1.6.0 |
| uvicorn | 0.54.0 | 0.52.4 |
| websockets | 17.4 | 17.0.1 |
| zipp | 4.1.2 | 4.1.0 |

É divergência demonstrada do ambiente testado, não prova de vulnerabilidade específica dessas versões. A causa observada é o build por pip fora do lock, que também estava excluído do contexto por `.dockerignore`. A correção está no [PR #133](https://github.com/Patricia7sp/kairos/pull/133), branch separada `fix/build-dependencias-lock`: contexto inclui lock, uv oficial fixado por digest, sync --locked sem extras dev; sem alterar a produção. O teste de regressão real falhou na imagem implantada e passou nas novas imagens isoladas da aplicação/broker. Um contexto sintético com projeto/lock divergentes foi recusado; uv ficou ausente da imagem final. A lacuna de transitivos dev apontada na revisão foi reproduzida com imagem mutante e corrigida no teste. CI integral local concluído com exit 0; após os achados do primeiro Sonar, a receita restringe dependências de terceiros a wheels e separa o build editável local com backend temporário fixado por hashes. Regressões afetadas repetidas: 45 passed/26 subtests de Dockerfile/imagem; 26 passed/1 skipped de sessões offline; contrato do lock no broker aprovado. SHA corretivo `b93fb42`, [CI remoto e Sonar aprovados](https://github.com/Patricia7sp/kairos/actions/runs/41774274910): 4363 passed, 24 skipped, 35 deselected e 6657 subtests; promoção e revalidação do deploy continuam pendentes. Este PR continua somente documental.

## Transporte observado no servidor

- Docker publica `9119/tcp` de Kairos somente em `100.87.25.101:9119`. O broker não publica essa porta no host.
- Tailscale local: BackendState Running, Self online; IP de tailnet coincide com o bind. Consulta de Serve indica HTTPS na porta 443, rota `/` encaminhada para `http://127.0.0.1:8090`, Funnel false. Essa configuração não aponta para a porta 9119 do Kairos e não demonstra TLS no seu acesso.
- A usuária confirmou a URL efetiva: `http://100.87.25.101:9119/models#/registros`. O acesso escolhido usa HTTP direto na porta 9119, sem TLS de aplicação nesse caminho. O fragmento `#/registros` fica no navegador; o servidor recebe `/models`. Não alterar Serve para atender Kairos como consequência da coleta.

Tailscale documenta criptografia entre nós e distingue essa camada do HTTPS reconhecido pelo navegador ([HTTPS no tailnet](https://tailscale.com/docs/how-to/set-up-https-certificates)). A URL esclarece o caminho, mas não comprova ACL efetiva, sessão segura no navegador ou equivalência de HTTP e HTTPS. Não há terminador TLS do Kairos identificado nesse acesso; validar e aceitar explicitamente o transporte efetivo, sem chamá-lo de HTTPS.

GET público de `/models`: HTTP 200, `text/html; charset=utf-8`, HTML carrega `/ui/js/app.js`, SHA256 `5c4aab6a0c703407b9df3f447e4149a23c37bfdc9ffdeba7122980cb7c19f216`. O [shell servido](../../../kairos_web/ui/js/app.js) usa hash e inclui a rota `registros`; não é necessário confundir esse caminho com um frontend React diferente. Isso identifica o frontend, não prova renderização autenticada no Mac.

## Imagem worker efetiva da stack

O container mantenedor `kairos-runtime-worker-image` referencia o ID imutável `sha256:05096dbc7992f32719b7f8d97c8d122853a83c631e52c0a6947f82ddfbdb2f0a`, diferente da imagem de teste construída anteriormente. Inspeção: running, usuário `10000:10000`, AppArmor `docker-default`, rede `none`, rootfs somente leitura. `codex --version` retornou `codex-cli 0.154.0`, exit 0, com aviso de que CODEX_HOME não existe no mantenedor; isso não foi tratado como inicialização de sessão bem-sucedida.

O teste existente `test_real_codex_executes_with_standard_docker_isolation` foi repetido usando esse ID como entrada da imagem, via plugin de coleta pytest que substitui apenas a constante IMAGE do módulo, sem modificar o teste nem simular Docker/isolamento: **1 passed em 2,01 s**, exit 0. Opt-ins: external=1, runtime_sandbox=0, worker_sandbox=0. O worker criado pelo teste foi descartável, com fixture sintética; nenhum token/provider/modelo pago foi usado. Listagem do label prototype vazia após o teste; Kairos e broker healthy.

Isso vincula o cenário padrão de isolamento à imagem efetivamente mantida pela stack, além do ensaio da imagem recém-construída. Não atesta o perfil sandbox interno, todos os consumidores/configurações de produção ou procedência integral do build.

## Sondas sem autenticação

GETs no endereço HTTP acima, sem cookies ou tokens; somente status, headers selecionados e booleanos de autenticação foram registrados. Nenhum conteúdo de configuração foi exportado.

| Sonda | Resultado observado |
| --- | --- |
| `/api/auth/me` | HTTP 200, authenticated false, auth_required true; nenhum Set-Cookie |
| `/api/config` | HTTP 401, nenhum Set-Cookie |
| `/api/health` com Origin `https://origin-invalid.example` | HTTP 200, sem Access-Control-Allow-Origin e sem Set-Cookie |
| Preflight OPTIONS de `/api/auth/ws-ticket`, Origin estrangeiro, método solicitado POST/content-type, sem credenciais | HTTP 401, sem Access-Control-Allow-Origin e sem Set-Cookie; recusa da camada de autenticação, não prova independente da validação de origem |
| WebSockets existentes `/ws/chat`, `/ws/runtime`, `/api/events`, sem credenciais e Origin do próprio Kairos | Handshake recusado com HTTP 403 nos três caminhos |

São verificações pontuais de negação e headers. Ausência de ACAO em health não comprova defesa CSRF/origem de todas as rotas; a recusa de WebSocket sem credenciais não comprova sessão válida, origem cruzada autenticada ou reconexão. Uma consulta inicial ao caminho inexistente `/api/runtime/events` também retornou 403 e foi excluída dessa evidência; o endpoint runtime real `/ws/runtime` foi consultado depois. Login/logout/revogação, reconexão e renderização no Mac não foram exercitados nesta coleta. Não usar CORS como substituto de autenticação/autorização.

Foi solicitada uma sonda autenticada com token protegido somente em memória. A revisão automática rejeitou inicialmente o acesso ao segredo por ausência de autorização explícita; aquela ação não foi executada. A usuária autorizou depois expressamente a leitura e o uso para os testes. Com essa autorização, o token foi usado somente em memória, sem saída, arquivo, variável de ambiente ou exportação de registros privados.

| Sonda autorizada | Resultado observado |
| --- | --- |
| Login de teste pelo endereço HTTP confirmado | HTTP 200, authenticated true |
| Cookie de sessão emitido | HttpOnly true, SameSite lax, Secure false |
| `/api/auth/me` com cookie da sessão de teste | HTTP 200, authenticated true, auth_required true |
| `/api/runtime/status` | HTTP 200, enabled true, state ready; caminhos de projetos não exportados |
| `/api/logs?limit=1` | HTTP 200, state ready, um evento; conteúdo não exportado |
| Ticket novo de `/api/auth/ws-ticket` em `/ws/chat` | Ping/pong passou, sem mensagem de modelo |
| Reutilização do ticket já consumido | Handshake HTTP 403 |
| Reconexão com segundo ticket novo | Ping/pong passou |

CookieJar e credenciais em memória foram descartados ao terminar. Não foi chamado logout, pois essa rota revoga tickets pendentes globalmente. O resultado valida esse fluxo no servidor contra a URL real, não a sessão existente do Mac, os fluxos completos de runtime/replay, logout/revogação global ou renderização. Os tempos de execução dessas sondas não são amostras do benchmark E0.3. `Secure=false` é observação do acesso HTTP, não aprovação de equivalência com HTTPS.

### Alternativa de coleta no navegador já autenticado

Este bloco pode ser executado pela usuária no Console das ferramentas de desenvolvedor da página aberta no Mac. Faz somente GET de `/api/auth/me` usando o cookie da própria sessão pelo mecanismo normal do navegador. Não lê cookies, storage ou token e não exporta registros privados. Imprime apenas os campos de diagnóstico selecionados; serve como pré-verificação, não como benchmark E0.3.

```javascript
(async () => {
  const result = { scope: "e0_browser_preflight", benchmark_executed: false };
  if (location.origin !== "http://100.87.25.101:9119") {
    console.log(JSON.stringify({ ...result, state: "error", error: "unexpected_origin" }));
    return;
  }
  Object.assign(result, {
    protocol: location.protocol,
    secure_context: globalThis.isSecureContext,
    user_agent: navigator.userAgent,
    registros_route: location.hash === "#/registros",
    logs_view_present: Boolean(document.querySelector("[data-logs-content]")),
  });
  try {
    const response = await fetch("/api/auth/me", {
      credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    result.auth_http_status = response.status;
    if (!response.ok) {
      result.state = "error";
      result.error = "auth_probe_http_error";
    } else {
      const auth = await response.json();
      result.authenticated = auth.authenticated === true;
      result.auth_required = auth.auth_required === true;
      result.state = result.authenticated && result.auth_required ? "ready" : "unavailable";
    }
  } catch (error) {
    result.state = "error";
    result.error = error.name || "probe_failed";
  }
  console.log(JSON.stringify(result, null, 2));
})();
```

Mesmo com state ready, faltam os controles/ACK persistidos, correlação de evento e amostras renderizadas dos cenários E0.3. Não derivar `controle_local_ms` de GET de autenticação ou de simples atualização de registros. A tela Registros faz leitura via `/api/logs`; não é por si só um controle de aceitação persistido. O journal dessa tela não fornece `event_id`; o runtime tem eventos identificados e outra projeção. Os controles de turno existentes podem iniciar trabalho real e a resposta de aprovação pode esperar o efeito externo. Não disparar esses caminhos como carga sintética em produção. Falta definir/testar a coleta com fixture controlada, ACK após persistência e correlação até a projeção servida, além de executar as amostras no cliente real.

## Janela observada de recursos — sem carga induzida

Em 04/11, oito rodadas de `docker stats --no-stream` entre `04:40:41Z` e `04:41:30Z`, com pausa de 5 s após cada comando. Não se comprovou ociosidade dos serviços; não houve carga de benchmark induzida nesta janela. Percentuais são os informados por Docker, sem normalização para o host inteiro.

| Horário UTC | CPU Kairos | CPU broker |
| --- | --- | --- |
| 04:40:41 | 0,31% | 0,01% |
| 04:40:48 | 0,31% | 0,01% |
| 04:40:55 | 0,35% | 0,06% |
| 04:41:02 | 0,18% | 72,99% |
| 04:41:09 | 0,38% | 0,01% |
| 04:41:16 | 0,26% | 0,07% |
| 04:41:23 | 0,35% | 0,02% |
| 04:41:30 | 0,28% | 0,01% |

Kairos: 94,09 MiB em todas as amostras, 15 PIDs. Broker: 86,81 MiB em sete amostras e 87,39 MiB na de 04:41:02, 14 PIDs. O máximo amostrado de CPU do broker não informa pico entre leituras, duração nem causa; também não invalida a observação anterior de 100,80%.

No encerramento: mesmos IDs de imagem registrados acima, ambos healthy, RestartCount 0 e State.OOMKilled false. São campos do estado dos containers atuais, não histórico completo de OOM do host. `/proc/meminfo`: MemTotal 7722434560, MemAvailable 3841167360, SwapTotal 4294963200 e SwapFree 3804438528 bytes. Filesystem que contém a stack: total 1004298338304, usado 125757366272 e livre para o usuário 836592631808 bytes; valores de fim da janela, não picos nem garantia de capacidade sob carga.

Esta série amplia o registro de recursos do executor; não fornece ACK, renderização, percentis, aceitação de carga ou evidência de navegador do E0.3.

## Pendências do E0

- Corrigir e promover o build que não consumia o lock; verificar novamente dependências/artefato e perfis de produção exigidos no deploy final.
- URL, login de teste e ciclo de tickets identificados/validados no servidor; faltam aceite do transporte HTTP sobre tailnet e evidência de sessão/origem/reconexão da interface no navegador do Mac, sem expor tokens.
- E0.3: amostras autenticadas e renderizadas; metadados junto de `controle_local_ms` devem explicitar navegador–servidor e inclusão de rede. Não produzir percentis a partir destas sondas HTTP ou dos tempos de testes.
- Aceite dos limites e rito do timebox em 09/11: aceite formal, extensão justificada com nova data ou recusa; vencimento não aprova.
