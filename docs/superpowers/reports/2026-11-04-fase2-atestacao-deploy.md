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
- O endereço HTTP usado nas sondas do servidor é `http://100.87.25.101:9119`. A URL realmente aberta no navegador do Mac foi solicitada e continua pendente; outro proxy/caminho não foi descartado. Não alterar Serve para atender Kairos como consequência da coleta.

Tailscale documenta criptografia entre nós e distingue essa camada do HTTPS reconhecido pelo navegador ([HTTPS no tailnet](https://tailscale.com/docs/how-to/set-up-https-certificates)). Isso não comprova qual caminho o Mac usa, ACL efetiva, sessão segura no navegador ou equivalência de HTTP e HTTPS. A rota TLS/terminação do acesso escolhido precisa ser identificada e validada no caminho real.

## Sondas sem autenticação

GETs no endereço HTTP acima, sem cookies ou tokens; somente status, headers selecionados e booleanos de autenticação foram registrados. Nenhum conteúdo de configuração foi exportado.

| Sonda | Resultado observado |
| --- | --- |
| `/api/auth/me` | HTTP 200, authenticated false, auth_required true; nenhum Set-Cookie |
| `/api/config` | HTTP 401, nenhum Set-Cookie |
| `/api/health` com Origin `https://origin-invalid.example` | HTTP 200, sem Access-Control-Allow-Origin e sem Set-Cookie |

São verificações pontuais de negação e headers. Ausência de ACAO em health não comprova defesa CSRF/origem de todas as rotas, validação de WebSocket ou sessão autenticada. Login/logout/revogação, reconexão e renderização no Mac não foram exercitados nesta coleta. Não usar CORS como substituto de autenticação/autorização.

## Pendências do E0

- Vínculo verificável do build/receita/dependências com o artefato promovido e os perfis de produção exigidos.
- URL efetiva do navegador, transporte/terminação, sessão/origem/reconexão no acesso real, sem expor tokens.
- E0.3: amostras autenticadas e renderizadas; metadados junto de `controle_local_ms` devem explicitar navegador–servidor e inclusão de rede. Não produzir percentis a partir destas sondas HTTP ou dos tempos de testes.
- Aceite dos limites e rito do timebox em 09/11: aceite formal, extensão justificada com nova data ou recusa; vencimento não aprova.
