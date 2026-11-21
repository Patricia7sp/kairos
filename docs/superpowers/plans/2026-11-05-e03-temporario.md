# Harness temporário do E0.3 — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execução direta, TDD e revisão independente da branch inteira ao final. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Medir negações sintéticas pelo Chat real e sua projeção no navegador do Mac, com dados sanitizados e sem executar a ferramenta negada.

**Architecture:** Ferramental exclusivamente em scripts/e03, montado somente na instância temporária. Gateway stub instalado pelo seam de composição, preservando InteractionService, persistência, rotas e stream da main. Driver usa endpoints existentes, confirma o deny persistido e mede no relógio monotônico do próprio navegador. Launcher mantém dados/credencial próprios, limites de recursos e teardown por identidade.

**Tech Stack:** Python 3.11, unittest/pytest, FastAPI existente, JavaScript nativo/Vitest existente, Docker/Tailscale existentes. Nenhuma dependência nova.

**Spec:** Proposta da usuária aprovada em 05/11 e registrada no PR #132, docs/superpowers/reports/2026-11-02-fase2-plataforma.md (6319238 e precisão subsequente a9baff2). Base de produto: main216cab2; registro documental integrado em main1da925c antes do primeiro commit do harness.

## Global Constraints

- Nunca habilitar/montar o seeder ou dados temporários na stack de produção.
- Única mudança de comportamento do servidor temporário: substituição explícita do factory de gateway. Nenhuma alteração no núcleo, schema, rotas de controle ou contrato do stream.
- Flag explícita KAIROS_E03_TEMPORARY=1; home privado novo e sem credenciais de provider. Gateway tem modelo sintético, chamadas finitas e comando inócuo, e o driver envia somente deny.
- Cada controle só é válido com HTTP200/decided e GET autenticado confirmando tool_result denied para a mesma sessão/tool_call_id. approval_id→tool_call_id é conservado pelo cliente.
- controle_local_ms = max(t_ack_http,t_persist_confirmado_cliente)-t_send, incluindo rede; preservar separadamente os componentes e descrever ACK composto na exportação.
- O fork allow/deny ocorre antes da persistência do resultado; medir deny não prova desempenho de allow.
- R publica stream→DOM e intervalo persistência→DOM: limite inferior t_dom-t_stream; superior t_dom-t_send_deny. A ordem persistir→emitir e enviar deny→persistir fundamenta o intervalo; não inventar clock de commit. O limiar usa o limite superior conservador, explicitamente rotulado.
- Classes denied/timeout/erro; timeout não vira deny. Falhas ficam nas brutas e no denominador; percentis do conjunto ficam indisponíveis se houver amostra inválida, sem imputação numérica.
- ≥30 denies limpos por cenário; p95 nearest-rank ceil(0.95*n), máximo e brutas. Limiares smoke de 2000ms C/3000ms R; não são SLA.
- Sem segredo em argumentos, console, exportações ou Git. Credencial da temporária somente em arquivo privado e memória; não reutilizar credencial da produção.
- Se o seeder exigir alterar o núcleo além do seam, encerrar tentativa de C e registrar parcial; não fabricar percentil.

## Review Focus

- Resposta decided anterior à persistência: nunca fechar C só pelo200; prova do registro canônico obrigatória.
- Timeout produz o mesmo denied do usuário no core: exigir decisão HTTP aceita e classificação do prazo no harness; não deduzir sucesso do conteúdo isolado.
- tool_result não contém approval_id: correlacionar pelo call_id/session_id guardados; rejeitar duplicata ou mismatch.
- Ordem DOM/stream/send, aba oculta, relógio não finito ou registro ausente: lacuna explícita; nunca zero/abs silencioso.
- Início fora da temporária, secret herdado, alvo/ID mudado no teardown: recusa, sem remover recursos alheios.

### Task 1: prova do seeder e medidas válidas

**Files:** scripts/e03/seeder.py; scripts/e03/measurements.py; scripts/e03/__init__.py; tests/test_phase2_benchmark.py; tests/test_e03_temporary.py.
**Interfaces:** SyntheticGateway com catalog/prepare/stream/aclose; install_gateway_seam(home) somente sob flag e home privado. summarize_samples(samples,minimum=30) -> JSON schema1 com contagens, percentis ou lacunas. Consome rotas de aprovação/mensagens e stream existentes.

- [x] RED: testes de ausência de flag/home inadequado, gateway real de composição+deny persistido sem execução; estatística com insuficiência, erro/timeout, correlação/ordem inválida.
- [x] GREEN: gateway finito que emite mutador sintético no primeiro round e resposta de término após tool_result; instalar somente o factory na temporária.
- [x] Confirmar RED/GREEN e executar a regressão existente de deny, HTTP400/403/404/409 e persistência. Se exigir núcleo, aplicar condição de saída.
- [x] Commit em português e registrar no ledger resultados/rulings.

