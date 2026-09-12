# Progresso funcional do Kairos

Atualizado em 12/09/2026. Branch de trabalho: `feat/functional-completion`.
Base anterior: PR #23, `40a0626`. A conclusão integral não está declarada.

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

## Bloqueio externo confirmado

Autenticação real OpenRouter e descoberta do modelo configurado passaram. A geração
com `nvidia/nemotron-3-ultra-550b-a55b:free` retornou HTTP 404: a política da conta
não permite o treinamento exigido pelo endpoint gratuito. Não é chave inválida nem
modelo ausente do catálogo. O endpoint consta no catálogo e aceita `max_tokens`.

A geração bem-sucedida com esse modelo permanece **não validada**. Alterar a política
de privacidade da conta depende de decisão da usuária, ou de fornecer outro modelo
compatível. Não mudamos política nem recorremos a outro ID silenciosamente. O teste
usou uma instalação descartável, com configuração e cofre copiados em armazenamento
privado; os originais foram montados somente para leitura.

## Inventário restante, sem herdar conclusões do README antigo

- Chat de modelos transmite eventos de ferramentas, mas não oferece o catálogo nem
  executa ferramentas. Agent Runtime é a superfície existente para execução isolada.
  Um laço de ferramentas no Chat exigiria integração de permissões, execução e retorno
  ao modelo; não marcar essa capacidade como pronta por exibir um cartão.
- Cron operacional foi implementado no lote seguinte, descrito abaixo. Monitores,
  notepad, blueprints, limites finitos recorrentes e entrega externa seguem pendentes.
- APIs legadas `/api/env` e `/api/logs` ainda retornam dados fixos; `/api/cron/jobs`
  foi substituída por administração persistida no lote de cron.
- A CLI declara **50 comandos**. Após implementar `model`, **28** ainda não têm handler:
  acp, backup, claw, console, debug, dump, gui, hooks, import-agent, import, insights,
  login, logout, logs, memory, monitoring, pairing, pause, peer, prompt-size, setup,
  skin, slack, uninstall, update, verify, webhook, whatsapp. Há também subcomandos
  pendentes dentro dos grupos com handler. Esses comandos retornam 69.
- Registry canônico possui oito provedores: anthropic, custom, deepseek, gemini, groq,
  ollama, openai e openrouter. Não confundir isso com os 36 previstos originalmente.
- Gateway tem protocolo de adaptadores/entrega, mas não integra as 23 plataformas
  previstas originalmente. Credenciais externas e testes reais serão necessários.
- TUI/desktop, MCP, plugins, perfis isolados e gerenciamento de skills precisam de
  aceite operacional específico; suas suítes unitárias não certificam uso completo.

## Próxima sequência

1. Finalizar verificação da CLI, pacote Docker e integração desta branch.
2. Registrar PR/CI remota/implantação apenas quando executados e conferidos.
3. Retomar cron operacional, depois demais comandos e conectores, com um aceite
   reproduzível por fluxo. Manter distinção entre falta de código e bloqueio externo.

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
