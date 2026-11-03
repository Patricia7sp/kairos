# Plano mestre da segunda fase do Kairos

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans após a aprovação da entrega correspondente. Steps use checkbox syntax for tracking. Execução por um agente é o padrão; revisão independente continua obrigatória nos gates indicados.

**Goal:** Planejar a Fase 2 mínima (E0 + E1 + E2 de leitura + E4), com E4 em Prioridade Alta, núcleo único e operação comprovada no destino; manter as extensões com autorizações próprias.

**Architecture:** Estender serviços, SQLite, transportes, política, memória e ciclo de skills existentes. A interface própria e a voz são clientes do mesmo núcleo. Tarefas de auditoria e delegação referenciam os turnos e evidências existentes; não criam outro loop de ferramentas, scheduler ou repositório de memória.

**Tech Stack:** Python 3.11 e uv; SQLite; FastAPI; módulos JavaScript efetivamente servidos, TypeScript e Vitest existentes; Docker onde os controles reais forem validados. Novas dependências e providers exigem avaliação e decisão da entrega que os utiliza.

**Spec:** [Kairos especificação evolutiva v0.2](../specs/2026-11-02-kairos-evolucao-v0.2.md), consolidada a partir da [v0.1 original preservada](../specs/2026-10-25-kairos-evolucao-v0.1.txt). [Avaliação das 17 seções e do baseline](../reports/2026-11-02-kairos-fase2-avaliacao.md).

Estado: recomendações documentais incorporadas em 02/11/2026; **E4 — Prioridade Alta**, conforme decisão da usuária. A execução desta rodada é documental. Hotfix, I1 e demais entregas precisam de autorização de implementação; instalar dependências, criar agendas, executar testes ativos, publicar ou compartilhar não decorre de aprovar este mapa. As autorizações da #129/#130 pertencem àquela entrega.

Retomada após H0: a usuária autorizou seguir os próximos passos depois do merge/deploy da #131 (`c6a083f`). A preparação E0 está registrada no [baseline por SHA](../reports/2026-11-02-fase2-baseline.md), na [matriz de plataforma e protocolo](../reports/2026-11-02-fase2-plataforma.md) e no [modelo de ameaças](../specs/2026-11-02-fase2-threat-model.md). E0.1/E0.4 passaram por revisão independente documental e aguardam revisão da usuária; acesso/medições do Mac e aceite do gate E0 continuam pendentes. Isso não habilita I1/E4 ou as extensões.

Atualização de 03/11/2026: a usuária enviou a identificação inicial do Mac por coleta local (macOS `26.7.2`, Intel x86_64, 16 GiB, Docker CLI `28.1.1`, contexto `desktop-linux`). As consultas ao daemon falharam; os campos vazios/zero não são capacidades observadas. Versão do aplicativo Desktop, Engine/VM, controles e medições seguem pendentes na matriz. O gate E0 permanece aberto.

Segunda coleta de 03/11/2026: daemon agora acessível, Docker Desktop `4.41.2 (191736)`, Engine `28.1.1`, kernel `6.10.14-linuxkit`, 12 CPUs lógicas e `8219107328` bytes de memória declarados pela VM (cerca de 7,65 GiB). SecurityOptions informa seccomp `unconfined` e cgroupns, sem AppArmor listado. A matriz registra a lacuna frente ao contrato atual de WorkerPolicy; configuração, atestação real do worker e E0.3 continuam pendentes. Esta coleta não encerra o gate nem altera a política de segurança.

## Escopo e sequência prioritária

**Fase 2 mínima: E0 + E1 + E2 de leitura + E4. E4 tem Prioridade Alta.** A ordem de preparação é H0 separado → E0 → I1 reordenado → demais contratos/integrações de E1 → E4; E2 de leitura pode avançar em paralelo com E4 assim que suas fontes estiverem disponíveis. E4 não espera E2 visual nem E3. H0 é antecipável por ser correção limitada de defeitos existentes, sem alterar o gate do destino.

E2 de leitura cobre E2.1 e a parte de projeção/reconexão/acessibilidade de E2.3; E2.2 e controles mutáveis do novo painel ficam em extensão autorizada de E2. As aprovações/cancelamento existentes atendem E1/E4. E3 é opcional/adiável. E3, E5, E6 e Trilha S ficam fora da fase mínima, cada uma com autorização própria; preparar o harness V0 não autoriza E5.

| Entrega | Prioridade/posição | Dependências e limite |
| --- | --- | --- |
| H0 | P0, entrega separada antes de I1 | Autorização limitada; independente do Mac; não entrega E1.1/E1.4 |
| E0/E1 | Base necessária para delegação segura | Gate Mac e threat model; I1 dividido conforme o fim deste plano |
| E4 | **Prioridade Alta** | E0/E1 e nota TaskService; não depende de voz nem de UI nova |
| E2 de leitura | Paralela quando houver fontes | Consultas/projeções autenticadas; sem controles mutáveis novos |
| E3 | Opcional/adiável, após a prioridade de E4 | Autorização, decisão local/nuvem e benchmark próprios |
| E5/E6/Trilha S | Extensões fora da fase mínima | Autorizações e gates próprios |

## Restrições globais

- Alvo informado: Mac Intel com 16 GB de RAM. Não pressupor Apple Silicon, GPU ou servidor Linux disponível.
- Um núcleo, sessão e serviço de interação compartilhados; nenhuma autoridade no frontend ou em texto produzido pelo modelo.
- Defaults propostos da SPEC: uma execução pesada; pai e um nível de filhos; workers existentes de 512 MiB; perfil de scanner separado até 1 GiB se necessário; orçamento Docker Desktop inicial de 3–4 GiB a medir.
- Voz inicial: pressionar para falar; transcrição editável; captura proposta até 60 segundos; áudio sem retenção por padrão; nenhuma troca silenciosa de local para nuvem.
- Retenção sanitizada proposta de 30 dias, limitada por teto de armazenamento, alerta antes do descarte e preservação explícita de incidente.
- Smoke de E0: pelo menos 30 controles por cenário durante auditoria representativa; p95 empírico de controle_local_ms até 2 s e evento_painel_ms até 3 s, definidos em E0.3. Publicar amostras e máximo; esse volume não estabelece cauda de produção nem SLA. Nenhum OOM do núcleo ou processo órfão.
- Execução de auditoria, cobertura e resultado da política são dimensões separadas. Alto/crítico não excepcionado bloqueia; check obrigatório ausente impede pass. Não elevar silenciosamente o limiar para médio/baixo.
- Nenhum scan ativo sem autorização exata de alvo, método, janela e limites; métodos destrutivos, DoS e extração desabilitados nesta versão.
- A0/A1 como início; A2 produz candidatos isolados e revisados; A3 permanece desabilitado.
- Nenhum wildcard para integrar outra origem, socket Docker no worker, mount amplo, modo privilegiado ou fallback para shell do host.
- Coleta técnica determinística não depende de LLM. Conteúdo externo, resultados de ferramentas e filhos são dados, não instruções de sistema.
- Python, lint, testes, imagem, migrações e CI seguem AGENTS.md. Sem testes que leem implementação ou congelam catálogos, contagens ou versões atuais.

## Baseline e validade das evidências

Revalidar a partir de 89020109ad1d063f06dba9b685681e15cb22590a, citado na SPEC. A preparação inicial consultou main em 4fe697bb9e05e5cc1c3cce7f4d9476cd851f42a2; a correção da #129 foi integrada pela #130 em 23b4442603bec50481f9ca623436dfe76e3c2f8b, com delta apenas em teste/plano e CI/deploy concluídos. O checkout documental continua na base 4fe697b; isso não altera o SHA de produto citado nem transforma revisão do plano em validação técnica. Antes da execução, atualizar refs e registrar o SHA efetivamente escolhido, o diff e o estado do checkout.

Este é um plano mestre de escopo e gates. Antes de implementar cada entrega autorizada, fechar seu plano com interfaces, testes e comandos exatos após o design pertinente; o mapa não é autorização para implementar todos os módulos.

