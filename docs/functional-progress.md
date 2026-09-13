# Progresso funcional do Kairos

Atualizado em 13/09/2026. Código publicado na `main`: PR #31, `a896c6f`.
Este documento inclui o checkpoint de continuidade após a implantação. A conclusão integral não está declarada.

## Mandato e critérios

Continuar autonomamente, com prioridade para Chat, Sessões, Modelos e Provedores.
Validar implementação, integração e comportamento externo separadamente. Preservar
credenciais, conversas, escolhas de modelo e identidade própria do Kairos. Ollama
operacional continua adiado conforme decisões anteriores.

## Implementações deste ciclo

- [x] OpenRouter: teste de conexão autentica em `/api/v1/key` antes do catálogo público.
- [x] Chat: parâmetros canônicos imutáveis são convertidos em JSON no limite HTTP de
  seis adaptadores; objetos aninhados deixaram de derrubar a transmissão.
- [x] OpenAI Responses: `max_tokens` canônico é traduzido para `max_output_tokens`.
- [x] Falha de privacidade OpenRouter: erro `policy` com mensagem fixa em português;
  leitura limitada a 16 KiB, sem repassar conteúdo ou segredos do upstream.
- [x] Credenciais: remoção de chave principal no cofre e metadados, preservando as
  demais entradas; credenciais externas são somente leitura. Remoção e gravação
  compartilham lock e rollback. Metadados inválidos são rejeitados antes da troca.
- [x] Sessões: respostas atrasadas não substituem a seleção atual; listeners são
  descartados; transcrições completas são escapadas e exportáveis. Histórico
  preserva nomes de ferramentas e mensagens compactadas. Tags nulas não causam
  aplicação parcial de outras alterações. Sessões de runtime abrem o runtime.
- [x] Ferramentas: tela consulta inventário real, com busca, filtros, requisitos e schemas.
- [x] Busca web: resultados têm título, URL e resumo; conteúdo inesperado, desafio de
  acesso e respostas excessivas são falhas explícitas, não sucesso fictício.
- [x] Ajustes: temperatura e limite de tokens persistidos atomicamente, com remoção
  explícita de preferência, validação e uso nos turnos seguintes.
- [x] Status, capacidades, perfis de roteamento e uso vêm de configuração, catálogo,
  runtime observado e contabilidade persistida. Não se inventam modelo, capacidades,
  gateway ativo, totais de custo desconhecidos nem série diária inexistente.
- [x] CLI `model`: show/list/refresh/set/test usam o gateway e a configuração canônica;
  configuração preservada em seleção inválida; atualização servida do cache sinaliza
  falha, teste de conexão não promete geração.

## Validação e evidências

- CI local anterior à adição da CLI model: **1.842 testes Python + 5.571 subtestes**,
  **134 Web**, **17 TUI**, **18 desktop**; typescript, Ruff, shellcheck e lock passaram.
  Codex **0.153.4** necessário nos testes de compatibilidade. O 0.154.0 instalado no
  PATH do usuário causa duas reprovações esperadas; não alteramos essa instalação.
- Navegador Chromium em 390/900/1400 px, aplicação FastAPI real e upstream controlado:
  ajustes chegam ao payload HTTP; Chat, custo/histórico após reload, exportação de
  transcrição longa, arquivar/desarquivar, tags e navegação para Chat funcionam.
  Ferramentas e todas as rotas principais carregam; sem erro JS ou overflow.
- Revisão independente: duas falhas corrigidas (troca parcial da chave e limite de
  tokens OpenAI). Segunda revisão sem bloqueadores; 37 testes direcionados passaram.
- Primeira imagem Docker construída. Revalidação final/entrega registradas abaixo.
- Relatórios locais: `/tmp/kairos-functional-{ci,browser,build}.log`,
  `/tmp/kairos-functional-browser.json`. São artefatos temporários, não a única fonte
  de continuidade; este arquivo registra os resultados relevantes.

## Privacidade OpenRouter: diagnóstico corrigido e decisão vigente

O Kairos envia `data_collection: "deny"` por padrão. A usuária confirmou que quer
manter essa proteção contra provedores que possam usar conversas para treinamento.
Autenticação e catálogo do modelo `nvidia/nemotron-3-ultra-550b-a55b:free` funcionam,
mas o endpoint testado retorna 404 quando essa restrição está presente.

A comparação direta registrada abaixo confirmou geração HTTP 200 ao omitir somente
a restrição; portanto, atribuir o problema genericamente à conta estava incorreto.
O teste não autoriza relaxar a proteção. O fluxo protegido desse modelo continua
sem geração bem-sucedida validada. Não alterar conta, padrão ou modelo silenciosamente.
Essa incompatibilidade não bloqueia o trabalho nas funcionalidades independentes.

## Inventário restante, sem herdar conclusões do README antigo

- Chat: busca web opcional e ciclo real de ferramentas implementados no novo lote,
  validado conforme aceite abaixo. Arquivos/terminal continuam no Agent Runtime isolado. Outras
  ferramentas não estão habilitadas pelo Chat.
- Cron operacional foi implementado no lote seguinte, descrito abaixo. Monitores,
  notepad, blueprints e entrega externa seguem pendentes. Limites finitos recorrentes
  estão publicados via PR #28.
- `/api/logs` real e aposentadoria explícita de `/api/env` implementados no lote
  de registros abaixo, já publicado via PR #27. `/api/cron/jobs` usa armazenamento real.
