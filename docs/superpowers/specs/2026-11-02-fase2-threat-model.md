# E0.4 — modelo de ameaças e classificação de dados

Revisão de arquitetura: produto `c6a083f51e12aaddeac8720185ecc55a27d010de`, 02/11/2026. Fontes: [SPEC v0.2](2026-11-02-kairos-evolucao-v0.2.md), [plano mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md), [baseline E0.1](../reports/2026-11-02-fase2-baseline.md) e [matriz de plataforma](../reports/2026-11-02-fase2-plataforma.md).

**Estado: revisão independente documental concluída; aceite da usuária pendente; eficácia não certificada.** A inspeção e os testes existentes sustentam os controles delimitados na coluna atual. Controles exigidos/propostos não são anunciados como implementação. E0.2/E0.3 permanecem pendentes; este documento não habilita E1.4a, exceções, delegação ou testes ativos.

## Intenção, escopo e invariantes

Proteger credenciais, dados de projeto e autoridade enquanto o Kairos evolui para auditoria e delegação. Reusar InteractionService, AgentRuntimeService/RuntimeStore, filesystem, cofre e ciclo de skills existentes. Resultado convincente do modelo não é autorização nem evidência de execução.

Invariantes para o design de I1/E4: uma fonte canônica de cada estado; Principal derivado no backend; alvo/revisão/permissão revalidados antes da leitura/efeito; privilégio do filho limitado ao do pai; resultado incompleto visível; segredo não sai por evidência; cancelamento continua acessível e só conclui após inatividade comprovada. Esses invariantes guiam as próximas entregas, sem alterar o comportamento do produto nesta revisão.

Fora do aceite deste documento: resistência a administrador do host/daemon comprometido, multiusuário pronto, pentest, STT/TTS, autopromoção, alvo ativo arbitrário, prevenção infalível de prompt injection e certificação de toda a cobertura.

## Ativos e limites de confiança

Topologia confirmada pela usuária em 04/11/2026: navegador no Mac como cliente; núcleo, estado, credenciais e workers no servidor Linux existente. A fronteira cliente/API atravessa a rede: validar autenticação, origem, transporte protegido/TLS, reconexão e revogação no acesso efetivo. A decisão não autoriza abrir portas ou expor serviços adicionais. AppArmor e peer credentials aplicam-se ao executor Linux, não ao navegador; portabilidade nativa e Docker Desktop no Mac ficam fora do uso inicial.

| Ativo | Proteção/representação atual | Fronteira e lacuna |
| --- | --- | --- |
| Credenciais, tokens, passphrase, referências | [CredentialService](../../../kairos_security/credentials/service.py), cofre/keyring e [CredentialSecret](../../../kairos_security/credentials/contracts.py) | O adaptador autorizado resolve o segredo; metadado/mascaramento não tornam o valor público. Backend/host continuam confiáveis. |
| Estado e journals | SQLite de sessões/runtime/skills, journal operacional privado | Persistência e ACL de operador não equivalem a segregação multiusuário/projeto. Texto permitido ainda pode conter dados sensíveis. |
| Código, snapshots e árvore de projeto | [captura por descritor](../../../kairos_filesystem/tree.py), identidade/inventário e limites | Origem no executor pode mudar durante leitura. Cliente web não concede acesso ao filesystem local do Mac; transferência/captura exigem contrato próprio. Snapshot de projeto não autoriza lê-lo. |
| Políticas e aprovações | Workspace, allowlists, identidade de diretório, aprovações do chat/runtime | Vínculo comum com Principal/ação/digest/expiração/revogação ainda é E1.4a/b. |
| Artefatos e relatórios | Findings/JSON, documentos, backups e versões de skills | Artefato pode conter código/dado privado; falta contrato comum de referência, ACL, tamanho e retenção por tarefa/projeto. |
| Memória e skills | Experiências candidatas/ativas, memória curada, proveniência e publicação | Conteúdo não ganha autoridade por ser recuperado; escopo na recuperação/procedência ampliada ainda é E5. |

Fluxo conceitual; linhas tracejadas são contratos futuros, não componentes implantados:

```mermaid
flowchart LR
    O[Operadora] --> UI[Navegador no Mac / outros clientes]
    UI --> A[Autenticação e transportes no servidor Linux]
    A --> C[InteractionService / núcleo]
    C --> P[Política atual / workspace]
    P --> F[Ferramentas e filesystem]
    C --> R[AgentRuntimeService]
    R --> S[RuntimeStore: turno / lease / journal]
    R --> W[Worker Docker atestado]
    C --> V[Serviço de credenciais]
    V --> AD[Adaptador autorizado]
    C --> AD
    AD --> L[Provider externo]
    C --> M[Memória / skills]
    C --> J[Eventos / relatórios]
    X[Página / arquivo / resultado não confiável] --> C
    C -. E1.4a .-> G[Principal / AuthorizedTarget / grant]
    C -. E4 .-> D[Tarefa pai / filho com autoridade limitada]
```

Fronteiras: cliente/API; núcleo/conteúdo externo; núcleo/LLM e ferramentas; processo/estado; filesystem/worker; rede/egress; futuro pai/filho. O LLM e seus resultados ficam do lado de dados não confiáveis, inclusive quando a resposta tem formato de comando ou decisão.

## Atacantes e premissas

- Cliente sem autenticação ou com sessão de outro recurso; tenta consultar/alterar artefatos e aprovações.
- Página, arquivo, ferramenta MCP, resposta de provider ou futuro filho malicioso; tenta converter dados em instrução, solicitar segredo ou ampliar escopo.
- Escritor concorrente do projeto; substitui links, diretórios, conteúdo, mounts ou política entre observação e efeito.
- Dependência/plugin comprometido; executa código com os privilégios que recebeu. Pin/integridade não provam que seu conteúdo é benigno.
- Processo do mesmo UID, administrador e daemon Docker: estão fora da promessa de isolamento forte entre usuários. Permissões privadas, descritores e locks reduzem riscos específicos, mas não defendem contra controle administrativo do host. O acesso ao daemon é autoridade de host e não deve chegar ao worker.

Premissas a validar no destino: integridade do host/daemon, atestações realmente observáveis, usuário e filesystem compatíveis, estado com privilégios mínimos e ausência de fallback. Quando uma premissa falha, a capacidade dependente precisa ser marcada indisponível; não se transforma isso em aprovação textual.

## Classificação e destinos

A tabela define requisitos para o design seguinte. Não descreve um classificador global já implantado. Conflitos usam a classe mais restritiva; conteúdo externo não é declarado público só porque veio de uma ferramenta.

| Classe | Exemplos | Destinos permitidos e ACL exigida | Sanitização e retenção |
| --- | --- | --- | --- |
| Público | Documentação publicada, código explicitamente público, métricas agregadas sem identidade | Superfície autorizada e finalidade declarada; origem/fonte preservadas | Remover segredos acidentais e identificadores desnecessários; retenção conforme finalidade. |
| Interno por projeto | Código privado, prompts, resultados, snapshots, nomes/caminhos, experiências | Apenas operador e recursos do projeto; envio ao provider somente pelo caminho autorizado para aquela tarefa | Reduzir escopo/tamanho e associar projeto/revisão; evitar conteúdo bruto em logs. |
| Pessoal/sensível | Conversas, incidentes, identificadores e conteúdo de terceiros | Acesso mínimo, exportação explícita e artefato restrito quando necessário; não compartilhar por conveniência | Minimização, mascaramento/classificação e trilha de acesso; prazo e teto definidos antes da coleta. |
| Segredo/credencial | Tokens, chaves, cookies, passphrase e material de recuperação | Somente serviço local protegido e componente autorizado que precisa autenticar; nunca prompt de modelo, UI, evidência ou memória como valor bruto | Usar referência ou sentinela; não tratar os 30 dias propostos como licença de retenção de segredo. |

Retenção de 30 dias, teto de armazenamento, alerta e preservação de incidente são propostas da SPEC, não garantia transversal atual. Hoje o journal operacional gira por tamanho e há contratos próprios de histórico/backup. E1.7 deve definir retenção efetiva, acesso, falhas de gravação e descarte sem perder holds; E5 revisa memória. Até lá, não declarar uma política global aplicada.

## Matriz de ameaças, controles e testes