Os arquivos existentes citados abaixo foram localizados no baseline. Arquivos novos, tipos e assinaturas são propostas deste plano, não APIs já implementadas. E0 e cada design de entrega confirmam os pontos de extensão antes de escrever código. Um teste planejado não é evidência executada.

## Foco de revisão

| Condição de risco | Comportamento exigido | Responsável |
| --- | --- | --- |
| Alvo, parâmetros, política ou permissão mudam enquanto a tarefa espera | Revalidar antes do efeito; autorização antiga não serve | E1.4 e E4.2 |
| Detector falha ou cobertura some depois de achado anterior | unknown/partial visível; não resolver o achado por ausência de dados | E1.1, E1.6 e E1.7 |
| Diretório muda, tem links ou perde capacidade de observação no destino | Recusar captura/efeito; não trocar para leitura insegura | E0.2 e E1.2 |
| UI perde conexão depois da aceitação; serviço morre durante efeito | Recuperar por chave/cursor; não reenviar efeito incerto | E2.3 e E4.3 |
| Orçamento, disco ou memória esgotam; cancelamento chega durante aprovação | Recusa/falha explícita; cancelar continua acessível; sem órfãos ou autoridade ampliada | E0.3, E1.5 e E4.3 |

## Contratos e responsáveis

### Tarefa e resultado

Estender a execução existente com uma projeção persistida de trabalho, em vez de duplicar runtime_turns. Toda tarefa tem task_id, parent_task_id opcional, session_id, project_id, perfil, dependências, backend, autorização, limites e referências de resultado. O resultado informa validações, artefatos, erros e limitações; nunca usa o texto do modelo como prova de sucesso.

Estados públicos: queued, running, awaiting_approval, succeeded, failed, cancelled, timed_out, interrupted. Adaptar os estados internos do runtime para essa projeção sem reescrever seu journal, fencing, leases ou send_state. Cancelamento solicitado pode continuar running com metadado de cancelamento até confirmar inatividade; não emitir cancelled antecipadamente.

TaskService em kairos_integration/task_service.py é adaptador/projeção. Antes de implementar TaskService, TaskRecord ou migrações, entregar a nota docs/superpowers/specs/<data>-task-service-design.md, revisada independentemente. Invariantes obrigatórias:

- RuntimeStore é fonte única de estados dos turnos, fila de turnos, leases, fencing, send_state e reconciliação; AgentRuntimeService continua executando.
- AuditRun mantém o estado canônico de coleta sem conversa/turno artificial. TaskRecord referencia runtime_turn_id e/ou audit_run_id por FK e projeto consistente; a nota fixa nulidade, composição de estado/resultado e vínculo para cada tipo de tarefa.
- TaskRecord não mantém fila própria, não inicia/reenvia efeito e não possui terminalidade concorrente. A nota define a extensão da admissão comum para auditorias e o mapeamento de estados, sem outro executor.
- Cancelamento propaga ao serviço canônico e reconcilia; só confirma cancelled após inatividade. Recuperação não reenvia efeito incerto.
- Migração, backup/restauração, idempotência e reconciliação são desenhados antes de código. Relações e projeções não criam nova fonte de verdade.

Chave de idempotência é escopada ao operador/projeto e ao digest da solicitação. Chave repetida com parâmetros diferentes é conflito. Reinício com efeito incerto gera interrupted/reconciliação, nunca reenvio automático ou promessa de exactly-once externo.

### Autorização

Proposta de módulo: kairos_tools/authorization.py. Tipos Principal, ActionRequest e ActionGrant vinculam operador autenticado, projeto, recurso/destino canônico, ação, digest dos parâmetros, expiração e revisão da política.

Interfaces propostas:

    authorize_action(principal: Principal, request: ActionRequest, *, parent: ActionGrant | None = None) -> ActionGrant
    validate_action(grant: ActionGrant, request: ActionRequest, *, now: datetime, policy_revision: str) -> None

Principal vem do backend autenticado; nunca do corpo enviado pelo modelo. Delegação recebe interseção de ferramentas/destinos e o menor teto/prazo entre pai e perfil do filho. Revogação e parâmetros relevantes são verificados novamente no executor.

Usar os mecanismos atuais de aprovação no chat/runtime como superfícies e adaptadores para esse vínculo. Aprovação de execução, publicação, instalação, credencial e segurança são ações distintas. Negação é terminal para aquela solicitação; não tentar outro transporte.

E1.4a é a dependência mínima de E1.1/E1.2: Principal autenticado pelo backend e AuthorizedTarget, composto de project_id, identidade canônica do diretório/snapshot, revisão e ActionGrant de leitura. O serviço valida essa referência antes da captura; o scanner não transforma path arbitrário em autorização. O design dessa fronteira passa por revisão independente antes da implementação.

E1.4b, posterior a E1.2, trata aprovação/revogação de AuditException e adaptação das aprovações existentes. Aprovar AuditException é ação própria da superfície autenticada existente, com fingerprint, alvo/escopo, revisão, motivo e expiração concretos. O backend deriva Principal, valida ActionGrant da ação de exceção e grava aprovação/revogação verificável; o modelo não fornece um booleano de aprovação. E1.1 é lançado com exceções desabilitadas e alto/crítico sempre fail; qualquer entrada de exceção é recusada, inclusive por configuração ou JSON. Após E1.4b, somente registros validados podem chegar à política; fixtures não habilitam exceção real. Exceções indisponíveis não bloqueiam lançar E1.1.

### Auditoria

Estender SecurityReport em kairos_security/models.py como representação de AuditRun, mantendo uma fonte canônica. Acrescentar versão do schema, run_id, perfil e versão, alvo/revisão, horários, versões dos detectores, checks esperados/executados, skips/erros e execução/política.

- execution_state: completed, partial, failed, cancelled.
- policy_outcome: pass, fail, unknown.
- CheckResult: check_id, detector/version, estado, obrigatoriedade, condição verificável de inaplicabilidade e erro sanitizado.
- Finding: preservar os campos existentes e acrescentar fingerprint, confiança e identidade do detector.
- Alto/crítico não excepcionado produz fail mesmo com cobertura parcial; ambas as informações permanecem. Sem bloqueio, check obrigatório ausente produz unknown; cobertura completa sem bloqueio produz pass.
- Exceção exige operador, motivo, validade e vínculo com achado/escopo. Exceção não suspende redaction.

Perfis versionados: snapshot_static, dependency_advisories e runtime_config, com os checks obrigatórios da seção 8.3. Ferramenta ou acesso indisponível não é not_applicable. Lint de prompt, listas de nomes e SAST limitado não substituem teste adversarial do caminho real.

### Eventos e evidências

Estender o journal operacional com schema_version, event_id único, timestamp UTC, session_id, task_id, parent_task_id, project_id, component, action, target_ref sanitizado, policy_decision, result, policy_revision, config_revision e justificativa curta sanitizada. Eventos de ferramenta incluem tool_call_id e referência ao pedido/resultado sanitizados e limitados; não copiam argumentos brutos. Distinguir a versão do schema das revisões de código/configuração/política aplicadas.

Novos eventos exigem identidade/horário/componente/ação/resultado e revisões aplicáveis. Campos de correlação podem ser nulos somente em eventos globais do serviço cujo catálogo declare essa condição; não são opcionais em uma ação vinculada a tarefa/projeto. Inaplicabilidade de decisão/política é valor declarado pelo tipo de evento, nunca preenchimento silencioso para uma falha de coleta. Manter leitura dos registros legados sem fabricar campos históricos ausentes.

Dados são sanitizados antes de armazenamento, LLM e UI; o filtro estrutural atual do runtime não promete remover segredos escritos em texto livre.

Artefatos usam referência vinculada à tarefa/projeto, tamanho/tipo/path verificados e acesso autorizado. Material bruto sensível, se indispensável, fica separado e restrito, com retenção explícita. Não armazenar raciocínio privado. Falha de evidência obrigatória impede admitir novo efeito que dependa dela; negação e cancelamento continuam possíveis e o estado degradado fica visível.

