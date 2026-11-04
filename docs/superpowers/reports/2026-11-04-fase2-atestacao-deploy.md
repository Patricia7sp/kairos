# E0.2 — evidência parcial de procedência e transporte do deploy

Coleta de leitura em 04/11/2026, após revisão do PR #132 no SHA `7b6346d`. Complementa a [matriz E0](2026-11-02-fase2-plataforma.md). Nenhum rebuild, redeploy, login, modificação de listener ou alteração de credenciais foi realizado. O gate E0 continua pendente.

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
| WebSockets existentes `/ws/chat`, `/ws/runtime`, `/api/events`, sem credenciais e Origin do próprio Kairos | Handshake recusado com HTTP 403 nos três caminhos |

São verificações pontuais de negação e headers. Ausência de ACAO em health não comprova defesa CSRF/origem de todas as rotas; a recusa de WebSocket sem credenciais não comprova sessão válida, origem cruzada autenticada ou reconexão. Uma consulta inicial ao caminho inexistente `/api/runtime/events` também retornou 403 e foi excluída dessa evidência; o endpoint runtime real `/ws/runtime` foi consultado depois. Login/logout/revogação, reconexão e renderização no Mac não foram exercitados nesta coleta. Não usar CORS como substituto de autenticação/autorização.

Foi solicitada uma sonda autenticada com token protegido somente em memória. A revisão automática de aprovação rejeitou o acesso ao segredo por ausência de autorização explícita para lê-lo e usá-lo fora do fluxo normal; a ação não foi executada. Foi oferecida a alternativa de usar a sessão já aberta no navegador. Autenticação e métricas continuam sem resultado até execução por um método autorizado; não reclassificar GETs públicos como teste autenticado.

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

Mesmo com state ready, faltam os controles/ACK persistidos, correlação de evento e amostras renderizadas dos cenários E0.3. Não derivar `controle_local_ms` de GET de autenticação ou de simples atualização de registros. A tela Registros faz leitura via `/api/logs`; não é por si só um controle de aceitação persistido.

## Pendências do E0

- Vínculo verificável do build/receita/dependências com o artefato promovido e os perfis de produção exigidos.
- URL efetiva identificada; falta validar/aceitar o transporte HTTP sobre tailnet e a sessão/origem/reconexão no acesso real, sem expor tokens.
- E0.3: amostras autenticadas e renderizadas; metadados junto de `controle_local_ms` devem explicitar navegador–servidor e inclusão de rede. Não produzir percentis a partir destas sondas HTTP ou dos tempos de testes.
- Aceite dos limites e rito do timebox em 09/11: aceite formal, extensão justificada com nova data ou recusa; vencimento não aprova.
