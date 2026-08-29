# Providers, modelos e Chat

O Chat da SPA e o comando `kairos chat` usam o mesmo `InteractionService`.
Provider e modelo são resolvidos uma vez por turno; a seleção efetiva, as
mensagens, o uso e o custo conhecido são persistidos no `state.db`.

## Preparação

Confira o cofre antes de configurar uma credencial:

```bash
kairos auth vault-status --json
```

Na SPA, abra **Provedores**, informe a chave e use **Testar conexão**. A chave
vai direto ao keyring ou ao cofre criptografado, o campo é limpo após o envio e
as APIs nunca devolvem o segredo nem um fragmento dele.

Ollama não exige credencial. Ele fica utilizável quando o daemon local responde
no endpoint configurado. O Kairos não habilita silenciosamente outro provider
quando a seleção falha.

## Modelos e OpenRouter

A página **Modelos** mostra provider, capacidades, contexto, estabilidade,
origem do catálogo e preço conhecido. Previews ficam ocultos por padrão.
OpenRouter oferece o filtro **Somente gratuitos**; gratuidade é atributo do
catálogo, não promessa de disponibilidade ou limite da conta.

O routing padrão do OpenRouter nega coleta de dados, exige parâmetros
compatíveis e permite fallback apenas entre endpoints do mesmo modelo. O
Kairos não troca o ID escolhido por outro modelo pago.

Trocar a seleção afeta o próximo turno e não reinicia a conversa. A interface
também permite começar uma conversa nova com a escolha.

## Terminal

```bash
kairos chat --session homologacao "olá"
kairos chat --session homologacao --provider openrouter --model openrouter/free "olá"
kairos chat --session homologacao --json "resuma a conversa"
```

`--json` produz NDJSON no protocolo v1, igual ao WebSocket. Não inclua chaves em
argumentos de linha de comando.

## Verificação e deploy

Sem consumir créditos:

```bash
uv run pytest -q --deselect tests/test_container.py::RealImageTests
npm --prefix web test
npm --prefix web run typecheck
docker compose config --quiet
curl -fsS http://100.87.25.101:9119/api/health
```

Uma chamada real só deve ocorrer com credencial configurada e autorização
explícita. Antes do deploy, registre commit, imagem, saúde e estado do cofre sem
copiar identificadores sensíveis.

## Backup e rollback

Preserve o volume `kairos-data`, o arquivo de passphrase administrado e o
`web-token`. Rollback troca somente o checkout/imagem; nunca execute
`docker compose down -v`. Após voltar a versão, valide saúde, acesso ao banco e
`kairos auth vault-status --json` antes de reabrir o Chat.