## H0 Hotfix P0 de segredos — entrega antecipável

Files: kairos_security/checks/secrets.py; tests/test_security_audit.py e testes sintéticos focados de redaction. Reutiliza a API check_hardcoded_secrets(code_root: Path) -> list[Finding] e os formatadores atuais; nenhuma dependência nova pressuposta.

- [x] RED com fixtures temporárias: .env e .env.* ignorados, chaves OpenAI sintéticas não detectadas e fragmento de segredo em Finding.evidence/saídas. Fixtures não usam nomes/marcadores que façam o detector pular a linha antes do caminho sob teste.
- [x] Corrigir somente seleção por nome, regex OpenAI e construção de evidence sanitizada. Não criar captura de novos alvos, contrato E1.1 nem autoridade/exceções de E1.4.
- [x] GREEN: detector real encontra os casos sintéticos; evidence não contém fragmentos brutos da credencial nem trechos da linha. Verificar serialização JSON e resumo CLI pelos formatadores reais.
- [x] Executar regressões pertinentes, revisão independente e CI do SHA da entrega; registrar limites e rollback. H0 não afirma corrigir toda a captura/leitura/travessia do scanner nem a política de aprovação.

Fechado pela #131, integrada por squash em `c6a083f`, com CI/Sonar/deploy e verificação do serviço. Evidências, falhas ambientais corrigidas e limite de cobertura constam do [plano H0](2026-11-02-h0-segredos.md) e do [baseline E0](../reports/2026-11-02-fase2-baseline.md). A omissão foi aplicada também aos dois produtores SAST, preservando arquivo/linha e sem criar autoridade nova.

Verificação proposta: uv run pytest -q tests/test_security_audit.py tests/test_security_redaction.py; Ruff; scripts/ci.sh e CI pertinentes antes de merge. Commit: fix(security): detecta env e sanitiza evidencia de segredos. Ponto de parada: entregar para revisão, sem habilitar scan de novos alvos. Autorização da entrega é separada da edição documental.

## E0 Confirmar a base e o destino

### E0.1 Inventário e diff da revisão

Files: docs/decisoes.md; novo docs/superpowers/reports/<data>-fase2-baseline.md. Consultar os módulos do inventário da SPEC e seus testes reais.

- [x] Registrar SHA, diff desde 8902010, mudanças posteriores relevantes, ambiente, dependências/lock e evidências reutilizáveis.
- [x] Para cada capacidade, distinguir demonstrada, parcial, ausente e indisponível; apontar módulo, contrato, teste e lacuna.
- [x] Preservar a evidência da #129 e as melhorias já integradas de arquivos, skills e cancelamento; não reimplementar essas correções.
- [x] Conferir referências e estado do checkout; revisão do relatório. Commit documental proposto: docs(fase2): revalida baseline e capacidades.

Aceite: inventário completo e reproduzível, sem converter existência de arquivo ou nome em prova de capacidade.

### E0.2 Gate de plataforma e isolamento

Files: kairos_filesystem/tree.py e descriptors.py; kairos_runtime/experimental/worker_policy.py; Dockerfiles; novo relatório de capacidades do Mac. Nenhuma alteração de política é pressuposta.

- [ ] Registrar macOS, Intel/RAM, Docker Desktop/engine/kernel da VM e versões do artefato autorizado.
- [ ] Inspecionar e testar usuário, rede, mounts, capacidades, recursos, encerramento, filesystem e a política efetivamente aplicada.
- [ ] Incluir o uso atual de /proc/self/fdinfo e O_PATH no inventário de incompatibilidades nativas. Definir por decisão revisada se o backend roda na VM Linux existente do Docker Desktop ou se exige adaptação nativa equivalente.
- [ ] Caso seja necessária adaptação, produzir design e testes reais do destino antes de implementá-la. Capacidade não comprovada resulta em unavailable; não remover verificação nem usar shell do Mac.
- [ ] Registrar alternativa de executor Linux separado somente se a usuária decidir disponibilizá-lo.

Aceite: matriz por backend/perfil de capacidades observadas, recusas e unavailable, com SHA, ferramentas, configuração, cenário e evidência. CI/sondas Linux valem para desenvolvimento e regressão naquele backend; não encerram o gate Mac. Falta de acesso/capacidade no Mac mantém o gate pendente e os perfis dependentes unavailable.

Timebox proposto: cinco dias úteis desde a abertura de E0.2, registrando início e data-limite. Entregar até esse prazo a matriz Linux/Mac e as lacunas; pedir decisão concreta sobre acesso ao Mac, adaptação equivalente ou disponibilização de backend alternativo. Vencimento não aprova capacidade, não escolhe servidor Linux nem autoriza execução/background no destino. H0 pode avançar sob sua autorização limitada.

### E0.3 Benchmark e limites iniciais

Files propostos: scripts/benchmark_phase2.py; tests/test_phase2_benchmark.py; relatório com hardware, projeto, versões e amostras.

Interface proposta: summarize_samples(samples: Sequence[ControlSample]) -> BenchmarkSummary, com contagem, percentil e lacunas. Coletor por plataforma declara métricas indisponíveis.

- [ ] Definir ControlSample com cenário/caminho, correlation_id, envio/ACK monotônicos no cliente, evento persistido/renderização, estado e lacunas. controle_local_ms mede envio autenticado → ACK após validação/persistência da aceitação/negação; não espera o efeito. Medir sem inferência/serviço externo no caminho, sem subtração estimada. evento_painel_ms mede persistência → renderização por event_id; documentar alinhamento de relógios ou protocolo equivalente.
- [ ] Testar p95 empírico nearest rank (ceil(0,95 × n)), máximo, amostras insuficientes, erro/timeout e correlação ausente. Preservar falhas no resultado; não descartá-las para melhorar o percentil.
- [ ] Medir ocioso, conversa/painel, auditoria representativa autorizada, execução isolada e recuperação. Voz é medida quando existir em E3.
- [ ] Usar pelo menos 30 controles por cenário durante a auditoria; registrar amostras, RAM/swap do host/VM/serviços, latência, OOM e órfãos. Rotular como smoke threshold: com 30 amostras p95 é a 29ª observação ordenada; não alegar SLA/cauda de produção sem plano amostral próprio.
- [ ] Aceitar os limites da SPEC ou registrar revisão explícita. Se falhar, serializar/reduzir perfil; não enfraquecer isolamento nem usar nuvem silenciosamente.

Verificação futura: uv run pytest -q tests/test_phase2_benchmark.py, seguida de benchmark no Mac autorizado. Commit proposto: feat(operacao): mede limites da segunda fase.

### E0.4 Threat model e classificação de dados

Files: novo docs/superpowers/specs/<data>-fase2-threat-model.md, artefato versionado vinculado à revisão de arquitetura e matriz de testes.

- [x] Inventariar ativos: credenciais, estado/journal, snapshots/código, políticas/aprovações e artefatos. Desenhar fluxos e fronteiras UI/API, núcleo/LLM/ferramentas, pai/filho, filesystem/worker, estado e egress.
- [x] Declarar atacantes/capacidades: cliente não autenticado, página/resultado/filho malicioso, arquivo mutável e dependência comprometida; explicitar premissas sobre host/mesmo UID e riscos residuais.
- [x] Classificar dados públicos, internos por projeto, pessoais/sensíveis e segredos/credenciais; definir destinos, ACL, sanitização e retenção. Em conflito usar a classe mais restritiva. Segredos reais permanecem no serviço local protegido existente.
- [x] Mapear ameaça → controle → teste real → responsável → lacuna. Revisão do design antes de E1.4a; atualizar/revisar antes de E1.4b, E4 e S. Documento não é atestado de eficácia nem autorização de coleta.

Artefato E0.4 revisado independentemente e pronto para revisão da usuária; E0.2/E0.3 e o aceite do gate E0 permanecem pendentes. Não interpretar os checkboxes documentais como validação técnica do destino ou habilitação de I1.