- A CLI declara **50 comandos**. Após implementar `model`, `logs`, `insights` e `debug`, **25** ainda não têm handler:
  acp, backup, claw, console, dump, gui, hooks, import-agent, import,
  login, logout, memory, monitoring, pairing, pause, peer, prompt-size, setup,
  skin, slack, uninstall, update, verify, webhook, whatsapp. Há também subcomandos
  pendentes dentro dos grupos com handler. Esses comandos retornam 69.
- Registry canônico possui oito provedores: anthropic, custom, deepseek, gemini, groq,
  ollama, openai e openrouter. Não confundir isso com os 36 previstos originalmente.
- Gateway tem protocolo de adaptadores/entrega, mas não integra as 23 plataformas
  previstas originalmente. Credenciais externas e testes reais serão necessários.
- TUI/desktop, MCP, plugins, perfis isolados e gerenciamento de skills precisam de
  aceite operacional específico; suas suítes unitárias não certificam uso completo.

## Próxima sequência

1. Retomar um recorte explícito das pendências de CLI ou cron descritas no checkpoint.
2. Implementar e validar o fluxo real, incluindo persistência e recuperação de falha.
3. Registrar entrega e aceites separados dos bloqueios externos, mantendo a proteção
   de privacidade e a preservação de dados.

## Verificação final do lote — PR #24

- Commit funcional: `76bb36b`. CI local final: **1.846 testes Python e 5.573
  subtestes**, **134 Web**, **17 TUI**, **18 desktop**; todos os checks de
  `scripts/ci.sh --fast` passaram. Imagem Docker: **22 testes de integração
  passaram**, incluindo o ciclo de vida s6 com imagem auxiliar, sem skips.
- Aceite externo com aplicação candidata, HTTP e WebSocket reais em armazenamento
  descartável: autenticação e catálogo passaram; a recusa de privacidade chegou
  como `turn_error`/`policy`, sem queda de socket. API de histórico retornou 200;
  seleção preservada e mensagens persistidas. Isso valida o caminho de erro,
  **não** geração bem-sucedida.
- A primeira CI remota aprovou oito jobs e reprovou o teste de prazo de cancelamento:
  o limite de 280 ms incluía persistência SQLite posterior ao prazo de RPC de 200 ms.
  Reproduzido introduzindo 120 ms de latência na gravação. O teste agora observa os
  orçamentos reais das duas esperas, mantém persistência real lenta e usa somente
  um limite de proteção contra travamento. Mutação reiniciando o prazo foi detectada;
  os 29 testes do serviço de runtime passaram. Nenhuma mudança no código de runtime.
- PR: https://github.com/Patricia7sp/kairos/pull/24. Não registrar merge ou deploy
  como concluídos até conferir os respectivos resultados.

## Entrega confirmada — 2026-09-12

- PR #24 integrada em `74c3f31`, após os **nove jobs da CI remota passarem**.
- Implantada a imagem `kairos:functional-validation` da revisão `dc4554c`;
  comparação dos arquivos de aplicação alterados conferiu o conteúdo publicado.
- Backup privado `20260912T143602Z` restaurado em volume descartável: **4.258
  arquivos comparados**, SQLite íntegro, **18 sessões, 56 mensagens, 16 turnos
  runtime, 10 checkpoints e 2 baselines**. Digests e marcadores restaurados.
- Aplicação e broker saudáveis após manutenção; imagens do broker/worker preservadas.
  Reinicialização para backup precedeu a implantação exclusiva da aplicação.
- Aceite publicado em Chromium, 390/900/1400 px: ajustes, ferramentas, sessões,
  modelos, provedores, visão geral e compositor carregam; filtro de ferramentas
  funciona; sem erro JS ou overflow. Seis APIs autenticadas retornaram 200.
- Comparação pós-implantação: configuração, metadados de autenticação, token Web e
  login dedicado do runtime inalterados; contagens de sessões/mensagens/turnos iguais
  ao backup. Aceite no navegador foi somente leitura, sem alterar preferências.
- Evidências: `/tmp/kairos-functional-{deployment,production-browser,preservation}.json`;
  registro de implantação também no diretório privado do backup.
- O bloqueio OpenRouter descrito acima permanece; não houve nova geração bem-sucedida.

## Continuidade — cron operacional

Iniciado o próximo lote em `feat/operational-cron`, a partir da main integrada.
Objetivo: substituir listagem e tick fixos por armazenamento durável, execução real,
administração e histórico. Sem criar jobs na instalação da usuária durante testes.


## Cron operacional — implementação e validação local

- Jobs JSON validados antes de leitura/escrita, criação once/interval/cron,
  pausa/retomada/exclusão e histórico persistido. Migração v3 adiciona `executions`
  ao banco canônico, com restrição de estados e trigger de imutabilidade terminal.
- Claim com ocorrência única no ledger antes de avançar JSON e executar o turno.
  Lock por volume mantido durante todo o tick; backlog colapsado e `once` esgotado
  não repetido. Interrupção sem conclusão observada é `unknown`, nunca sucesso.
- Integração com serviço canônico de interação, ticker Web de 60 segundos, limite
  de 300 segundos por turno; desligamento fecha stream e serviço mesmo se o journal
  falhar. CLI e API autenticada usam o mesmo armazenamento. Nova tela nativa.
- Revisão independente encontrou e permitiu corrigir cancelamento engolido,
  agenda inválida aceita, orçamento esgotado ignorado e campo repeat incompleto.
  Regressões incluem kill de processo real, concorrência, crash entre JSON/SQLite,
  timeout, cleanup com erro de persistência e histórico terminal imutável.
