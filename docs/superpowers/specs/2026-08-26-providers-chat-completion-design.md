# Conclusão de Providers, Modelos e Chat

**Data:** 2026-08-26  
**Estado:** implementação concluída; validação operacional contínua
**Escopo:** fases 3–6 de `2026-08-25-providers-modelos-chat-design.md`

## Objetivo

Concluir a experiência de Providers, Modelos e Chat do Kairos sobre uma única
rota de execução, usada pela Web e pelo terminal. A entrega moderniza todos os
providers previstos, adiciona OpenRouter como provider de primeira classe,
persiste a seleção efetiva de cada turno e migra o Chat para a SPA principal.

O resultado é considerado completo quando Chat e terminal usam o mesmo
`InteractionService`, todos os adapters obedecem ao contrato canônico, a SPA
permite configurar e selecionar providers/modelos, a imagem passa por toda a
CI e o stack implantado permanece saudável.

## Estado de partida

As fases 1 e 2 estão implementadas:

- contratos, registry interno, catálogo híbrido e resolvedor de seleção;
- keyring com sonda, cofre AES-GCM/Scrypt, migração segura e referências de
  credenciais sem plaintext.

O caminho operacional ainda usa `ProviderManager` diretamente. `/api/models`
lista descritores legados, o WebSocket instancia adapters por conta própria e
o Chat permanece na interface React herdada em `/legacy`. O stack não deve
escolher Anthropic ou qualquer outro provider quando não houver credencial ou
Ollama disponível.

## Decisões aprovadas

1. A migração será incremental, com `InteractionService` como fronteira
   canônica e adapters substituídos atrás do novo contrato.
2. Serão suportados OpenAI Responses, Anthropic Messages, Gemini, Ollama,
   OpenAI-compatible, DeepSeek, Groq, endpoints personalizados e OpenRouter.
3. OpenRouter mostrará todos os modelos textuais compatíveis e oferecerá o
   filtro destacado **Somente gratuitos**. Modelos gratuitos não serão
   selecionados automaticamente.
4. A política OpenRouter padrão usará `data_collection: "deny"`,
   `require_parameters: true` e fallbacks apenas entre endpoints do mesmo
   modelo.
5. Sem credencial válida ou Ollama disponível, o Chat fica bloqueado com
   orientação de configuração; não existe fallback silencioso.
6. Trocar provider/modelo afeta o próximo turno da mesma sessão e preserva o
   histórico. A UI também oferece iniciar outra conversa com a seleção.
7. O layout aprovado usa Chat como centro, sessões à esquerda e contexto de
   provider/modelo à direita. Modelos e Provedores permanecem páginas
   completas.

## Arquitetura

```text
SPA Chat / Terminal / futuros canais
                 |
                 v
          InteractionService
          /       |        \
         v        v         v
ModelSelection  Credential  Session/Message/Usage
Resolver        Service     Repositories
         \        |         /
          v       v        v
        ProviderGateway + ModelCatalog
                    |
                    v
          ProviderAdapterRegistry
                    |
 OpenAI | Anthropic | Gemini | Ollama | Compatible | OpenRouter
```

### InteractionService

`InteractionService` recebe um `InteractionEnvelope` independente de FastAPI,
WebSocket e CLI. Para cada turno ele:

1. valida conversa, conteúdo e override opcional;
2. resolve a seleção efetiva uma única vez;
3. cria um snapshot imutável de provider, modelo, parâmetros e credencial;
4. persiste a mensagem do usuário;
5. converte o histórico canônico pelo adapter escolhido;
6. transmite eventos normalizados de texto, reasoning, tools, uso e término;
7. persiste resposta, seleção efetiva, métricas e erro final.

As superfícies Web e terminal apenas traduzem entrada e eventos. Nenhuma delas
instancia adapters, resolve credenciais ou implementa fallback.

### ProviderGateway

`ProviderGateway` combina `ProviderAdapterRegistry`, `ModelCatalog` e
`CredentialService`. Ele cria adapters sem armazenar segredos, descobre
modelos, testa conexões e normaliza falhas. O gateway é o único consumidor
direto das factories do registry.

### Contrato de adapter

Cada adapter oferece:

- descritor do provider e métodos de autenticação;
- descoberta de modelos com capacidades, estabilidade, contexto e preço;
- teste de conexão sem persistir resposta sensível;
- conversão do histórico canônico;
- streaming de texto, reasoning, tool calls, uso e término;
- normalização de erro sem vazar payload, header ou credencial.

## Providers

### OpenAI

Usa Responses API para streaming, tools, itens de mensagem e usage. O adapter
não usa Chat Completions como caminho principal. A descoberta combina Models
API com curadoria para capacidades que a API omitir.