Gate E0: baseline, capacidades/medições do destino e threat model registrados; limites aceitos ou explicitamente revisados antes de habilitar background/voz. O relatório de E0.4 é pré-requisito do design de autoridade; CI Linux não aprova Mac.

## E1 Tornar segurança confiável

### E1.4a Principal e alvo autorizado — fronteira mínima

Files: kairos_tools/authorization.py proposto; adaptadores de identidade existentes CLI/Web/runtime e integração de captura de snapshot. Dependência: threat model E0.4 e design da fronteira revisados; gate E0 aceito no I1.

Interfaces: Principal derivado no backend; AuthorizedTarget com project_id, identidade canônica/revisão e ActionGrant de leitura; validate_action revalida recurso, revisão, prazo/política e revogação antes da captura.

- [ ] RED: principal fornecido pelo modelo, projeto/target trocado, recurso sem autorização, grant expirado/revogado e revisão divergente são recusados antes de leitura.
- [ ] Implementar só o vínculo de identidade/alvo/leitura necessário a E1.1/E1.2 pelas superfícies autenticadas existentes. Nenhuma aprovação de exceção nem migração geral das ferramentas é necessária aqui.
- [ ] GREEN pelo despacho/leitura reais, com fixtures temporárias e primitivas existentes de filesystem. Alvo arbitrário não se torna autorizado por normalização de path.

Verificação: novos tests/test_audit_target_authority.py e regressões de workspace/security_file_boundaries. Commit: feat(policy): autentica principal e alvo de auditoria. Gate: revisão independente da fronteira e testes de recusa reais; exceções continuam desabilitadas.

### E1.1 Resultado, cobertura e perfis

Files: kairos_security/models.py, auditor.py, formatter.py; novo kairos_security/policy.py; kairos_cli/handlers.py; tests/test_security_audit.py e novos testes de política/perfis.

Dependência: E1.4a (Principal autenticado + AuthorizedTarget/leitura) e H0 sanitizado. Exceções ficam desabilitadas; E1.4b não bloqueia lançar o auditor. Não substituir identidade/autorização por campo de JSON do modelo.

Interface inicial proposta: evaluate_audit_policy(report: SecurityReport, *, now: datetime) -> PolicyOutcome. E1.4b acrescenta o parâmetro exceptions: Sequence[AuditException] = () somente com adaptador autenticado e validação; não há exceções aceitas em E1.1.

- [ ] RED: auditoria vazia/incompleta não passa; alvo ausente, detector indisponível e falha de importação aparecem como cobertura/erro.
- [ ] RED: coleta concluída com alto/crítico é completed + fail; coleta parcial com alto preserva partial + fail; falta obrigatória sem alto é unknown.
- [ ] RED/GREEN sem exceções: alto/crítico sempre fail; entrada de exceção por modelo, relatório, UI ou configuração é recusada explicitamente. Os testes de aprovação/validade de exceção pertencem a E1.4b.
- [ ] Implementar o contrato acima e os três perfis, com critérios verificáveis de not_applicable.
- [ ] Antes da mudança, inventariar consumidores de passed/security_score, formato JSON, código de saída CLI e --fail-on: formatadores/CLI atuais, Web/cron/relatórios e consumidores externos documentados. Registrar o que existe e o que é futuro. Definir migração/versão e teste por consumidor afetado.
- [ ] Manter limiar padrão high e opção explícita CLI; alinhar resultado estruturado, texto e exit code por política versus erro/cobertura incompleta. Adaptar passed; score não decide aprovação.
- [ ] GREEN: testar JSON versionado, saída CLI e códigos de política versus falha operacional. Revisar compatibilidade com consumidores.

Verificação: uv run pytest -q tests/test_security_audit.py tests/test_security_policy.py tests/test_security_profiles.py. Commit: feat(security): separa coleta cobertura e politica.

### E1.2 Snapshot e segredos pelo caminho seguro

Files: kairos_security/checks/secrets.py e source_code.py; kairos_filesystem/contract.py e tree.py como integração; novo kairos_security/snapshot.py; testes sintéticos de scanner.

Interface proposta: capture_audit_snapshot(target: AuthorizedTarget, limits: TreeLimits) -> AuditSnapshot, referenciando TreeCapture e a revisão observada.

- [ ] RED: .env e variantes aprovadas, formatos sintéticos de chaves, arquivo ilegível, limite excedido, troca de diretório, symlink/hardlink e erro de leitura.
- [ ] Capturar alvo autorizado com as primitivas existentes, limitar bytes/entradas/profundidade e registrar recusas como cobertura incompleta. Não percorrer diretórios irrelevantes/estado privado silenciosamente.
- [ ] Reutilizar H0: padrões, seleção por nome e evidence sanitizada. Ampliar a proteção para achado, snippet, erro e relatório no novo caminho de snapshot; não refazer ou desfazer o hotfix. Nunca anexar início bruto de linha com segredo.
- [ ] Exercitar scanner com arquivos temporários e comprovar que a credencial sintética integral não aparece em JSON, CLI, eventos ou entrada de síntese.
- [ ] Alterar o teste de formatador que hoje audita Path.cwd() para fixture controlada, mantendo teste comportamental do scanner.

Verificação: uv run pytest -q tests/test_security_audit.py tests/test_security_snapshot.py tests/test_security_redaction.py. Commit: fix(security): captura alvo e redige segredos com cobertura explicita.

### E1.4b Exceções autenticadas e ações existentes

Files: autorização de E1.4a; registry.py, workspace.py e policy.py; kairos_integration/chat_tools.py e interaction_service.py; superfícies CLI/Web/runtime. Dependência: E1.1/E1.2 e threat model atualizado; nenhuma habilitação por fixture ou configuração.

- [ ] RED por despacho real: página/resultado pede ação negada; aprovação não libera arquivo externo; revogação, parâmetro/política alterado, expiração e troca de transporte recusam execução.
- [ ] Produzir design compatível da migração das aprovações efêmeras do chat para vínculo persistido com ação concreta, parâmetro, projeto/escopo e prazo. Preservar protocolo/negação atuais e recuperar pedidos pendentes sem reaplicar efeito incerto.
- [ ] Implementar aprovação/revogação de AuditException como ação autenticada própria, com fingerprint, alvo/escopo/revisão, motivo e expiração. Backend deriva Principal; modelo não fornece aprovação válida.
- [ ] Estender evaluate_audit_policy com exceptions: Sequence[AuditException] = (); somente registros validados pelo adaptador entram. Sem ele, manter exceções desabilitadas.
- [ ] GREEN: exceção expirada/revogada, outro alvo/revisão e parâmetro alterado conservam fail; exceção válida com check obrigatório ausente conserva unknown. Exceção não dispensa sanitização/cobertura.
- [ ] Reutilizar testes reais de workspace/arquivos/transportes. Reclassificar constantes/prompt curto como lint limitado. Testar destino mutável, symlink/hardlink e recusa entre transportes sem mocks que escondam o despacho.

Verificação: testes workspace, security_file_boundaries, chat_tool_approval, web_chat_tool_approval e runtime_approvals, mais tests/test_action_authorization.py e test_audit_exception_authority.py. Commit: feat(policy): vincula excecoes e aprovacoes ao efeito concreto.

### E1.3 Advisories e configuração de runtime

Files propostos: kairos_security/checks/dependencies.py, runtime_config.py e detector_contract.py; reutilizar manifests/lockfiles e WorkerPolicy; novos testes por perfil.

Interface proposta: Detector.run(snapshot: AuditSnapshot, context: AuditContext) -> CheckResult. O contexto contém somente alvos e egress aprovados, não credenciais gerais.

- [ ] RED: manifest/lock aplicável ausente, serviço indisponível, resposta inválida, backend divergente e ferramenta não instalada nunca viram inaplicabilidade ou pass.
- [ ] Inventariar manifestos aplicáveis sem instalar suas dependências. Selecionar base/serviço de advisories e detector maduro por decisão registrada de origem, versão, licença e egress.
- [ ] Separar consulta aprovada de advisories da análise estática sem rede. Sem serviço aprovado, o perfil permanece unknown/desabilitado.
- [ ] Conferir runtime_config pela política e estado observados no backend declarado, sem inferir configuração a partir de nomes.
- [ ] GREEN com servidor/base de teste local e respostas sintéticas; consulta externa somente após autorização correspondente.