- Primeiro aceite em Chromium: ticker real disparou um turno com upstream controlado,
  resposta salva e recuperada após reload, exclusão preservou histórico. Inspeção
  visual encontrou controles ocultos exibidos por CSS; corrigidos para o aceite final.
- A primeira CI local apontou apenas duas expectativas desatualizadas da árvore CLI,
  ajustadas para os três novos subcomandos. As 57 regressões e 165 subtestes passaram.
  Verificação completa final e entrega serão registradas após confirmação.
- Manual e limites: [Agendamentos](agendamentos.md). Nenhum job foi criado na
  instalação de produção da usuária durante os testes.

## Verificação final do lote de cron

- CI local completa: **1.876 testes Python + 5.575 subtestes**, **136 Web**,
  **17 TUI**, **18 desktop**, tipos, Ruff, shellcheck e lock passaram. Codex 0.153.4.
- **22 testes de integração da imagem passaram**, sem skips.
- Aceite final em Chromium: ticker real de um minuto executou exatamente um request
  ao upstream controlado, com modelo correto e transcript persistido após reload;
  exclusão preservou o histórico. 390/900/1400 px, campos inativos ocultos, sem erros
  JS e sem overflow; capturas inspecionadas após terminar a transição de layout.
- Revisão independente final: sem bloqueador no escopo documentado.
- Evidências locais: `/tmp/kairos-cron-{final-ci,image-tests,browser-final}.log` e
  `/tmp/kairos-cron-browser.json`. Entrega remota ainda depende de seus checks.

## Entrega confirmada do cron — 2026-09-12, 15:03 UTC

- PR #25 integrada em `1b5e317`, após **nove checks remotos aprovados**:
  https://github.com/Patricia7sp/kairos/pull/25. Revisão de implementação `d5bf5c2`.
- Imagem publicada `sha256:2a06c5e4ec3e9b21365ab7d39f6ebffa2f512ce4399fc0d7419336c1df49e0e3`.
  Conteúdo dos arquivos alterados conferido contra o código integrado.
- Migração v2→v3 ensaiada sobre cópia do backup real: nove tabelas canônicas com
  conteúdo idêntico, SQLite íntegro, migração idempotente e zero execuções criadas.
- Backup final `20260912T150222Z` restaurado em volume descartável: **4.260 arquivos**
  comparados, 18 sessões, 56 mensagens, 16 turnos runtime, 10 checkpoints e 2 baselines.
  Todos os digests e marcadores de continuidade conferidos.
- Aplicação e broker saudáveis; imagem e processo do worker preservados. O verificador
  de implantação falhou ao comparar a lista de mounts sem normalizar a ordem.
  Destinos, fontes, permissões e porta foram conferidos independentemente contra a
  configuração inalterada; verificador corrigido para comparar por destino.
- Aceite publicado em Chromium, 390/900/1400 px: agendamentos, ajustes, ferramentas,
  sessões, modelos, provedores, visão geral e compositor sem erro JS ou overflow.
  Nove APIs retornaram 200, incluindo runtime `ready`, scheduler ativo sem erro e
  lista de jobs vazia. Nenhuma criação/alteração de job em produção durante o aceite.
- Comparação após implantação: cinco arquivos protegidos, incluindo cofre criptografado,
  configuração, metadados de auth, token Web e login do runtime, permaneceram idênticos;
  18 sessões, 56 mensagens e 16 turnos preservados. SQLite íntegro.
- Auditoria local: `/tmp/kairos-cron-{deployment,production-browser,preservation}.json`;
  registro de implantação também salvo junto do backup privado.

## Ponto de retomada

Código integrado e publicado até `1b5e317`; PRs #24 e #25 entregues. Este registro
final foi acrescentado no arquivo local de progresso após o aceite publicado.

O escopo funcional integral permanece aberto. Próximos trabalhos independentes:
laço de ferramentas no Chat com as permissões adequadas, monitores/notepad/blueprints
do cron, comandos CLI ainda ausentes, APIs de ambiente/logs, aceite operacional de
MCP/plugins/skills/TUI/desktop e conectores externos. Não reutilizar os checkmarks
históricos do README como prova de funcionamento completo.

O diagnóstico de privacidade desse aceite foi corrigido pela comparação abaixo.
A decisão vigente da usuária já foi recebida: preservar `data_collection: deny`.
Não há confirmação pendente para continuar as tarefas independentes.

## Correção do diagnóstico OpenRouter — comparação direta em 2026-09-12

A atribuição anterior do bloqueio à política da conta estava incorreta/incompleta.
Uma comparação direta, com a mesma chave e `nvidia/nemotron-3-ultra-550b-a55b:free`,
confirmou:

- Com `provider.data_collection="deny"` (padrão injetado pelo Kairos): HTTP 404,
  mensagem `No endpoints found matching your data policy (Free model training)`.
- Omitindo somente `data_collection`, preservando `require_parameters=true` e
  `allow_fallbacks=true`, e respeitando os padrões da conta: **HTTP 200, com resposta**.

Portanto, a conta aceita esse modelo; a restrição adicional da requisição do Kairos
é a causa reproduzida. Não recomendar mudança na privacidade da conta nem tratar
uma resposta da usuária sobre essa mudança como pré-requisito para corrigir a
integração. O código atual impõe `deny` em `OpenRouterRoutingPolicy` quando a opção
não foi definida. A proposta inicial de omitir a preferência por padrão foi substituída pela
instrução posterior da usuária: manter `deny`.

