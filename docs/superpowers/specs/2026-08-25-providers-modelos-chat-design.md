# Providers, catálogo de modelos e Chat web

**Data:** 2026-08-25  
**Estado:** aprovado em conversa; aguardando revisão do documento  
**Escopo:** aplicação local, de usuário único

## Objetivo

Transformar o Kairos em uma aplicação web operacional para conversas com LLMs,
com escolha explícita de provider e modelo por contexto. A mesma resolução deve
ser usada pela interface web e pelo terminal, sem acoplar o núcleo a OpenAI,
Anthropic, Gemini ou outro fornecedor.

A entrega atual inclui o Chat na SPA principal, catálogo agentic atualizado,
configuração segura de providers e troca de modelo sem reiniciar o sistema.
WhatsApp, Telegram, desktop completo e providers instaláveis ficam para fases
posteriores, mas recebem fronteiras arquiteturais claras nesta entrega.

## Estado atual

- A SPA principal é `kairos_web/ui`. O React compilado em `web_dist` é legado e
  continua disponível em `/legacy`.
- A SPA principal possui páginas de Modelos, Sessões e Skills, mas não possui
  Chat operacional. O Chat ainda vive no frontend legado.
- `BaseLLMProvider` já normaliza parte de streaming e uso, porém catálogo,
  credenciais, transporte e capacidades permanecem misturados nos adapters.
- O WebSocket e o terminal resolvem e instanciam providers separadamente.
- OpenAI ainda usa Chat Completions e o catálogo estático de `gpt-4o`, `o1` e
  `o3-mini`. Os catálogos de Anthropic, Gemini, DeepSeek e Groq também estão
  defasados.
- `auth.json` recebe escrita atômica e isolamento por perfil, mas armazena os
  segredos em texto puro.
- O schema já oferece `sessions.model`, `sessions.model_config` e tabelas de
  uso por provider/modelo. Essas estruturas serão aproveitadas.

## Decisões de escopo

1. O catálogo principal mostra somente modelos adequados a chat/agentes.
   Modelos exclusivos de imagem, áudio, embedding, moderação e modelos
   depreciados não aparecem no seletor.
2. A primeira versão é local e de usuário único. Os contratos recebem contexto
   de perfil, sem embutir identidade de usuário nos adapters.
3. O Chat operacional será migrado para a SPA principal nesta entrega.
4. A integração usa apenas autenticação oficialmente suportada. Não haverá
   captura de cookie, automação de login de produto nem reaproveitamento de
   assinatura de ChatGPT, Claude ou serviço semelhante como credencial de API.
5. O registry de providers será interno nesta fase. Uma fase posterior o
   transformará em plataforma completa de plugins sem mudar seus consumidores.

## Arquitetura

```text
Web / Terminal / futuros canais
            |
            v
      InteractionService
            |
            +-- ModelSelectionResolver
            |   mensagem -> conversa -> agente/tarefa -> perfil -> global
            |
            +-- CredentialVault
            |   keyring -> cofre criptografado -> segredo externo
            |
            +-- ProviderGateway
                +-- ProviderRegistry
                +-- ModelCatalog
                +-- ProviderAdapter
                    +-- OpenAI Responses
                    +-- Anthropic Messages
                    +-- Gemini
                    +-- OpenAI-compatible
                    +-- Ollama
```

### InteractionService

Será o único ponto de execução usado por Web e terminal. Recebe uma interação
normalizada, resolve provider/modelo, obtém a credencial sem expô-la, inicia o
adapter e devolve eventos padronizados de streaming. Também persiste mensagens,
seleção efetiva, uso e mudanças de modelo.

O serviço não depende de FastAPI, WebSocket nem terminal. Essas superfícies
apenas traduzem entrada e saída.

### InteractionEnvelope

Representa uma nova interação com os campos mínimos:

- origem e identificador da conversa;
- perfil e, futuramente, remetente/canal;
- conteúdo normalizado e anexos;
- agente ou tipo de tarefa;
- override opcional de provider/modelo e seu alcance;
- metadados seguros de rastreamento.

O envelope é a fronteira para futuros adapters de WhatsApp e Telegram. Um
adapter de canal nunca acessará um provider de LLM diretamente.

### ProviderAdapter

O contrato cobre:

- descrição do provider e métodos de autenticação;
- descoberta e descrição de modelos;
- capacidades, contexto, limites e estabilidade;
- conversão do histórico canônico para o protocolo do provider;
- envio, streaming, tools e resultados de tools;
- teste de conexão;
- normalização de erros e métricas.