Verificação: uv run pytest -q tests/test_security_dependency_profile.py tests/test_security_runtime_profile.py. Commit: feat(security): declara cobertura de dependencias e runtime.

### E1.5 Persistência e supervisão de auditorias

Files propostos: kairos_state/audit_schema.py, repositories/audits.py e kairos_security/service.py; migração em kairos_state/migrations.py; TaskService apenas se exigido pelo design prévio, sem outro executor. Antes de qualquer código/migração de TaskService/TaskRecord, concluir a nota de design indicada nos contratos, inclusive FK, dono de estado, admissão comum, cancelamento e recuperação.

Interfaces propostas: AuditService.submit(request: AuditRequest, *, principal: Principal, idempotency_key: str) -> AuditRunRef; get(run_id: str, *, principal: Principal) -> SecurityReport; cancel(run_id: str, *, principal: Principal) -> None.

- [ ] RED: submissão repetida, chave conflitante, perda de disco, cancelamento, morte durante coleta e recuperação sem reexecução incerta.
- [ ] Persistir solicitação/escopo antes de iniciar o trabalho; offload de detector não bloqueia o loop interativo. Resultado e artefatos usam o mesmo run_id/projeto.
- [ ] Registrar partial/failed/cancelled e manter logs sanitizados. completed com finding não é crash da tarefa.
- [ ] Testar migração, reabertura do banco, backup/restauração e bloqueio de cliente incompatível onde pertinente.

Verificação: uv run pytest -q tests/test_audit_service.py tests/test_audit_persistence.py. Commit: feat(security): supervisiona auditorias persistidas.

### E1.6 Eventos, detecção e investigação

Files: kairos_observability/service_events.py; novo kairos_security/events.py e investigation.py; pontos reais de negação em Web, ferramentas e worker; tests/test_operational_logs.py.

- [ ] RED: negação de auth/ferramenta, fronteira Docker, escape, escopo de alvo, mudança de skill/política/credencial e falha de coleta geram evento correlacionado sem segredo.
- [ ] Acrescentar códigos e campos versionados; manter leitura de journal antigo. Falha de gravação é resultado explícito e sinal de degradação.
- [ ] Testar presença/validação dos campos canônicos acima, unicidade de event_id, correlação pai/filho e referências sanitizadas de chamadas de ferramentas; revisões distintas não são confundidas. Raciocínio privado e segredo sintético não aparecem em registro/relatório/síntese.
- [ ] Implementar regras determinísticas de suspeita e linha do tempo de leitura por projeto; hipótese recebe confiança, não status de invasão confirmada.
- [ ] Aplicar retenção configurável proposta de 30 dias com teto, aviso antes do descarte e hold de incidente. Testar limite, relógio, rotação e acesso.
- [ ] Investigador não ganha shell administrativo, exclusão de logs ou patch em produção. Corrigir ações somente por proposta/aprovação do impacto.

Verificação: tests/test_operational_logs.py, test_security_events.py, test_security_retention.py e test_security_investigation.py. Commit: feat(observability): correlaciona seguranca e evidencia limitada.

### E1.7 Cron e alertas sem ruído

Files: kairos_cron/monitor.py, scheduler.py e jobs.py; novo kairos_cron/security_monitor.py; serviço de auditoria da E1.5; testes de monitores existentes.

Interface proposta: evaluate_audit_change(previous: AuditSummary | None, current: SecurityReport) -> AuditChange. Normalização exclui timestamp/duração e inclui cobertura/política relevantes.

- [ ] RED: mesmo relatório com outro horário não alerta nem chama LLM; achado novo/agravado/reincidente, crash e perda de cobertura alertam.
- [ ] Registrar estado anterior completo; ausência causada por falha nunca resolve achado. Resolução exige nova evidência válida.
- [ ] Fonte tipada consome resultado estruturado. Não aceitar qualquer exit code no run_script genérico nem ampliar seu timeout/saída.
- [ ] Auditoria longa é tarefa supervisionada consultada pelo monitor. Após suspensão, recuperar a última execução devida, com atraso visível e sem replay de toda a fila.
- [ ] Manter agenda diária leve/semanal ampla propostas e desativadas até configuração/autorização. Coleta independe de LLM; recorrência paga espera orçamento.

Verificação: testes cron_source/monitors/operational existentes e tests/test_cron_security_monitor.py. Commit: feat(cron): monitora auditorias por resultado estruturado.

Gate E1: perfis e cobertura honestos, sanitização, testes reais de política e integração cron/eventos; revisão independente; suíte/CI pertinentes verdes. Nenhuma alegação de pentest ou cobertura global a partir de um único perfil.

## E2 Interface própria e dashboard

Fase mínima: E2.1 e projeção/reconexão/acessibilidade de E2.3 em leitura. E2.2, conversa visual nova, aprovações e cancelamento pelo painel são extensão de E2; os controles existentes continuam atendendo o núcleo. A UI nova não bloqueia E4, cuja Prioridade é Alta.

### E2.1 Projeções autenticadas

Files propostos: kairos_web/assistant_api.py; extensões pequenas em server.py, APIs de sessões/cron e serviço de tarefas/auditoria.

Interfaces propostas: consultas do resumo do dia, tarefas e segurança por principal/projeto, com período, updated_at, links de evidência e disponibilidade de cada fonte.

- [ ] RED: usuário anônimo, projeto alheio, artefato trocado, fonte ausente e falha parcial não retornam dados indevidos ou status verde.
- [ ] Implementar projeções de dados persistidos, sem novo scheduler/memória. Síntese LLM opcional é marcada e mantém referências.
- [ ] Testar autenticação, CSRF/origin e WebSocket por comportamento, incluindo negação e tickets atuais. Outra origem exige revisão explícita antes de habilitar.

Verificação: tests/test_assistant_api.py e regressões Web existentes. Commit: feat(web): projeta tarefas e seguranca por projeto.

### E2.2 Entrada e conversa compartilhada

Files: kairos_web/ui/js/app.js e views; nova views/assistant.js; reutilizar chat-client.js, api.js e InteractionEnvelope/InteractionService; web/src/__tests__/assistant-flows.test.ts.

- [ ] Obter decisão visual antes de implementar a nova superfície. Rota sugerida /assistant pode ser ajustada ao roteamento existente.
- [ ] RED: abrir direto, entrar pelo link e voltar; texto aparece na mesma sessão/histórico e usa a política do núcleo.
- [ ] Implementar UI-01 e UI-02 sobre módulos servidos, sem framework novo ou outro loop de ferramentas.
- [ ] GREEN em Vitest e teste de transporte real; erro volta legível e não cria conversa divergente.

Commit: feat(web): adiciona experiencia propria do assistente.

### E2.3 Tarefas, aprovações e reconexão acessível

Files: views/assistant.js e utilitários existentes; projeções E2.1; web/src/__tests__/assistant-tasks.test.ts e testes Python de retomada.

- [ ] RED: fonte indisponível, estado inconclusivo, negação, cancelamento em curso e custo desconhecido são explícitos.
- [ ] Na fase mínima, exibir responsável, dependências, tempo, custo conhecido, evidências, resumo do dia e saúde/cobertura; estados de aprovação/cancelamento vêm da fonte. Controles mutáveis novos exigem a extensão de E2 e autorização própria; enquanto isso usar a superfície existente.
- [ ] Reconectar por estado persistido/cursor e chave de submissão. Não reenviar ação aceita após perda de resposta.
- [ ] Exercitar UI-03–UI-08: teclado completo, foco, estados visuais não dependentes de cor/áudio, retorno da página e erro sanitizado.
- [ ] Medir controle_local_ms e evento_painel_ms conforme E0.3, com pelo menos 30 controles por cenário e falhas explícitas; rotular como smoke, sem confundir teste DOM com benchmark no Mac.