Teste com prompt sintético mínimo, volume original e passphrase montados somente
para leitura, cofre copiado para diretório temporário privado. Nenhuma alteração de
conta, configuração persistida ou histórico da usuária. Evidência:
`/tmp/kairos-policy-comparison.json`. A geração direta foi bem-sucedida; o fluxo
padrão do Kairos continua aplicando a restrição por decisão explícita da usuária.


## Decisão vigente da usuária — proteção de privacidade preservada

A usuária confirmou que considera importante a restrição contra uso das conversas
para treinamento. **Manter `data_collection: "deny"` como padrão do Kairos.**
Essa decisão substitui a proposta anterior de omitir a preferência por padrão.
Recusas explícitas também permanecem obrigatórias. Não relaxar a política nem
fazer fallback para outro modelo silenciosamente para obter uma resposta.

O diagnóstico fica separado da decisão: a conta aceita a geração sem essa
restrição, mas esse teste não autoriza removê-la do Kairos. A incompatibilidade do
endpoint do modelo gratuito com a preferência protegida deve ser explicada na UI.
A continuação segue nas tarefas independentes, com testes externos limitados a
requisições que respeitem a política vigente.

Novo lote: busca web opcional no Chat, ciclo de ferramenta real com histórico e
contabilidade de todas as chamadas, e indicação clara da política OpenRouter.


## Novo lote — Chat com busca web e proteção preservada

Implementação em `feat/chat-search-privacy`, validada localmente. Integração e
publicação registradas no próximo checkpoint. Plano: `2026-09-12-chat-web-search.md`.

- Busca desligada por padrão; Web lembra a escolha por conversa nesta aba/navegador.
  CLI `chat`/`run` aceita `--web-search`. Campo separado de parâmetros do provedor.
- Somente `web_search` é anunciada/executada, com schema estrito, consulta até 2.000
  caracteres, 1–5 fontes, argumentos até 16 KiB, saída JSON até 32 KiB e timeout 15 s.
  Consulta vai ao DuckDuckGo; resultados externos são tratados como não confiáveis.
- Ciclo persiste respostas do modelo/resultados e reutiliza modelo, credencial e
  parâmetros congelados. Até quatro rodadas de execução/oito chamadas, depois pode
  haver resposta final sem ferramentas. Erro explícito para recurso desligado/limite.
- Consumo por rodada registrado sem duplicação, incluindo chamada interrompida;
  total acumulado separado do custo de cada resposta. Cancelamento durante gravação
  conclui transcript+contabilidade. Interrupção da busca/crash deixa resultado seguro
  no histórico e não repete o efeito ao reabrir.
- Identificadores locais repetidos em novas rodadas são legítimos em Gemini/Ollama;
  não são confundidos com evento duplicado dentro da mesma resposta do provedor.
- Web mostra fontes, resultados e erros escapados; contexto conserva custo acumulado
  após reload e sinaliza resposta interrompida. Privacidade explica treinamento e
  indisponibilidade de endpoints sem atribuir indevidamente a falha à conta.
- Aceite Chromium com app/WS/SQLite/adaptador reais e HTTP externo controlado passou:
  busca ligada/desligada/erro/política, seis chamadas preservando modelo e `deny`,
  histórico, custo acumulado, preferências, 390/900/1400 px, sem erro JS ou overflow.
  Relatório `/tmp/kairos-chat-search-browser.json`; repetido com código congelado.
  Uma conversa produziu 42 tokens/duas chamadas e custo acumulado US$ 0,000108,
  preservado após reload. Nenhuma chamada ao modelo real nesse aceite controlado.
- Busca externa real, isolada do histórico da usuária: consulta sintética retornou
  três fontes, incluindo docs.python.org. `/tmp/kairos-chat-search-live-search.json`.
- CI intermediária: 1.929 Python + 5.575 subtestes, 142 Web, 17 TUI, 18 desktop,
  lint/typecheck/shell/lock passaram. Depois foram acrescentadas correções e testes
  de cancelamento/persistência; nova verificação integral pendente de código final.
- Revisão independente encontrou e guiou correções de contabilidade interrompida,
  janela de cancelamento durante gravação e IDs locais reutilizados. Preservação de
  `thoughtSignature` do Gemini em chamadas de função foi implementada e validada.

Próximo lote identificado: diário operacional real e limitado para serviços, CLI
`logs`, API e UI nativa. `/api/env` é rota legada sem consumidor atual; não recriar
editor de segredos. Auditoria local: `/tmp/kairos-next-env-logs-audit.md`.


### Aceite final do lote de busca — 2026-09-12

- Código congelado: **1.939 testes Python + 5.575 subtestes**, **145 Web**, **17 TUI**,
  **18 desktop**. Ruff, formatação, typescript, shellcheck, recall e lock passaram.
  Log `/tmp/kairos-chat-search-ci-frozen.log`, Codex de teste fixado em 0.153.4.
- Imagem construída e testes Docker completos: **22 testes + 23 subtestes**,
  incluindo stub s6 derivado da imagem atual. Não houve skips nessa execução Docker.
- Revisão final independente sem bloqueadores após corrigir também perda de lease:
  executor que perdeu ownership não grava marcador nem repara histórico; o sucessor
  recupera chamadas pendentes ao assumir. Contabilidade da chamada anterior permanece.
- Gemini mantém assinatura opaca por functionCall e nomes por rodada com IDs locais
  repetidos. HTTP controlado cobre rodadas paralelas/sequenciais e SQLite reaberto;
  serializer público e repr não expõem a assinatura. Ollama foi validado no limite
  HTTP controlado; aceite operacional de um servidor Ollama continua adiado.