### Anthropic

Usa Messages API e seu protocolo nativo de streaming e tool use. Blocos de
conteúdo e uso são convertidos para eventos canônicos sem descartar
identificadores necessários à continuação de tools.

### Gemini

Usa a API nativa de geração em streaming, aceita API key e ADC quando
disponível, e converte function calls/results para o histórico canônico.

### Ollama

Descobre somente modelos instalados no endpoint configurado. Ausência do
daemon é estado `unavailable`, não erro global. Ollama não exige credencial.

### OpenAI-compatible

Atende DeepSeek, Groq e endpoints personalizados quando o protocolo for
realmente compatível. Configurações específicas de URL, headers permitidos e
catálogo ficam no descritor, sem branches por marca no serviço de interação.

### OpenRouter

OpenRouter é um adapter de primeira classe sobre
`https://openrouter.ai/api/v1`. O endpoint `/models` fornece catálogo dinâmico
de modelos textuais. O mapeamento preserva ID canônico, contexto, modalidades,
parâmetros suportados, expiração e preços.

Um modelo é gratuito quando os custos relevantes de prompt, completion e
request são zero ou quando a variante canônica é explicitamente `:free`.
`openrouter/free` aparece como roteador especial, nunca como default implícito.

Chamadas de Chat usam:

```json
{
  "provider": {
    "data_collection": "deny",
    "require_parameters": true,
    "allow_fallbacks": true
  }
}
```

Os fallbacks pertencem somente aos endpoints do mesmo modelo; o Kairos não
troca o ID selecionado. A interface pode alterar a política em configuração
avançada por perfil ou conversa.

## Catálogo e cache

O catálogo combina fontes na precedência `API > cache > curadoria` por campo,
sem apagar metadados válidos omitidos pela fonte mais recente. Somente modelos
com saída textual e chat aparecem no seletor principal. Tools, visão,
reasoning, contexto, estabilidade, gratuidade e preço são filtros explícitos.

Snapshots dinâmicos ficam fora do histórico e contêm provider, instante de
coleta, validade e payload normalizado. Falha de rede mantém o último snapshot
utilizável e marca a origem como cache. Falha de um provider nunca apaga os
demais.

## Persistência e precedência

`sessions` permanece a fonte canônica da conversa:

- `sessions.model`: modelo persistido da conversa;
- `sessions.model_config`: provider, parâmetros, política de routing e origem
  da seleção;
- `messages.display_metadata`: provider/modelo efetivamente usados pelo turno;
- `session_model_usage`: tokens, custo e provider de cobrança.

A precedência é:

```text
mensagem > conversa > agente/tarefa > perfil > global
```

A resolução ocorre antes do streaming e não muda durante o turno. Uma troca
atualiza a sessão para a próxima mensagem. O histórico registra qual seleção
produziu cada resposta.

## API e eventos

As APIs de Modelos e Provedores passam a consumir o gateway e expõem somente
metadados normalizados. Operações de escrita validam provider, modelo,
capacidade e escopo antes de persistir.

O WebSocket envia eventos versionados:

- `turn_start`: sessão e seleção efetiva;
- `delta`: texto incremental;
- `reasoning_delta`: reasoning permitido para exibição;
- `tool_call` e `tool_result`: ciclo de tools;
- `usage`: tokens e custo conhecido;
- `turn_error`: erro normalizado e possibilidade de retry;
- `turn_end`: motivo de término e estado persistido.

Clientes informam uma versão de protocolo suportada. Eventos desconhecidos
são ignoráveis e novos campos são aditivos.

## SPA principal

O Chat ocupa a área central. A coluna esquerda contém nova conversa e
histórico. A coluna direita apresenta provider, modelo, capacidades, política,
custo e ação de troca.

A página **Modelos** oferece busca e filtros por provider, gratuito, tools,
visão, reasoning, contexto e estabilidade. OpenRouter mostra preço e o filtro
**Somente gratuitos**.

A página **Provedores** mostra conexão, autenticação, origem do catálogo,
atualização, teste de conexão e configurações avançadas. Nenhuma resposta
devolve segredo ou fragmento de segredo.

Sem provider utilizável, o composer fica bloqueado e conduz à configuração.
Ollama disponível pode ser selecionado sem credencial. Trocar modelo oferece
aplicar ao próximo turno ou criar uma conversa.

## Segurança e erros

Somente `CredentialService` lê segredos. Adapters recebem valores em memória e
não os mantêm em descritores, exceções ou representações. Logs, banco, REST e
WebSocket usam referências mascaradas.

Erros canônicos distinguem:

- credencial ausente ou inválida;
- modelo indisponível ou depreciado;
- limite, saldo ou rate limit;
- timeout ou rede;
- parâmetro/tool incompatível;
- falha interna do provider.