Commit: feat(web): acompanha tarefas aprovacoes e retomada.

Gate E2 de leitura: dados reais, fontes/estados degradados, reconexão, acessibilidade e autorização verificadas; decisão visual antes de superfície nova. Gate da extensão: conversa/controles mutáveis, idempotência e revisão da experiência/evidência integrada. Nenhum dos gates de UI adia E4 quando suas dependências de backend estiverem atendidas.

## E4 Delegação mínima e execução controlada — Prioridade Alta

Objetivo central da fase mínima. Depende de E0/E1, dos contratos comuns e da nota de design TaskService revisada antes de código. Não depende de E3 nem da nova UI E2; usar transportes existentes. Priorizar E4 sobre voz.

### E4.1 Persistir tarefa, relação e dependência

Files propostos: kairos_integration/task_contract.py e task_service.py; kairos_state/task_schema.py e repositories/tasks.py; migrations.py. Referenciar RuntimeStore e AgentRuntimeService existentes.

- [ ] RED: um nível de delegação, sem ciclo, sem filho de filho, projeto consistente, chave idempotente e dependência inconclusiva.
- [ ] Implementar a nota TaskService aceita antes de código: projeção/FKs para fontes canônicas, sem fila própria ou terminalidade concorrente. Estender E1.5 quando pertinente; resultado referencia auditoria/runtime/evidências.
- [ ] Testar migração, backup/restauração e reinício. Pai não conclui antes de dependências/validações exigidas.

### E4.2 Autoridade e contexto mínimo

Files: autorização de E1.4a/E1.4b, composition.py, registro/despacho de ferramentas e perfis de execução existentes. Atualizar o threat model E0.4 para a fronteira pai/filho.

- [ ] RED: filho tenta ferramenta/destino negado, memória de outro projeto, credencial, instrução de administrador ou delegação recursiva.
- [ ] Aplicar interseção pai/perfil no serviço e executor, incluindo CLI, Web, cron e backend de runtime. Um novo nome de ferramenta não herda permissão por omissão.
- [ ] Entregar somente objetivo, contratos e referências necessárias; validar artefato e projeto antes de repassar o resultado como dado não confiável.
- [ ] Revisor é papel separado sob demanda; não alegar independência garantida só por trocar prompt.

### E4.3 Fila, orçamento, cancelamento e reinício

Files: TaskService e admission.py; adapters do runtime/worker e limites existentes.

- [ ] RED: duas admissões pesadas, prazo, orçamento, repetição, OOM, cancelamento durante aprovação, kill switch e crash durante efeito.
- [ ] Uma execução pesada global na admissão/fila persistida comum existente, estendida conforme a nota TaskService; TaskRecord não mantém outra fila. Controles/interação têm precedência sobre novas admissões em background. Não preemptar efeito externo só por latência.
- [ ] Declarar por backend limites impostos e uso observado. MAX_TOOL_CALLS atual não comprova limite interno do Codex; perfil que exige limite não disponível é recusado até haver enforcer verificável.
- [ ] Cancelar propaga aos filhos/runner, coleta confirmação de inatividade e preserva evidência. Efeito externo prévio continua informado; morte incerta é interrupted/reconciliada.
- [ ] Kill switch recusa novas admissões e cancela execuções controladas, com trilha; cancelamento não disputa a fila de trabalho.

### E4.4 Primeiro cenário completo

- [ ] Coordenador solicita auditoria de snapshot; executor entrega relatório estruturado; analista resume referências e limites. Código alterado fica como proposta revisável.
- [ ] Falha de coleta interrompe conclusão de segurança. Saída do filho que pede mais privilégio continua dado e é recusada.
- [ ] GREEN: AG-01–AG-09, isolamento de artefatos, retomada, orçamento e cancelamento reais.

Verificação: tests/test_task_lifecycle.py, test_task_authority.py, test_task_recovery.py e test_multiagent_audit_flow.py, mais regressões reais do runtime/worker. Commits por persistência, política, lifecycle e cenário. Gate E4: cenário ponta a ponta e tentativas de escalada recusadas, revisão independente e CI/imagem pertinentes.

## E3 Voz opcional/adiável — fora da fase mínima

Autorização própria; planejar após a prioridade alta de E4, ou em paralelo somente sem atrasá-la. Não é dependência de E4; extensão interativa de E2 e benchmark do destino continuam necessários.

### E3.1 Seleção de providers e benchmark

Files propostos: kairos_voice/contracts.py e benchmark de áudio; integração ao serviço de credenciais existente; decisão registrada em docs/decisoes.md.

Interfaces propostas: SpeechToText.transcribe(audio: AudioInput) -> Transcript e TextToSpeech.synthesize(text: str, options: VoiceOptions) -> AudioOutput; contratos não contêm chaves.

- [ ] Comparar candidatos autorizados no Mac Intel com conteúdo de teste consentido, registrando qualidade, tempo, RAM/swap, privacidade, custo e dependências.
- [ ] Escolher STT/TTS e local/nuvem com a usuária. Sem decisão, manter voz desativada.
- [ ] Pin/integridade/licença e extras opcionais somente após aprovação da dependência; atualizar uv.lock, empacotamento e Dockerfiles se houver novo pacote.

### E3.2 Canal de áudio com a mesma sessão

Files: adapters de kairos_voice, endpoint de áudio autenticado proposto e controle de captura em views/assistant.js.

- [ ] RED: falta de consentimento, arquivo inválido/excessivo, timeout e falha local não enviam áudio à nuvem.
- [ ] RED/GREEN para providers local e remoto: sem consentimento/configuração apropriada não ocorre captura, transmissão ou reprodução TTS. Configuração local não concede consentimento remoto; envio remoto exige aviso e escolha explícita. Não iniciar fala ao abrir/reconectar a interface sem a configuração/ação consentida.
- [ ] Pressionar para falar, transcrição editável antes de enviar, estado de gravação/transcrição/processamento/reprodução e fallback textual.
- [ ] Encaminhar transcrição aprovada pelo mesmo InteractionEnvelope/serviço e sessão. Carregar dependência pesada sob demanda.
- [ ] Limitar captura inicialmente a 60 s propostos; não reter áudio por padrão nem incluir áudio bruto em logs.

### E3.3 Interrupção, política e regressão de recursos

- [ ] RED: transcrição manda ignorar política ou imita aprovação; executor recusa a ação sensível sem confirmação autenticada específica.
- [ ] Parar TTS cancela reprodução/síntese controlada; cancelar tarefa usa o controle próprio e preserva seus efeitos reais.
- [ ] GREEN: VOZ-01–VOZ-07, igualdade de política texto/voz, consentimento local/remoto, exclusão/retenção configurada e erros.
- [ ] Repetir benchmark E0 com voz concorrendo com trabalho permitido; nenhum OOM/órfão ou troca de backend silenciosa.

Verificação: tests/test_voice_contracts.py, test_voice_transport.py, test_voice_policy.py e testes Vitest de captura/controle. Commits por contrato, canal e cancelamento. Gate E3: decisão local/nuvem, benchmark Mac e cadeia de autorização aprovados.

## Trilha S Testes ativos autorizados

Esta trilha fica fora da fase mínima e exige autorização própria, atualização/revisão do threat model E0.4 e controle especializado; não é requisito para usar o assistente. Perfis estático sem rede, advisories com egress aprovado, web/API autorizado e laboratório especializado permanecem distintos.

### S.1 Registro de autorização e perfil especializado

Files propostos: kairos_security/targets.py e active_runner.py; adapter separado da política do worker padrão.

- [ ] RED: falta/expiração de janela, host/IP/porta/método fora do escopo, destino descoberto pelo agente e pedido de privilégio.
- [ ] Registrar responsável, projeto, ambiente, destino, método, limites e expiração; validar no runner fora do LLM.
- [ ] Planejar imagem/ferramenta com versão, integridade, dependências, licença e controles observáveis. Sem socket, cofre, mounts amplos, modo privilegiado ou acesso ao plano de controle.
- [ ] Laboratório com capacidades adicionais é decisão posterior, sem pressupor sua execução no Mac.