- Assinaturas Gemini de partes de texto/imagem estão fora desse contrato. Chamadas
  pagas/live Gemini não foram realizadas; não confundir teste de wire com aceite externo.
- Navegador final passou com o código congelado; fixture encerrada e porta 9137 livre.
- Artefatos: `/tmp/kairos-chat-search-{ci-frozen,build-frozen,container-final,browser-final}.log`,
  `/tmp/kairos-chat-search-final-review.md` e JSON do navegador/busca externa.


### Publicação do lote de busca — PR #26

- PR #26 integrado: `10d248294a3cd1c778c21efcb04ce80f6a79e008`; código revisado
  `66e6789c8a790f09d510711319a70237533d1679`. Nove checks remotos passaram.
- Imagem publicada: `sha256:4e664bf5963a04c4e220fbd62f07a33d99b87d6a8327187913085f96818e617b`.
  Aplicação e broker saudáveis; imagem/contêiner do keeper preservados.
- Backup privado restaurado/conferido antes do deploy: `20260912T155959Z`, 4.261
  arquivos, integridade SQLite e digests de checkpoints/baselines validados.
- Aceite publicado em Chromium: nove APIs 200, runtime ready, scheduler ativo,
  compositor pronto, busca desligada por padrão e explicação de treinamento visível;
  390/900/1400 px sem erro JS ou overflow. Nenhuma geração nem mutação de dados nesse
  aceite de produção.
- Cinco arquivos protegidos permaneceram idênticos; 18 sessões, 56 mensagens,
  16 turnos de runtime, 10 checkpoints e duas baselines preservados.
- Relatórios `/tmp/kairos-chat-search-{deployment,preservation,production-browser}.json`;
  evidência da implantação também salva no diretório privado do backup.
- Notas locais anteriores, já incorporadas ao PR, foram preservadas adicionalmente
  no stash `Kairos prior delivery notes preserved before PR26; incorporated in 66e6789`.

## Continuação em curso — eventos operacionais

Worktree `.worktrees/service-events`, branch `feat/service-events`, base `10d2482`.
Plano `docs/superpowers/plans/2026-09-12-service-events.md`. Módulo de diário passou
65 testes e revisão independente; Web/API/CLI e produtores de Chat/busca/cron
estão implementados. Aceite inicial em Chromium passou, incluindo ticker cron real,
igualdade API/CLI, filtros, refresh/reload e três larguras sem erros JS/overflow.
Revisão de integração exigiu distinguir chamada de turno, falha de cancelamento e
retirar escrita síncrona do event loop. Correções e validação final em curso;
publicação deste lote ainda pendente. Manual: [Registros](registros.md).
O escopo funcional integral permanece aberto; a proteção `data_collection: deny`
permanece vigente.


### Verificação do lote de registros operacionais

- Diário durável e limitado, catálogo fixo sem conteúdo sensível, rotação e leitura
  segura compartilhada por API autenticada, CLI `logs` e tela Registros. `/api/env`
  foi aposentado com HTTP 410 e indicação do fluxo existente de provedores.
- Eventos reais de lifecycle Web, chamadas ao modelo, buscas e término de cron.
  Chat registra cada chamada, não sucesso do turno completo. Falha interna, erro
  de provedor e cancelamento têm classificação distinta.
- Escrita em thread não bloqueia o event loop; cancelamento aguarda o fim da
  escrita reservada. Cancelar após estado terminal do cron não gera unknown falso.
  Revisão final independente sem bloqueadores.
- CI local final: **2.027 testes Python + 5.579 subtestes**, **156 Web**, **17 TUI**,
  **18 desktop**; lint, formatação, tipos, shellcheck, recall e lock passaram.
  Codex dos testes permanece fixado em 0.153.4.
- Artefatos: `/tmp/kairos-service-events-ci.log`,
  `/tmp/kairos-service-events-integration-review.md`. Imagem construída; aceite
  final de navegador/Docker e publicação serão registrados após confirmação.
- Diário best effort, duas partes de 512 KiB, sem recuperação automática de
  corrupção nem garantia de auditoria completa. Falha de I/O pode perder eventos;
  o journal não substitui os ledgers de conversas/uso/execuções.

Próxima pendência iniciada em worktree isolado: limites finitos de ocorrências
recorrentes, plano `docs/superpowers/plans/2026-09-12-cron-limits.md` na branch
`feat/cron-limits`. Implementação ainda em curso, não incluída neste lote.


Correção de empacotamento encontrada pelo teste Docker: o novo pacote era
descoberto pelo setuptools local, mas faltava na lista COPY do Dockerfile.
COPY acrescentado e regressão de importação da Web + escrita/leitura do diário
na imagem real adicionada; RED confirmou ModuleNotFoundError na imagem anterior.
Aceite final do navegador já passou com ticker real de 57,89 s, filtros/reload,
API/CLI iguais, erro/recuperação seguros e três larguras sem overflow/erros JS.
Rebuild e repetição dos testes da imagem em andamento.


Imagem corrigida validada: 22 testes existentes e 24 subtestes passaram; o teste
novo de importação também passou após usar a API pública OpenAPI para consultar
rotas (a versão atual do FastAPI usa routers lazy sem atributo path). Total:
**23 testes da imagem aprovados**, sem skips. A alteração final foi apenas no
teste; não exigiu reconstrução do código da aplicação.


### Publicação dos registros — PR #27