A resposta parcial é preservada quando o streaming já começou. Retry
automático só ocorre para falha transitória antes de texto, reasoning ou tool
call. Isso impede repetir efeitos externos.

## Estratégia incremental

1. Introduzir contrato moderno e `ProviderGateway` sem mudar consumidores.
2. Modernizar OpenAI, Anthropic, Gemini e Ollama atrás do contrato.
3. Consolidar OpenAI-compatible, DeepSeek, Groq e endpoints personalizados.
4. Adicionar OpenRouter, catálogo dinâmico e política segura.
5. Introduzir `InteractionService`, persistência e métricas.
6. Migrar API REST/WebSocket e terminal para o serviço compartilhado.
7. Entregar Chat, Modelos e Provedores na SPA principal.
8. Remover o caminho legado somente após testes de paridade.
9. Sincronizar o stack Komodo, redeployar e verificar saúde.

## Testes e aceite

APIs externas são simuladas no CI; nenhum teste consome créditos. Smoke tests
reais só rodam com credencial explicitamente configurada ou Ollama disponível.

A entrega exige:

- testes de contrato, descoberta, streaming, tools, erros e usage para todos
  os adapters;
- testes de OpenRouter para preços, gratuidade, filtros e política de routing;
- testes de precedência e snapshot imutável da seleção;
- testes de persistência por turno e agregação de uso/custo;
- testes REST/WebSocket e CLI sobre o mesmo `InteractionService`;
- testes frontend de estados vazio, configurando, streaming, erro, troca e
  filtros;
- auditoria que garanta ausência de segredo em arquivos legados, banco, logs
  e respostas;
- Python, frontend, lint, tipagem, shellcheck, hadolint, build e integração
  verdes;
- container `healthy` e endpoints de saúde aprovados após redeploy.

## Estado da implementação

As fases 3–6 foram entregues pelos PRs #6–#9. `kairos_providers` contém o
gateway, os adapters nativos e o catálogo dinâmico; `kairos_integration`
centraliza seleção, persistência, retry, ownership e contabilidade no
`InteractionService`. WebSocket e CLI usam o mesmo protocolo de eventos, e a
SPA principal oferece Chat, Modelos e Provedores.

A suíte automatizada cobre contratos, adapters, persistência, transportes,
frontends e imagem. Saúde, credenciais e chamadas reais do ambiente implantado
continuam sendo verificadas como rotina operacional, não como implementação
pendente desta especificação.

### Correções de aceite de 2026-09-12

As correções posteriores tornam explícita a intenção de nova conversa e
preservam a seleção persistida ao reabrir uma sessão. Trabalho assíncrono da
SPA é cercado pelo ciclo de vida da rota para que histórico, sondas e eventos
atrasados não alterem outra conversa ou tela.

Modelos oferece filtros funcionais de raciocínio e contexto mínimo. A
capacidade de raciocínio deriva somente de parâmetros suportados declarados
pelo catálogo; quando não há declaração explícita, a API devolve `null` e o
modelo não passa pelo filtro. Previews são buscados com `include_preview=true`
quando o controle correspondente é ativado.

Provedores mantém duas dimensões independentes: configuração da credencial e
resultado da conexão. Uma credencial configurada permanece **não testada** até
uma descoberta explícita; sucesso vira **Conexão verificada**, falha vira
**Falha na conexão**, e salvar ou substituir a credencial invalida qualquer
resultado anterior. A sonda comprova acesso à descoberta naquele instante,
sem prometer geração, saldo, limite ou disponibilidade futura do modelo.

## Migração e rollback

Sessões e mensagens existentes são preservadas. Campos atuais são preenchidos
de forma compatível; migrações de schema são idempotentes e transacionais. O
frontend legado permanece em `/legacy` durante a transição.

Cada etapa produz software executável e pode ser revertida sem remover o cofre
ou o histórico. O caminho legado só é removido quando testes de paridade de
Chat e terminal passarem na imagem final.

## Fora de escopo

- WhatsApp, Telegram e outros canais externos;
- autenticação multiusuário;
- marketplace e execução isolada de plugins de provider;
- captura de cookies ou reaproveitamento de assinaturas de produtos;
- chamadas reais automáticas que possam gerar custo.

## Referências

- `docs/superpowers/specs/2026-08-25-providers-modelos-chat-design.md`
- OpenRouter Quickstart: `https://openrouter.ai/docs/quickstart`
- OpenRouter Models: `https://openrouter.ai/docs/guides/overview/models`
- OpenRouter Provider Routing:
  `https://openrouter.ai/docs/guides/routing/provider-selection`