### S.2 Enforcement de rede e entrega

- [ ] RED em laboratório local: DNS muda, IPv4/IPv6 especial, redirect fora do escopo e conexão nova para loopback/link-local/interno/metadata.
- [ ] Aplicar política no ponto efetivo de conexão e na rede. Validar cada resolução/redirect; exceção de laboratório exige alvo exato, não substring/hostname sozinho.
- [ ] Ferramenta que não respeita a barreira não é habilitada. Destrutivo, DoS e extração permanecem desativados.
- [ ] Artefato só sai após tamanho/tipo/path/redaction e vínculo de projeto/tarefa validados.

Verificação: tests/test_security_target_scope.py, test_active_runner_network.py e integração do perfil especializado com destinos locais autorizados. Gate S: rede e escopo demonstrados, autorização do alvo vigente e decisão específica antes de cada ativação.

## V0 Harness de avaliação antes de E5

Files propostos: evals/phase2/scenarios.json com entradas/referências sintéticas; aproveitar o harness de evals existente e seus comandos antes de criar adaptador específico. Se necessário, scripts/evaluate_phase2.py e tests/test_phase2_evaluation.py; relatório docs/superpowers/reports/<data>-fase2-evaluation-baseline.md. Preparação de avaliação não instala/promove candidatos de E5.

Manifesto versionado de 10–20 cenários fixos; proposta inicial de 12, com entradas, resultado esperado e verificadores comportamentais: auditoria benigna com cobertura; segredo sintético redigido; fonte/check ausente; injeção que pede ação negada; escape por links; filho que amplia autoridade; cancelamento pai/filho; reinício sem reenvio; limite/admissão pesada; reconexão sem ressubmissão; artefato/memória de outro projeto; chave idempotente repetida/conflitante. Casos futuros só são exercitados após autorização da capacidade; indisponível/não executado nunca conta como sucesso. O baseline pode conter falhas existentes demonstradas, separadas de erro do harness; elas são a referência para avaliar correções, sem serem reclassificadas como aprovação.

- [ ] Fixar manifesto, fixtures, comandos e rubricas antes de avaliar candidato. Não usar testes que leem implementação nem congelam catálogos atuais.
- [ ] Executar baseline no mesmo SHA/ambiente/configuração e backend declarado; registrar sucesso por cenário e validação, violações de autorização/isolamento, falhas/timeout, latência local/total separadas, tokens/custo conhecidos e lacunas. Métrica desconhecida não vira zero.
- [ ] Definir limiares por cenário antes da comparação: nenhuma violação de segurança/isolamento nem regressão funcional obrigatória; qualidade por verificador/rubrica; teto de custo/latência e tolerância declarados. Critérios inconclusivos bloqueiam promoção, sem ajuste depois de ver o candidato.
- [ ] Validar o próprio harness com candidato deliberadamente defeituoso e demonstrar que o verificador recusa. Comparar candidato e baseline com o mesmo manifesto/configuração; guardar amostras e ensaio de rollback.

Gate V0: conjunto fixado e baseline executado antes de iniciar E5. Antes de promover em E5, executar e aprovar todos os casos obrigatórios do perfil, sem inferir melhoria de resumo do modelo. Lacunas conhecidas no baseline não impedem preparar uma correção autorizada; impedem declarar sucesso/promover enquanto os critérios obrigatórios não forem cumpridos. A preparação/execução do harness integra a entrega de avaliação autorizada, não autoriza evolução/promoção.

## E5 Evolução, memória e promoção controladas — extensão

E5 fica fora da fase mínima. Depende de autorização própria, E4 e gate V0 com baseline; não iniciar candidatos para depois escolher casos/métricas.

### E5.1 Candidatos e avaliação

Files: kairos_skills/mutations.py, runtime.py e serviços existentes de autoria/publicação; novo serviço de avaliação que referencia o ledger, sem fluxo paralelo de instalação.

- [ ] RED: skill/config/patch de procedência desconhecida, dependência não aprovada e candidato que falha em qualidade, segurança, custo/latência ou reversão.
- [ ] Registrar observação, fonte, escopo, versão e baseline; avaliar isoladamente e guardar casos/resultados reproduzíveis.
- [ ] Instalar candidato significa verificar origem/versão/integridade/dependências e construir/testar artefato isolado; não instalar comando arbitrário no host.

### E5.2 Procedência e escopo na memória existente

Files: kairos_tools/memory.py, kairos_memory/__init__.py, carregamento de contexto e APIs de experiências existentes.

- [ ] RED: hipótese/saída de filho vira preferência confirmada, recuperação atravessa projeto ou conteúdo externo vira instrução de sistema.
- [ ] Versionar a representação canônica existente com classe, fonte/evidência, escopo, estado e política de retenção. Design de migração deve preservar dados e API, sem dois repositórios ativos.
- [ ] Aplicar escopo na busca/contexto, não somente ao gravar. Memória global contém só itens confirmados para esse alcance.
- [ ] Testar migração de entradas legadas, concorrência, erro de leitura, retenção sensível e rollback; feedback observado não é autoaprovação.

### E5.3 Promoção e rollback

- [ ] A0/A1 usam alvos e rotinas já autorizados; A2 prepara candidato em área isolada. Publicação/promoção exige ação autenticada concreta.
- [ ] RED: candidato inferior ou avaliação inconclusiva não promove; versão anterior continua ativa. Configuração e patches têm rollback específico, não só rollback de skill.
- [ ] Demonstrar comparação com baseline, revisão independente, promoção autorizada, acompanhamento e reversão em regressão.
- [ ] A3 desabilitado; segurança, credenciais e aumento de autoridade nunca entram em autopromoção.

Verificação: testes reais de skills, tests/test_experiences.py e test_memory_tool.py, mais test_evolution_candidates.py, test_memory_provenance.py e test_promotion_rollback.py. Gate E5: melhoria sustentada por casos, procedência e rollback demonstrados; nenhuma exigência de treinamento de pesos.

## E6 Piloto e operação para outras pessoas — extensão

Fora da fase mínima; autorização própria antes de implementar compartilhamento ou convidar pessoas.

### E6.1 Decisão de compartilhamento

- [ ] Escolher explicitamente instâncias realmente separadas ou identidade/autorização multiusuário. Não implementar as duas opções por antecipação.
- [ ] Registrar threat model, limites, recuperação, revogação e evidência necessária. Token atual compartilhado não atende nenhuma opção.
- [ ] Instâncias separadas precisam validar separação de estado, credenciais, memória, artefatos e acesso; apenas nomes diferentes de diretórios não provam fronteira.
- [ ] Opção multiusuário exige identidade por usuário, autorização por recurso/projeto, quotas e revogação no backend. Preservar autenticação existente; novos OAuth/integrações ficam fora desta etapa.

### E6.2 Aceite e operação do piloto

Files condicionais: composição/configuração por instância ou extensão dos serviços de identidade/estado existentes; testes de acesso cruzado e runbook.

- [ ] RED: acesso cruzado direto por API/WebSocket/artefato, token revogado, quota, sessão restaurada de outra instância e vazamento em resumo/memória.
- [ ] Demonstrar onboarding, isolamento escolhido, limites e recuperação/rollback. Acesso remoto exige decisão separada, TLS e revisão do perímetro; API local é padrão.
- [ ] Repetir cenários representativos de recursos e documentar comportamento com computador suspenso/desligado; não prometer disponibilidade contínua.

Gate E6: etapas estáveis, escolha da usuária, testes de isolamento sem acesso cruzado e runbook verificado antes de convidar pessoas.

## Matriz dos requisitos nomeados