- PR #27 integrada em `5889fbe7e79072fb4c98fec3b596b17b9dd3b4f5`; nove checks
  remotos passaram. Código final `294fcb4c109d3f2b2b06b41ac51c8c7b52c363b2`.
- Imagem `sha256:95f6d5e7edfff0a676419d3e7a332893ab4a12208896082118d0aff532aa2e25`
  publicada; aplicação e broker saudáveis, keeper preservado.
- Backup `20260912T162609Z` restaurado/conferido: 4.261 arquivos, SQLite íntegro,
  todos os digests de checkpoints/baselines válidos.
- Aceite publicado: Registros e oito outras rotas, dez APIs 200 e env 410, três
  larguras sem erro JS/overflow. CLI no contêiner leu o evento web.started real.
  Nenhuma geração ou mutação de dados de produção durante o aceite.
- Cinco arquivos protegidos inalterados; 18 sessões, 56 mensagens, 16 turnos de
  runtime, 10 checkpoints e duas baselines preservados.
- Artefatos `/tmp/kairos-service-events-{deployment,preservation,production-browser}.json`;
  implantação também registrada no diretório privado do backup.

## Continuação — limites de ocorrências recorrentes

Branch `feat/cron-limits`, worktree `.worktrees/cron-limits`, incorpora o código de
registros. Limites implementados em armazenamento, CLI/API e tela Agendamentos;
reserva durável consome orçamento mesmo com falha/unknown. Crash entre SQLite/JSON,
duplicata e pausa/retomada não permitem ultrapassar o limite.

- 53 novos casos backend e 11 API/CLI; revisão independente sem bloqueadores.
- CI completa: **2.091 Python + 5.579 subtestes**, **166 Web**, **17 TUI**,
  **18 desktop**; lint, formato, tipos, shell, recall e lock passaram.
- Imagem construída e **23 testes Docker + 24 subtestes** passaram, sem skips.
- Aceite final do navegador com ticker real em andamento; publicação pendente.
- Não há edição de limite existente. Leitura pode mostrar o JSON anterior após
  crash até a próxima reserva/pausa reconciliar o ledger; novo efeito sempre
  verifica o consumo antes de executar. Não é limite financeiro.


### Aceite final dos limites recorrentes

- Chromium com aplicação, scheduler, SQLite e upstream HTTP controlado reais:
  UI criou intervalo de um minuto e limite dois; duas execuções concluíram e o
  tick seguinte não gerou uma terceira chamada.
- Histórico e reload preservaram 2/2, next_run_at nulo e estado esgotado.
  Pausa/retomada não renovou o limite; padrão ilimitado e once com campo oculto
  verificados. 390/900/1400 px sem overflow ou erros JS; capturas inspecionadas.
- Artefatos `/tmp/kairos-cron-limits-{browser.json,ci.log,container.log,review.md}`.
  Servidor descartável encerrado; nenhum job criado na instalação da usuária.
- Código de aplicação permanece o verificado após rebase sobre a main PR #27.
  Entrega remota e implantação ainda serão registradas após seus checks.


### Publicação dos limites recorrentes — PR #28

- PR #28 integrada em `880936df160b1a976203030be7ef5d0d1ec48234`; nove checks
  remotos passaram. Código final `0daf9ea56e56a75aba2a8bf213a66190526a81d3`.
- Imagem `sha256:daf12829c962e13ae5f0fca1fdf8338830f6d032a866488b6c18ac7595fe6382`
  publicada; aplicação/broker saudáveis e keeper preservado.
- Backup `20260912T163604Z`: 4.268 arquivos restaurados/conferidos, SQLite íntegro
  e digests de checkpoints/baselines válidos.
- Aceite publicado em Chromium: limite vazio disponível só em recorrentes,
  campo oculto em once, dez APIs 200/env410, três larguras sem erros JS/overflow.
  Nenhum envio de formulário nem job de teste na instalação da usuária.
- Cinco arquivos protegidos idênticos; 18 sessões, 56 mensagens, 16 turnos,
  10 checkpoints e duas baselines preservados.
- Artefatos `/tmp/kairos-cron-limits-{deployment,preservation,production-browser}.json`.

## Continuação — métricas reais na CLI insights

Branch `feat/usage-insights`, worktree `.worktrees/usage-insights`, base PR #28.
`kairos insights [--json]` implementado com leitor compartilhado pela API de uso.
Preserva totais acumulados, custos conhecidos/estimados/desconhecidos e ausência
de janela/série temporal. Leitura não executa modelo nem abre cofre.

Revisão identificou duas correções necessárias no caminho herdado:

- URI SQLite escapada para impedir que `?mode=rwc` em um nome de diretório
  altere a abertura e crie banco fora do home. Testes de ?, #, %, Unicode e
  caminhos relativos passam.
- Valores numéricos inválidos ou soma não finita retornam indisponível com JSON
  válido, sem declarar custo completo ou emitir Infinity.

A conexão é de leitura dos dados, mas SQLite pode criar auxiliares WAL/SHM.
Esse limite está documentado e transações confirmadas no WAL ativo são lidas.
Não usar immutable, que esconderia dados ainda não consolidados no arquivo DB.
Validação final/aceite externo controlado e entrega ainda em curso.
Manual: [Métricas](metricas.md).


### Verificação final de insights

- CI local final: **2.164 testes Python + 5.581 subtestes**, **166 Web**, **17 TUI**,
  **18 desktop**; lint/formato/tipos/shell/recall/lock passaram.
- Revisão final sem bloqueadores após URI escapada, teste de WAL ativo e rejeição
  de dados numéricos inválidos. Custos desconhecidos seguem None; subtotais
  conhecidos não se tornam custo completo.
