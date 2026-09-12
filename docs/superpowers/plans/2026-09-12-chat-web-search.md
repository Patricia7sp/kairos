# Chat com busca web e privacidade preservada

## Requisitos e decisões

A preferência vigente da usuária é manter `data_collection: deny` como padrão.
Explicar incompatibilidade de endpoints sem atribuir indevidamente o problema à
conta, sem relaxar política ou trocar modelo automaticamente.

Busca web opcional, desligada por padrão. Campo canônico `web_search: bool = False`
no envelope, separado dos parâmetros enviados ao provedor. WebSocket aceita o
mesmo campo; a interface lembra a escolha por conversa neste navegador, sem alegar
persistência de configuração no servidor. CLI aceita `--web-search`.

Somente a ferramenta `web_search` é anunciada/executada pelo Chat. Arquivos e shell
permanecem no Agent Runtime isolado. Executor assíncrono valida JSON e schema,
limita consulta, resultados, tamanho da saída e tempo, propaga cancelamento e
converte falhas em resultados seguros. Conteúdo externo é dado não confiável.

O serviço mantém seleção, credencial e política congeladas durante todo o turno.
Persiste cada resposta do modelo e cada resultado antes da chamada seguinte.
Contabiliza cada rodada uma única vez; eventos de consumo representam o total do
turno. Máximo de quatro rodadas com execução e oito chamadas de ferramenta por
turno; uma rodada final sem ferramentas pode concluir a resposta. Pedidos além do
limite recebem resultados de erro persistidos e o turno termina explicitamente.
Nenhuma ferramenta é executada quando o recurso está desligado.

## Implementação e verificação

1. Executor independente em `kairos_integration/chat_tools.py`, com função
   `async execute_web_search(call: CanonicalToolCall) -> InteractionToolResult` e
   constante `WEB_SEARCH_TOOLS` (tupla de schemas OpenAI). Testes de execução real
   via HTTP controlado, argumentos inválidos, ferramenta desconhecida, timeout,
   limites e cancelamento. Não usa dispatch síncrono nem ferramentas de host.
2. Envelope, transporte e laço no serviço com testes de duas rodadas, reabertura,
   política/modelo, consumo, erros e limites. Nenhuma mudança nas garantias de
   exclusão/leases/cleanup existentes.
3. Interface/cliente/CLI: escolha explícita, aviso de envio da consulta ao buscador,
   resultados escapados em execução e histórico. Erro OpenRouter explica a política
   da requisição/conta. Testes de protocolo e apresentação.
4. Revisão independente, CI completa com versão Codex fixada, aceite no navegador
   com upstream controlado e registro dos limites reais no progresso funcional.

## Progresso

- Baseline: 37 testes de serviço/transporte/OpenRouter passaram.
- Decisão: não repetir teste externo que omita `data_collection: deny`.
- Executor, laço, transporte, CLI e Web implementados; aceite inicial passou.
- Decisão: IDs sintetizados repetidos entre rodadas são aceitos; duplicidade dentro
  da mesma rodada é inválida. Limites contam chamadas, não IDs únicos.
- Cancelamento durante provider e gravação persiste contabilidade/estado interrompido.
- Gemini preserva thoughtSignature opaca e nome da chamada por rodada; testes HTTP/reabertura passam.
- Revisão final e CI passaram: 1.939 Python/5.575 subtestes, 145 Web, 17 TUI, 18 desktop.
- Docker: 22 testes/23 subtestes. Chromium final e busca externa real passaram.
- Integração/publicação pendentes do checkpoint de entrega; escopo integral aberto.