| Requisitos da SPEC | Tarefa responsável | Prova exigida |
| --- | --- | --- |
| UI-01, UI-02 | E2.2, extensão autorizada | Entrada/retorno e mesma sessão/serviço |
| UI-03, UI-06 | E2.1, E2.3 | Fontes, período, atualização e segurança indisponível |
| UI-04, UI-05 | E2.3 leitura; extensão para controles; E1.4b | Estado real na mínima; ação concreta/negação pela superfície existente ou extensão autorizada |
| UI-07, UI-08 | E2.3 | Retomada sem reenvio; teclado, foco e estados visuais |
| VOZ-01, VOZ-02, VOZ-03 | E3.2 | Captura explícita, edição e erros para texto |
| VOZ-04, VOZ-05 | E3.3 | Parar fala distinto de tarefa; aprovação no backend |
| VOZ-06, VOZ-07 | E3.1–E3.3 | Consentimento/local-nuvem, retenção e limite de captura |
| AG-01, AG-05 | E4.1, E4.4 | Um nível, dependências e validações antes da conclusão |
| AG-02, AG-06, AG-07 | E4.3 | Uma pesada, cancelamento, orçamento, timeout e repetição |
| AG-03, AG-04, AG-08, AG-09 | E4.2 | Contexto mínimo, política única, dados não confiáveis e artefatos por projeto |

## Matriz dos cenários globais da seção 15

| Cenário | Responsável |
| --- | --- |
| Scanner sem diretório/detector | E1.1–E1.3: erro e cobertura incompleta |
| Segredo sintético em .env | H0: detectar/redigir; E1.2: manter o contrato no snapshot seguro |
| Auditoria concluída com vulnerabilidade | E1.1, E1.5, E1.7: finding/política, não crash |
| Relatório igual com timestamp diferente | E1.7: deduplicação e nenhuma chamada LLM desnecessária |
| Escape de workspace | E1.2, E1.4a/E1.4b: recusa no caminho real e evento |
| Página ou filho pede ação negada | E1.4b, E4.2: recusa determinística |
| Token/escopo expirou ou parâmetros mudaram | E1.4a/E1.4b, E4.2: nova autorização |
| DNS/redirect sai do alvo | S.2: bloquear antes de alcançar o destino |
| Docker sem controle exigido | E0.2, E1.3: unavailable, sem fallback ao host |
| Reinício durante tarefa | E1.5, E4.3: reconciliação sem duplicação |
| Interromper fala | E3.3: TTS interrompido, tarefa coerente |
| Cancelar tarefa | E4.3: filhos/processos encerrados e efeitos informados |
| LLM indisponível | E1.3, E1.7, E2.1: coleta continua, síntese indisponível |
| UI reconecta | E2.3: estado persistido, sem ressubmissão |
| Outro projeto/usuário | E2.1, E4.2, E5.2, E6.2: negar no backend |
| Skill falha na avaliação | E5.1, E5.3: nenhuma promoção, versão anterior preservada |

Revisão editorial desta versão: [relatório v0.2](../reports/2026-11-02-kairos-fase2-revisao-v0.2.md), com escopo e limites distintos do aceite técnico.

## Rastreabilidade das recomendações incorporadas

| Recomendação | Destino e gate |
| --- | --- |
| Hotfix de segredos antecipável | H0, separado de I1/Mac e sem ampliar autoridade |
| Dividir autoridade e exceções | E1.4a → E1.1 sem exceções → E1.2 → E1.4b |
| E4 antes de voz | Prioridade Alta no escopo, roadmap e seção E4; E3 adiável |
| Fase mínima | E0 + E1 + E2 leitura + E4; E3/E5/E6/S com autorizações próprias |
| Design TaskService antes de código | Contratos e gates E1.5/E4.1, FKs/fontes canônicas e nenhuma fila própria |
| Harness antes de E5 | V0, manifesto de 10–20 casos, baseline e métricas predefinidas |
| Threat model/classificação próprios | E0.4 antes de E1.4a; atualizar para E1.4b/E4/S |
| Estatística e métrica de E0.3 | Smoke de 30 controles por cenário; ACK/persistência/renderização definidos |
| Consumidores de passed/security_score | Inventário e compatibilidade/exit code antes de E1.1 |
| Saída do gate Mac | E0.2: evidência Linux limitada, unavailable, timebox e decisão explícita |
| Filesystem, aprovações, cobertura, limites, retenção | Contratos, E0.2, E1.2/E1.4b/E1.6/E4.3 |
| Revisão editorial versus aceite técnico | Seção de verificação e relatório histórico anotado |

## Verificação por entrega e rollback

Revisão de rastreabilidade/clareza dos documentos é editorial, não assurance independente do produto. O relatório histórico da v0.1 não atesta esta revisão nem substitui revisão de código, testes executados e aceite técnico de cada entrega.

Cada tarefa de código segue RED → implementação mínima → GREEN, testes integrados pertinentes, documentação e commit em português. Reutilizar evidência válida por SHA/configuração/ambiente; reexecutar os checks afetados. Revisão independente nos contratos de autoridade, migrações, rede, isolamento e fechamento de cada entrega.

Usar scripts/ci.sh integral no ambiente autorizado, com Python 3.11, binário Codex fixado verificado e diretório novo de cobertura. Docker real e opt-ins necessários são gates da superfície; os ausentes constam como não executados. Conferir CI remoto por SHA antes de merge. Sucesso de upload ao Sonar não substitui Quality Gate da revisão analisada.

Rollback: backup e restauração testada para migrações; feature desabilitada e versão anterior para UI/voz; recusa de novas admissões e reconciliação para tarefas; versão aprovada anterior para skills/configurações. Não fazer downgrade destrutivo sobre banco novo nem prometer desfazer efeitos externos. Preservar relatório e ação de rollback.

Uma entrega só termina com efeito real, política no backend, erros explícitos, proteção de dados, testes relevantes executados, documentação e recuperação demonstrada. CI verde não é certificado de segurança.

## Decisões e primeira autorização

As seis decisões da seção 17 permanecem nos gates E0, E3, S, E2, E6 e recorrência paga. Este plano não escolhe silenciosamente plataforma, provider, alvo, visual, compartilhamento ou orçamento.

### Primeiro incremento I1 reordenado

Escopo para autorização de implementação: **E0 → E1.4a → E1.1 sem exceções → E1.2 → E1.4b**. E0.1–E0.4 entregam inventário por SHA, capacidades/benchmark do destino e threat model; gate E0 precisa ser aceito antes de E1.4a. E1.4a fornece Principal autenticado, alvo/revisão e leitura autorizada. E1.1 entrega contrato/política/cobertura com exceções desabilitadas. E1.2 integra captura segura e reaproveita H0. E1.4b acrescenta exceções autenticadas e migração compatível das aprovações de ações. A indisponibilidade de E1.4b não impede entregar E1.1/E1.2 com exceções fechadas.

H0 tem entrega/autorização limitada própria, pode anteceder E0/I1 e não aguarda Mac. Sua conclusão não aprova E1.1, a captura de E1.2, a autoridade de E1.4 nem execução/background no destino.

Entregáveis I1: baseline; matriz/timebox e medições do Mac; threat model/classificação; fronteira Principal/AuthorizedTarget; inventário dos consumidores e contrato do auditor sem exceções; snapshot/redaction integrados ao H0; fluxo autenticado de exceções/aprovações de E1.4b; regressões, revisão independente, CI e rollback pertinentes. Entregas intermediárias podem ser revisadas com as exceções desabilitadas. I1 não conclui E1: runtime_config/advisories, persistência, eventos e cron seguem E1.3 e E1.5–E1.7.

Pontos de parada: Mac sem evidência mantém E0 pendente; cumprir o timebox com matriz/lacunas e solicitar decisão concreta, sem aprovar por Linux ou adotar backend alternativo sozinho. Sem Principal/alvo/leitura autorizados, E1.1/E1.2 não iniciam. Sem aprovação autenticada de exceção, alto/crítico permanece fail e E1.4b não habilita exceções. No fechamento de cada entrega, apresentar evidências para revisão antes de habilitação/publicação.

I1 não inclui agenda habilitada, nova UI/conversa visual, delegação, voz, E5, Trilha S ou piloto. E4 continua em Prioridade Alta no mapa da fase mínima e recebe entrega/autorização própria após as dependências. Aprovar a direção, editar documentos ou aprovar I1 não implementa E0–E6 em bloco.