- Aceite real em armazenamento descartável: WebSocket/HTTP/adaptadores/SQLite,
  um turno com busca e duas chamadas, 42 tokens e US$ 0,000108 estimado. CLI em
  processo novo e API idênticas após reabrir o banco. `days=7` declara janela não
  suportada e preserva total acumulado. Sem dados privados no relatório.
- Arquivos de dados/config/cofre preservados; auxiliares SQLite explicitamente
  permitidos pelo contrato. Servidor do aceite encerrado; porta 9140 livre.
- Imagem construída e smoke da CLI real passou: banco descartável, uma chamada,
  nove tokens, custo desconhecido preservado. Docker completo ainda em finalização.
- Artefatos `/tmp/kairos-usage-insights-{ci-final.log,acceptance.json,review.md,image-cli.json}`.
  Publicação ainda pendente dos checks remotos e conferência da instalação.


### Publicação de insights — PR #29

- PR #29 integrada em `901e1bd39f26928c2061bef6f16e854da14ca6f3`; os nove checks
  remotos passaram. Código final `93de4481191daa0385e26f4ec38a6843394bd5eb`.
- Imagem `sha256:e68a6d4e6bcd78f92b08cab00720ed5c253fea0eafab50be4b59021fa95a5a02`
  publicada e saudável. Broker e keeper preservados. **23 testes Docker +24
  subtestes** passaram sem skips, além do smoke da CLI na imagem real.
- Backup `20260912T164836Z`: 4.268 arquivos restaurados e conferidos; SQLite
  íntegro, checkpoints e baselines com todos os digests válidos.
- Aceite publicado passou: API e CLI idênticas; sete chamadas e 901 tokens já
  existentes na contabilidade, custo desconhecido preservado. `days=7` mantém
  all_time e informa janela indisponível. Anônimo recebe 401; sem erros JS.
- Nenhuma geração ou mutação de conteúdo no aceite publicado. Cinco arquivos
  protegidos inalterados; 18 sessões, 56 mensagens, 16 turnos de runtime,
  10 checkpoints e duas baselines preservados.
- Ambiente Python do checkout principal atualizado para descobrir o novo pacote
  observability; imports de registros e métricas conferidos.
- Evidências `/tmp/kairos-usage-insights-{deployment,preservation,production}.json`,
  `/tmp/kairos-usage-insights-{ci-final,container}.log`; implantação também salva
  no diretório privado do backup.

## Checkpoint de continuidade após PR #29

Os lotes de modelos/configurações/sessões, cron operacional, busca no Chat,
registros, limites recorrentes e insights estão integrados e publicados conforme
os aceites acima. Os servidores descartáveis dos testes foram encerrados.

O escopo integral permanece aberto. Não converter testes de adaptador com HTTP
controlado em promessa de geração externa. O modelo gratuito OpenRouter testado
continua sem geração bem-sucedida validada com `data_collection: deny`, conforme
a decisão de privacidade; isso não significa bloqueio genérico da conta.

Pendências que exigem implementação ou aceite próprio:

- 26 comandos CLI ainda sem handler, listados no inventário. Subcomandos e paridade
  completa de grupos existentes também precisam de conferência.
- Monitores de fonte, notepad, blueprints, schedulers externos e entrega a canais.
- Provedores além dos oito canônicos e as integrações de plataforma previstas;
  credenciais e contratos específicos serão necessários para seus aceites reais.
- TUI/desktop, MCP, plugins, perfis isolados e skills ainda precisam de aceite
  operacional específico. Ollama operacional permanece adiado pela decisão anterior.

Próximo recorte recomendado pela auditoria: definir o diagnóstico local de CLI a
partir das fontes reais existentes, sem herdar upload automático do debug legado.
`verify` depende de um executor de receitas ainda ausente; `memory` também não tem
um executor nativo equivalente pronto para simplesmente ligar ao comando.
Auditoria `/tmp/kairos-next-cli-audit.md`. Não marcar esses comandos como prontos
apenas por adicionar um handler ou uma interface.

As notas locais de entrega anteriores foram incorporadas aos PRs seguintes e
preservadas adicionalmente em stashes identificados. Não reaplicar esses stashes
sem comparar o conteúdo: já está documentado aqui.

## Continuação — diagnóstico local de Chat, Modelos e Provedores

Branch `feat/local-diagnostics`, base PR #30 (`b47018d`). `kairos debug [--json]`
agora observa configuração e catálogo canônicos, schema/tabelas do Chat, diário
operacional e RPC `runtime.status` no socket existente. Não abre cofre, não chama
provedores, não atualiza catálogo, não migra dados nem inicia runtime.

Autenticação e geração permanecem `not_tested`, integridade completa do banco
também. `complete` significa apenas fontes locais observadas; falta de runtime
ou diário pode não impedir Chat. Política OpenRouter é identificada como padrão
da aplicação, mantendo `data_collection: deny`, sem alegar inspecionar conta ou
conversas. Saída não contém IDs de modelo, paths, URLs, segredos ou erros brutos.

- Chat: streaming, persistência, cancelamento, contabilidade e busca opcional
  exercitados com integração real e provedores HTTP controlados. A geração externa
  bem-sucedida do modelo gratuito selecionado com `deny` continua não validada.
- Modelos: catálogo, seleção, atualização, capacidades, parâmetros e CLI
  implementados; presença/autenticação não equivalem a geração.