Os adapters nativos serão OpenAI Responses, Anthropic Messages, Gemini e
Ollama. DeepSeek, Groq, OpenRouter e endpoints personalizados reutilizam um
adapter OpenAI-compatible onde o protocolo realmente for compatível, podendo
especializar catálogo e parâmetros.

### ProviderRegistry

Registra descritores e factories internos. Consumidores dependem apenas do
contrato. A fase posterior de plugins adicionará descoberta externa,
manifestos, isolamento, permissões e compatibilidade versionada ao mesmo
registry.

## Catálogo de modelos

O catálogo será híbrido:

- **dinâmico:** quando autenticado, consulta a API oficial e representa a
  disponibilidade real para aquela credencial;
- **curado:** mantém modelos agentic recomendados e metadados de fallback para
  configuração antes da conexão;
- **cacheado:** guarda origem, instante e validade; uma atualização nunca
  bloqueia o Chat se houver cache utilizável.

Capacidades retornadas pelo provider prevalecem. A curadoria completa somente
campos ausentes. Previews ficam separados e desativados por padrão. Modelos
depreciados ficam ocultos, mas configurações antigas geram aviso de migração.

### Catálogo inicial

| Provider | Modelos ou regra inicial |
|---|---|
| OpenAI | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` |
| Anthropic | `claude-fable-5`, `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5-20251001` |
| Gemini | `gemini-3.7-flash` e demais modelos textuais agentic confirmados pela Models API |
| DeepSeek | `deepseek-v4-pro`, `deepseek-v4-flash` |
| Groq | modelos ativos com saída textual e tool use confirmados |
| OpenRouter | modelos textuais filtrados por suporte a `tools` |
| Ollama | modelos locais instalados, descobertos em runtime |
| Outro | modelos do endpoint configurado ou ID manual validado |

O Antigravity atual é tratado como `kind=remote_agent`, pois a documentação do
Gemini o publica na Interactions API como agente preview, não como modelo LLM
comum. Ele aparece somente quando a credencial confirmar acesso e sempre com o
rótulo Preview.

## Autenticação e segredos

### Métodos suportados

| Provider | Métodos aceitos pelo Kairos nesta fase |
|---|---|
| OpenAI | API key |
| Anthropic | API key; federação de workload fica documentada, não implementada no modo local |
| Gemini | API key e OAuth oficial |
| OpenRouter | API key e OAuth PKCE oficial, que emite chave controlada pelo usuário |
| DeepSeek | API key |
| Groq | API key |
| Ollama | sem credencial por padrão; configuração explícita para endpoints protegidos |
| Outro | bearer/API key opcional, conforme configuração explícita |

OAuth de CLI ou login de produto não será apropriado por terceiros. A presença
de uma assinatura de produto não implica acesso programático.

### CredentialVault

A ordem de armazenamento é:

1. keyring seguro do sistema, quando disponível;
2. cofre criptografado, desbloqueado por senha-mestra a cada reinício;
3. segredo externo fornecido por ambiente ou arquivo administrado pelo usuário.

A chave de criptografia nunca será gravada ao lado do cofre. APIs retornam
somente estado, método, origem e identificador mascarado. Logs, exceções e
eventos passam por redação.

O `auth.json` antigo terá migração assistida: detectar, importar para o cofre,
validar a gravação e só então oferecer remoção do segredo legado. A migração
não apaga automaticamente dados recuperáveis sem confirmação.

Estados explícitos: `locked`, `unlocked`, `keyring`, `external` e
`not_configured`.

## Seleção de provider e modelo

Uma seleção é sempre um par tipado:

```text
ProviderModelRef(provider="openai", model="gpt-5.6-terra", kind="model")
```

Precedência, da maior para a menor:

1. override da mensagem atual;
2. escolha persistida da conversa;
3. regra do agente ou tipo de tarefa;
4. preferência do perfil;
5. padrão global.

O resolvedor devolve a seleção e a razão, como `message_override` ou
`conversation_override`. Essa razão aparece na interface e nos registros.

A troca vale na interação seguinte e não reinicia servidor nem conversa. O
histórico permanece canônico e cada adapter o converte. A mudança é registrada
como evento; caches incompatíveis entre providers são descartados. Se o novo
contexto for menor que o histórico ativo, o usuário recebe aviso e opções.

Não existe fallback silencioso para outro provider pago. Indisponibilidade
interrompe a interação e oferece atualizar catálogo, escolher substituto ou
configurar uma regra explícita de fallback.

## Persistência

- `config.yaml`: padrão global, preferências de perfil, regras por agente/tarefa
  e opções não secretas;
- `state.db`: seleção da conversa, eventos e uso efetivo;
- keyring/cofre: credenciais e refresh tokens;
- cache de catálogo: metadados sem segredos.

`sessions.model` permanece por compatibilidade. `sessions.model_config` recebe
JSON versionado com provider, modelo, tipo, origem da escolha e parâmetros
específicos. Sessões antigas continuam legíveis.

## Interface web

### Provedores de IA

A página Modelos evolui para uma área com:

- resumo do padrão e estado do cofre;
- cartão de cada provider com conexão, método e atualização do catálogo;
- configuração de API key ou conexão oficial, quando suportada;
- testar, substituir e remover credencial;
- modelos recomendados, catálogo disponível e filtros de preview;
- capacidades, estabilidade, contexto e custo conhecido;
- escolha de padrão.

### Chat

A SPA principal recebe:

- criação e continuação de conversas;
- transcript e streaming;
- seleção em duas etapas, provider e modelo;
- alcance da escolha: global herdado, conversa ou mensagem;
- indicação da regra aplicada e do modelo efetivamente usado;
- troca em runtime;
- erros acionáveis e estado de conexão.

### Regras por atividade

A configuração permite mapear agente ou tipo de tarefa para provider/modelo.
Não será criado ainda um construtor visual completo de agentes.

## Compatibilidade com terminal

O terminal deixa de instanciar providers diretamente e usa o mesmo
`InteractionService`. Flags de linha de comando continuam tendo maior
precedência dentro do escopo da interação do terminal. A saída mostra provider,
modelo e origem da seleção quando solicitado em modo diagnóstico.

## Erros e observabilidade

Erros normalizados distinguem autenticação, autorização, indisponibilidade de
modelo, limite/cota, rate limit, contexto, payload incompatível, rede e erro do
provider. Dumps crus são redigidos antes de logs ou eventos.

Cada interação registra provider, modelo, protocolo, razão da seleção, duração,
uso e status. Conteúdo de mensagens e segredos não entram em métricas por
padrão.

## Testes

- TDD para resolvedor, contratos, cofre, catálogo e adapters;
- HTTP simulado para adapters, sem consumo de APIs reais no CI;
- testes REST/WebSocket de seleção, persistência e streaming;
- testes do terminal sobre o mesmo serviço;
- testes frontend de configuração, filtros, precedência e Chat;
- teste de migração que garante ausência de segredo no arquivo legado após uma
  migração confirmada;
- testes de contrato do registry para preparar plugins;
- suíte completa, lint, tipagem e checks de segurança antes da integração.

## Fases de implementação

1. Contratos, registry, catálogo e resolvedor.
2. Keyring, cofre e migração segura de credenciais.
3. Adapters e modelos atuais dos providers.
4. Persistência de seleção e `InteractionService` compartilhado.
5. Página Provedores de IA e Chat operacional.
6. Migração do terminal, robustez, documentação e suíte completa.

## Fora do escopo desta entrega

- adapters de WhatsApp e Telegram;
- migração das demais telas do frontend legado;
- aplicação desktop completa;
- marketplace, instalação e execução isolada de plugins de provider;
- autenticação multiusuário e administração remota.

## Evolução obrigatória posterior

Após esta entrega estar integrada e estável, o ProviderRegistry será amadurecido
para uma plataforma completa de plugins. Essa fase incluirá manifesto
versionado, descoberta/instalação, permissões, isolamento, migrações,
compatibilidade de contrato e UI orientada por schema. Os contratos e testes
desta entrega são a base, não uma implementação descartável.

## Referências oficiais consultadas

- OpenAI Models: https://developers.openai.com/api/docs/models
- OpenAI API authentication: https://platform.openai.com/docs/api-reference/authentication
- Claude models: https://platform.claude.com/docs/en/about-claude/models/overview
- Claude authentication: https://platform.claude.com/docs/en/manage-claude/authentication
- Gemini 3.7 Flash: https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash
- Gemini OAuth: https://ai.google.dev/gemini-api/docs/oauth
- Gemini Interactions API: https://ai.google.dev/gemini-api/docs/interactions-overview
- DeepSeek models: https://api-docs.deepseek.com/quick_start/pricing
- Groq tool use: https://console.groq.com/docs/tool-use/overview
- OpenRouter model filters: https://openrouter.ai/docs/guides/overview/models
- OpenRouter OAuth PKCE: https://openrouter.ai/docs/guides/overview/auth/oauth