### Task 2: driver no navegador e exportação

**Files:** scripts/e03/ui/index.html, collector.js, channel.js, page.js; scripts/e03/serve.py; web/src/__tests__/e03-benchmark.test.js; tests/test_e03_launcher.py (bootstrap).
**Interfaces:** cliente da API existente /api/auth/*, /ws/chat, POST tool-approvals, GET session/messages, PATCH sessions. Coleta e exporta schema1 sanitizado com UA/protocolo/secure-context, confirmação persistida, correlação e intervalos R. Status de cenário vem de artefato sanitizado do launcher, sem novo endpoint funcional.

- [x] RED: reducer/collector recusa HTTPsem commit, call_id trocado, timeout tratado como deny, timestamps inválidos e aba oculta; exportação não inclui tokens/conteúdo arbitrário.
- [x] GREEN: página com login próprio, início/parada, progresso e download; somente deny; no máximo um controle pendente por conexão.
- [x] Verificar DOM visível e duas oportunidades de frame, preservar falhas e diferença entre DOM observado e pixel físico. Teste DOM não substitui execução no Mac.
- [x] Testar unidade JS e integração local real HTTP/WS, com brutas de ensaio rotuladas Linux, sem alegar Mac.

### Task 3: launcher, cargas e operação temporária

**Files:** scripts/benchmark_phase2.py; scripts/e03/launcher.py; scripts/e03/workloads.py; tests/test_e03_launcher.py; protocolo operacional em docs/superpowers/reports/.
**Interfaces:** launcher usa IDs imutáveis, binds RO apenas do ferramental e dados próprios/tmpfs, rede interna e publicação loopback; exposição tailnet HTTPS exclusiva com verificação de não alterar Serve existente. Artefatos privados separados dos públicos sanitizados. Cargas fixas: ocioso, conversa/painel, auditoria sintética, execução isolada e recuperação offline existente, com evidência de exposição no intervalo das amostras.

- [x] RED: validar flag/alvo/configuração, recusar credenciais herdadas e configurações de produção, teardown de identidade divergente, timeout e falha de carga.
- [x] GREEN: controle Docker restrito ao launcher confiável; servidor temporário sem socket Docker, provider credentials ou dados de produção. Credencial própria criada em arquivo privado; nunca impressa.
- [x] Coletar CPU/RAM/swap/disco, OOM e resíduos, associando run_id/cenário/intervalo. Se carga não foi exercitada durante o cenário, resultado parcial.
- [ ] Smoke local, revisão independente do conjunto, checks pertinentes e documentação/PR. Publicar somente a temporária autorizada; nunca mergear/ativar flag no startup de produção.
- [ ] Disponibilizar página para execução real no Mac; registrar falha/lacuna de cliente quando não executado, sem fabricar n ou percentis.
- [ ] Encerrar somente os recursos próprios, conferir resíduos e remover página/dados/credencial temporários após exportação/TTL. Registrar aceite ou parcial com limites.

Checkpoint da retomada: correções da revisão e integração local nos commits `d8abf86`, `88968a0` e `b83d959`. A prova Linux de `b83d959` tem 150/150 negações, relatório recebido pela página e teardown conferido. A publicação HTTPS observada, o envio da atualização ao PR #134 e a coleta no Mac continuam pendentes; a revisão automática exige autorização explícita para o push. Isso mantém Task 3 e o aceite do E0 abertos. Evidências e limites estão no relatório operacional.

## Retomada de 09/11 — transporte e coleta real

O checkpoint acima é histórico: o PR #134 foi integrado em `43cf04d`, com CI/Sonar/deploy conferidos no ledger. A coleta real no Mac terminou em 09/11 com 150/150 negações válidas e teardown confirmado. A falha DOM da coleta anterior não se repetiu; sua causa permanece desconhecida. O [relatório da retomada](../reports/2026-11-09-e03-mac-transporte.md) preserva os resultados e a ressalva de 21 exceções sem classe no contador antigo.

Correção limitada à Task 3, sem mudar o gateway, núcleo, protocolo de medidas, denominadores ou limiares:

- [x] Reproduzir o contador com registro sanitizado de tipo/operação/horário, preservando a coleta original.
- [x] Escrever regressões RED para inicialização indisponível, falha real durante operação e gate que não aprova transporte com falha.
- [x] Aguardar o listener privado antes de usar a ponte; contar `ConnectionRefusedError` esperada do boot separadamente. Manter autenticação obrigatória após a espera.
- [x] Registrar falhas de operação com campos fixos, sem mensagem/payload/credencial; conservar diagnóstico no relatório e atualizar o estado após encerramento.
- [ ] Verificar o fluxo HTTPS completo com o código corrigido, todos os testes pertinentes e revisão independente da branch.
- [ ] Consolidar E0.2/E0.3 e submeter os limites e lacunas ao aceite exigido, sem habilitar E1 por inferência.