- Provedores: oito adaptadores canônicos e gestão de credenciais/configuração/
  conexão implementados; faltam aceites externos específicos e provedores além
  desses oito. Ollama operacional permanece adiado.

26 testes focados passaram: SQLite/configuração/diário/cache reais, servidor Unix
real, timeout, cancelamento e fechamento de conexão, modelo não selecionável,
ausência de home sem criação, preservação de dados e CLI em processo novo.
Revisão independente encontrou quatro falhas corrigidas com regressões:
capacidade inválida do cache não é emitida, preço inválido e JSON do runtime
excessivamente aninhado não interrompem as demais observações, e a seleção global
compartilha a interpretação canônica do Chat. Segunda revisão sem bloqueadores.
Manual: [Diagnóstico local](diagnostico.md). Restam 25 comandos sem handler; o
escopo funcional integral e as demais pendências do checkpoint continuam abertos.

### Retomada e aceite de diagnóstico — 2026-09-13

- CI local final: **2.190 testes Python + 5.583 subtestes**, 13 skips opcionais,
  **166 Web**, **17 TUI**, **18 desktop**; Ruff, formato, tipos, shellcheck, recall
  e lock passaram. Codex dos testes fixado em 0.153.4, sem alterar a instalação
  do usuário. Imagem construída e **23 testes Docker + 24 subtestes** passaram,
  sem skips, com stub s6 derivado da imagem deste lote.
- CLI em dois processos novos, saída textual e JSON, com configuração/SQLite/
  diário e servidor Unix reais em home descartável: complete, saída 0, somente
  duas chamadas runtime.status, dados preservados. Ausência de runtime na CLI
  empacotada retorna incomplete/1 como previsto, com rede do contêiner desativada.
- Consulta à instalação atual pela nova imagem, com volume montado somente para
  leitura, usuário 10000 e rede desativada: configuração/provedor reconhecidos,
  banco schema 3/WAL disponível, registros e runtime prontos. O modelo configurado
  está ausente do catálogo local, portanto o resultado é incomplete. Não houve
  atualização de catálogo, inspeção de credenciais, geração nem troca de modelo.
- Esse aceite usa a imagem de validação; a aplicação em serviço continua na imagem
  publicada no PR #29. Não confundir a consulta com implantação deste lote.
- Relatórios temporários: `/tmp/kairos-local-diagnostics-acceptance.json`,
  `/tmp/kairos-local-diagnostics-production-readonly.json`,
  `/tmp/kairos-local-diagnostics-ci-final.log` e
  `/tmp/kairos-local-diagnostics-container.log`. Os resultados relevantes
  estão registrados aqui para sobreviver à limpeza de `/tmp`.
- Integração e implantação deste lote concluídas conforme o registro abaixo.
  `verify` e `memory` ainda não têm os executores descritos no checkpoint anterior;
  não marcá-los como implementados.

### Publicação do diagnóstico local — PR #31

- Usuária autorizou o merge e as próximas implantações. PR #31 integrada em
  `a896c6fc05cc3b84959c1474a563382b3aa01399`; código revisado
  `abf86305ae3274bf9a69c69b1116073eea8181df`. Os nove checks remotos passaram.
- Imagem `sha256:3780394c72b4ee4a9ef3f89aa8fbbfbd1963df89b1335cfd3572dd57797d3a78`
  publicada na aplicação. Aplicação e broker saudáveis; container/imagem do broker
  e keeper preservados. Porta Tailscale, mounts e ambiente da stack preservados.
- Backup privado `20260913T041107Z`: **4.270 arquivos** restaurados em volume
  descartável e conferidos por conteúdo, proprietário e permissões; dois bancos
  íntegros, **10 checkpoints e duas baselines** com archives/digests validados.
  O backup completo inclui o perfil dedicado e as credenciais, sem publicação.
- **21 sessões, 64 mensagens, oito registros de uso, 16 turnos e 3.476 eventos
  de runtime** preservados, assim como tags e execuções cron. Cinco arquivos
  protegidos permaneceram idênticos. A reinicialização do broker renovou somente
  `runtime_sessions.updated_at` de uma sessão; todos os demais campos foram
  comparados e permaneceram iguais.
- Conferência operacional corrigida para permitir auxiliares WAL/SHM na leitura
  SQLite após parar os escritores e comparar mounts por destino, sem depender
  da ordem devolvida pelo Docker. Uma reversão preventiva para a imagem anterior
  foi concluída saudável antes da repetição bem-sucedida; nenhum backup foi
  restaurado sobre o volume ativo.
- CLI `debug --json` publicada: configuração/provedor/banco disponíveis, registros
  e runtime prontos. Resultado `incomplete` porque o modelo configurado está
  ausente do catálogo local. Nenhuma atualização de catálogo, troca de modelo
  ou geração foi feita para alterar esse resultado; proteção `deny` preservada.
- Aceite publicado em Chromium: **dez APIs HTTP 200**, autenticação por cookie
  httpOnly, anônimo recebe 401 e `/api/env` permanece 410. API de uso e CLI
  insights idênticas. Dez rotas em **390/900/1400 px**, sem erros JavaScript
  nem overflow. Nova conferência após o navegador confirmou os dados protegidos
  idênticos. Volume descartável de restauração removido após o aceite; backup
  privado preservado.
- Evidências duráveis em
  `~/.local/share/kairos-production-backups/20260913T041107Z/`: `deployment.json`,
  `published-acceptance.json`, estados conferidos, inventário e backup completo. Não copiar esses
  arquivos privados para PRs. Este documento registra as evidências não sensíveis.