Responsável indica o componente/entrega que deve responder pelo contrato, não uma atribuição já acordada a uma pessoa. Evidências listadas são testes existentes exercitados no CI Linux da base; lacunas exigem novos testes e revisão, não checkboxes presumidos.

| ID / ameaça | Controle atual e evidência delimitada | Lacuna / controle exigido | Responsável / gate |
| --- | --- | --- | --- |
| T01 Cliente não autenticado ou acesso cruzado | Token de sessão, cookies/tickets e [SessionTokenTests](../../../tests/test_web.py); peer por UID no socket runtime Linux onde há SO_PEERCRED | Principal do backend e ACL por recurso/projeto, inclusive relatório e aprovação; validar transporte/origem/sessão remota. Sem SO_PEERCRED a checagem atual retorna sem verificar UID: lacuna para eventual portabilidade, não mecanismo de autenticação do navegador Mac. | E0.2 + E1.4a; E6 para multiusuário. |
| T02 Prompt/resultado vira permissão | [workspace](../../../kairos_tools/workspace.py), [policy](../../../kairos_tools/policy.py) e [test_workspace_tools](../../../tests/test_workspace_tools.py) recusam handlers/escapes no escopo restrito | Autoridade não pode vir de JSON/texto do modelo; revalidar ação, destino, parâmetros e política imediatamente antes do efeito. | E1.4a/b; atualizar antes de E4. |
| T03 Filho amplia autoridade, cria recursão ou outro executor | Listas de política e wire reconhecem delegação; isso não demonstra AG-01–AG-09 | Interseção de grants, um nível inicial, admissão canônica, limites e cancelamento durável; TaskRecord somente projeção, sem fila/terminalidade concorrente. | Nota TaskService + E4; não habilitado por esta revisão. |
| T04 TOCTOU, symlink/hardlink, mount ou filesystem divergente | [TreeIOTests](../../../tests/test_tree_io.py), [fronteiras](../../../tests/test_security_file_boundaries.py), validação por descritor/identidade e testes de imagem | AuthorizedTarget antes de captura; comprovar capacidades no backend do destino e recusar ausência. Não substituir por normalização de path. | E0.2 + E1.2/1.4a. |
| T05 Scanner exfiltra a própria linha | H0 usa sentinela nos três produtores; [regressão integrada](../../../tests/test_security_redaction.py) exige achados reais e ausência de segredo/contexto em JSON/CLI/dataclass/repr | Não cobre nomes de arquivos/targets, Findings arbitrários ou saída bruta de detector futuro; integrar captura e fronteira de divulgação. | E1.2; atualizar para cada produtor novo. |
| T06 Ausência de coleta vira aprovação | [SecurityReport](../../../kairos_security/models.py) e testes atuais demonstram regra por severidade, não completude | Separar execution_state, CheckResult obrigatório e policy_outcome; coleta vazia/incompleta não passa; alto/crítico continua fail; exceções fechadas inicialmente. | E1.1 após E1.4a. |
| T07 Aprovação reutilizada após mudar ação/alvo | [runtime approvals](../../../tests/test_runtime_approvals.py) são persistidas; chat genérico usa futures vinculadas à conversa | Digest, expiração, revisão/revogação e recurso precisam do vínculo comum. Exceções exigem aprovação autenticada própria; booleano do modelo não serve. | E1.4a/b; sem exceção real em E1.1. |
| T08 Reinício duplica efeito ou cancela cedo | RuntimeStore, leases/fencing/reconciliação e [recuperação](../../../tests/test_runtime_recovery.py); [contratos de cancelamento](../../../tests/test_cancellation_contracts.py) | Manter uma fonte canônica; AuditRun sem turno artificial; idempotência por operador/projeto/digest; efeito incerto não é reenviado automaticamente. | Design TaskService + E1/E4. |
| T09 Worker escapa ou conecta a destino arbitrário | [WorkerPolicy](../../../kairos_runtime/experimental/worker_policy.py) atesta rede none, mounts vazios, imagem/UID/caps/recursos; [testes offline reais](../../../tests/test_external_sandbox_integration.py) | Atestar cada worker e perfil no destino. Perfil ativo exige egress/DNS/redirect controlados fora do worker e autorização exata. | E0.2; Trilha S posterior. |
| T10 Memória/CPU/PIDs/disco/tempo esgotam | Worker limita recursos; [ExecutionLimits](../../../kairos_runtime/execution.py) e [test_execution](../../../tests/test_execution.py) validam timeout/bytes | MAX_TOOL_CALLS é rastreabilidade, não enforcer das chamadas internas; tokens/custo desconhecidos não são zero. Perfil com teto obrigatório sem mecanismo verificável fica indisponível. | E0.3 + admissão E1/E4. |
| T11 Segredo/texto privado entra em journal, LLM ou UI | H0 redige linhas; [public errors/allowlists](../../../kairos_runtime/redaction.py), [runtime security](../../../tests/test_runtime_security.py) e [journal](../../../tests/test_service_events.py) limitam formas/campos | Allowlists preservam texto/JSON permitido, não são redaction universal. Classificar antes de persistir/exportar; falha de evidência obrigatória bloqueia novo efeito dependente, preservando negação/cancelamento. | E1.2/1.7; interfaces de I1. |
| T12 Artefato substituído, exposto ou retido além do permitido | Capturas/skills/backup têm identidade e verificações próprias | Referência por tarefa/projeto, tamanho/tipo/path, ACL, retenção/teto e hold. Relatório bruto não vai ao cliente/LLM só por existir. | E1.2/1.7, atualizar antes de E4/S. |
| T13 Memória ou skill promove conteúdo malicioso | [learn](../../../tests/test_learn_interaction.py), [publicação](../../../tests/test_skill_publication_service.py) e [proteção](../../../tests/test_skill_org_policy.py) exercitam confirmação/proveniência | ExperienceStore.find ainda não recebe filtro de projeto; recuperar com escopo, origem e classe. Candidato não ganha autoridade; A3 permanece desabilitado. | E5, com harness/baseline próprios. |
| T14 Dependência/plugin/artefato comprometido | Lock, Actions por SHA, pin+checksum Codex e builds/testes no [CI da base](https://github.com/Patricia7sp/kairos/actions/runs/41539207915) | Integridade não prova ausência de vulnerabilidade; análise de advisories/perfil, capacidades mínimas e promoção revisada continuam necessárias. Não instalar automaticamente em produção. | E1.3 e entrega que propuser dependência; E5 para promoção. |

## Pontos de integração a revisar antes de código

E1.4a deve derivar identidade nas superfícies já autenticadas, vincular projeto/alvo/revisão/permissão e recusar mudanças antes de leitura. E1.1 recebe apenas essa referência, mantém exceções desabilitadas e não usa score como certificado. E1.2 conecta a captura segura aos detectores, preservando o H0.

Antes de TaskService/TaskRecord/migrações, a nota própria fixa referências por FK, composição de estados/resultados e adaptação da admissão; RuntimeStore continua canônico para turnos e AuditRun para coleta. Aprovações existentes precisam de migração compatível, não de troca por um booleano ou nova fila.

## Aceite e limites da revisão

Revisão independente deste artefato deve conferir: distinção atual/proposto; cobertura de ativos/fronteiras; ameaças com teste/responsável/lacuna; dados e destinos; separação servidor Linux/cliente Mac sem presumir controles; preservação dos mecanismos existentes. Revisar novamente antes de E1.4b, E4 e Trilha S.

Revisão documental executada em 02/11/2026 por GPT-6 Luna/high, com contexto isolado, somente leitura e conferência das afirmações relevantes no código: nenhum achado material ou pergunta nova para este escopo. Não executou teste de eficácia nem concedeu aceite E0. O registro está no [baseline](../reports/2026-11-02-fase2-baseline.md).

Os testes existentes cobrem contratos específicos com fixtures sintéticas e integrações Linux; não constituem pentest, teste adversarial de toda a cadeia ou certificação do host. Cobertura de subprocessos contém fragmentos inválidos registrados no baseline. Nenhum dado real sensível foi coletado para esta documentação.

Gate E0 continua pendente das capacidades e medições do destino, da revisão deste design e do aceite dos limites. A próxima fronteira de implementação continua **E1.4a → E1.1 sem exceções → E1.2 → E1.4b**, depois de E0 aceito. E4 tem Prioridade Alta após suas dependências; voz não o bloqueia.
