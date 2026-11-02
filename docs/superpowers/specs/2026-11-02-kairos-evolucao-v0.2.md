# Kairos — especificação evolutiva

Versão 0.2 • 2 de novembro de 2026 • Recomendações incorporadas; implementação por entrega autorizada.

A [v0.1 original](2026-10-25-kairos-evolucao-v0.1.txt) permanece preservada. Esta revisão é a referência normativa do [plano mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md). A usuária autorizou incorporar as recomendações documentais e definiu **E4 — Prioridade Alta**. Esta rodada não executa o hotfix nem as etapas E0–E6.

## Escopo e prioridade da Fase 2 mínima

O compromisso de escopo é **E0 + E1 + E2 de leitura + E4**. E4 entrega o objetivo central de delegação e tem **Prioridade Alta**, à frente da voz. E2 de leitura inclui projeções autenticadas, estados vazios/degradados, links de evidência e reconexão; nova conversa visual, aprovações e cancelamento pelo painel pertencem a uma extensão de E2. As superfícies existentes de aprovação/cancelamento continuam atendendo E1/E4.

E3 (voz), E5 (evolução/promoção), E6 (piloto) e Trilha S permanecem rastreados como extensões, cada uma com autorização própria. O harness de avaliação prepara a linha de base antes de E5 e não habilita promoção. Aprovar a direção ou esta revisão não autoriza implementar todo o mapa.


## 1. Decisão central

Evoluir o Kairos como plataforma principal de agentes. A experiência inspirada em JARVIS será uma interface de interação do Kairos, com voz opcional, dashboard e acompanhamento de tarefas. Não será um segundo cérebro, outro repositório de memória ou um backend concorrente.

O projeto já contém uma base operacional relevante: núcleo compartilhado, runtime, sessões isoladas, aprovações, credenciais, agendamentos, experiências e publicação de skills. Esta proposta acrescenta capacidades sobre essa base e corrige limitações identificadas na auditoria. Não pressupõe superioridade geral sobre todos os projetos externos.

### Origem das decisões


- Requisito da usuária: uso pessoal primeiro; produtividade; interface apartada; voz; multiagentes; testes de segurança autorizados; evolução progressiva; futura experimentação por poucas pessoas.
- Restrição informada: Mac Intel com 16 GB de RAM. Não assumir Apple Silicon, GPU dedicada ou servidor Linux disponível.
- Proposta técnica deste documento: organização modular, contratos, sequência de entregas, limites iniciais, formatos de dados e critérios de aceite. São escolhas revisáveis, não decisões anteriormente aprovadas pela usuária.
- A autorização desta revisão cobre os documentos. Hotfix, I1, habilitação de perfis, instalação, agendas, publicação e GitHub seguem autorizações de suas entregas; a autorização da #129 foi específica daquele PR e deploy.

## 2. Base de evidência e limites

Base original: Patricia7sp/kairos, commit 8902010, em 25/10/2026. A preparação documental consultou 4fe697b; a main foi depois atualizada para 23b4442603bec50481f9ca623436dfe76e3c2f8b pelo squash da #130, cujo delta é apenas teste/plano. A implementação deve registrar e revalidar o SHA, o diff e o ambiente escolhidos. Resultados antigos não são evidência de capacidades futuras.

A revisão anterior inspecionou código e o CI da revisão. O job unitário informou 4.102 testes aprovados, com exclusões e skips; isso não comprova ausência de vulnerabilidades. O ambiente local da revisão não reproduziu a suíte completa: faltavam dependências e Python 3.11. Foram realizadas verificações pontuais seguras, sem executar ataques ou acessar um ambiente de produção.

Revisão de arquitetura não equivale a pentest, certificação, análise exaustiva ou confirmação do estado atualmente implantado. Sucesso do upload ao Sonar não comprova aprovação do Quality Gate.

### Inventário: reaproveitar antes de acrescentar


| Área | Evidência na base | Decisão |
| --- | --- | --- |
| Interação CLI/Web | kairos_integration e transportes Web compartilham o serviço | Reutilizar; não criar outro loop de ferramentas |
| Runtime e sessões | kairos_runtime, workers Docker, recuperação e entrega | Estender contratos; preservar isolamento e revisão |
| Aprovações e arquivos | Política de ferramentas, aprovação e workspace restrito | Reutilizar e testar através dos caminhos reais |
| Credenciais | Keyring/cofre e endpoints de configuração | Usar serviço existente; nenhum segredo no frontend |
| Auditor de segurança | kairos_security e kairos security | Corrigir cobertura e interpretação; não substituir por outro scanner caseiro |
| Agenda e monitores | kairos_cron, persistência e integração no Web | Adicionar integração tipada de auditoria; não criar outro scheduler |
| Eventos operacionais | kairos_observability e APIs existentes | Acrescentar eventos de segurança e correlação |
| Memória e experiências | Memória persistente, experiências, estados e feedback | Reusar e ampliar procedência, escopo e curadoria |
| Skills | Autoria, validação, publicação, lineage e rollback | Reusar o ciclo, sem instalação irrestrita em produção |
| Web | Servidor FastAPI, páginas, TypeScript e testes | Aproveitar APIs e utilitários; nova superfície visual |
| Multiagentes | Nomes de políticas/hooks não demonstram delegação nativa completa | Construir orquestração explícita sobre o núcleo |
| Voz | Pipeline integrado STT/TTS não localizado na revisão | Capacidade nova, opcional e desacoplada |
| Intrusão | Monitores genéricos não equivalem a detector de ataques | Criar detecção baseada em eventos e regras; declarar lacunas |
| Compartilhamento | Autenticação atual orientada ao operador | Não tratar como isolamento multiusuário pronto |


## 3. Objetivos, não objetivos e sinais de fracasso


### Objetivos


1. Conversar com um único assistente que consulta o estado real do Kairos.
2. Ver tarefas, resultados, aprovações pendentes, alertas e resumo do dia em uma interface própria.
3. Usar voz sem torná-la obrigatória e sem reduzir controles de autorização.
4. Delegar tarefas com escopos, limites e rastreabilidade, começando com poucos agentes.
5. Observar riscos, detectar eventos suspeitos e propor melhorias verificáveis.
6. Ampliar skills e memória mediante evidência, testes, promoção controlada e rollback.
7. Funcionar de forma responsiva na máquina informada antes de ampliar a infraestrutura.

### Não objetivos iniciais


- Recriar Hermes, adotar integralmente OpenJarvis ou migrar o núcleo para LangGraph.
- Transformar Kairos em um sistema operacional ou em uma distribuição Kali.
- Treinar modelos localmente ou prometer aprendizado ilimitado e espontâneo.
- Rodar dezenas de agentes ou um LLM grande local continuamente.
- Executar testes ativos em destinos arbitrários ou instalar qualquer ferramenta a pedido do modelo.
- Oferecer SaaS multiusuário, defesa infalível ou correção automática irrestrita.
- Substituir monitoramento do sistema operacional por análise de conversa.
Fracasso seria duplicar memória/execução, exibir sucesso sem evidência, esconder falhas de cobertura, tornar a máquina inutilizável ou permitir que um agente amplie sua própria autoridade.

## 4. Abordagem arquitetural


### Alternativas consideradas


| Alternativa | Benefício | Custo/limite | Encaminhamento |
| --- | --- | --- | --- |
| Evolução modular do Kairos com interface separada e backend compartilhado | Aproveita controles e reduz operação no Mac | Exige contratos claros dentro do projeto | Recomendada nesta proposta |
| Serviço independente de assistente com backend próprio | Deploy e evolução independentes | Duplica identidade, memória, aprovações e observabilidade | Não indicado para a primeira fase |
| Migração para framework/plataforma externa | Componentes prontos em algumas áreas | Revalidação de mecanismos que já funcionam e possível aumento de footprint | Não justificada pela revisão |


A separação desejada é de experiência e acesso, não de autoridade: a interface pode abrir em outra rota ou aba, mas solicita trabalho ao Kairos. Proposta inicial: rota própria, por exemplo /assistant, no mesmo origin. O nome e a rota finais ficam com a implementação. Se futuramente houver outro origin, isso exige revisão explícita de cookies, CSRF, CORS, WebSocket e política de acesso; não habilitar wildcard para facilitar a integração.

### Responsabilidades das camadas


| Camada | Responsabilidade | Não pode fazer |
| --- | --- | --- |
| Interface do assistente | Conversa, captura/reprodução de áudio, visão consolidada, aprovações | Guardar chaves de providers ou decidir permissões |
| API e autenticação | Validar sessão, formatos, limites e associação de recursos | Confiar em identidade enviada pelo modelo |
| Núcleo/orquestração | Planejar, delegar, acompanhar dependências e consolidar resultados | Contornar política por instrução textual |
| Política e execução | Autorizar cada ação, emitir escopo limitado e executar no backend adequado | Ampliar escopo a pedido de um subagente |
| Estado, memória e evidência | Persistir tarefas, experiências, artefatos e eventos | Promover conteúdo externo a instrução de sistema |


Não há necessidade inicial de message broker, Kubernetes, banco vetorial ou barramento novo. Aproveitar armazenamento e eventos existentes; escolher mecanismo adicional somente se um teste demonstrar necessidade.

## 5. Contratos comuns

Os nomes abaixo são conceituais. Adaptar aos tipos existentes em vez de criar objetos paralelos. APIs e schemas precisam de versão e migração compatível.

### Tarefa e resultado


- Identidade: task_id, tarefa pai quando houver, sessão e escopo do projeto.
- Execução: agente/perfil responsável, dependências, backend e referência de autorização.
- Limites: prazo, passos, tokens/custo quando conhecidos, ferramentas e destinos permitidos.
- Estado persistente: queued, running, awaiting_approval, succeeded, failed, cancelled, timed_out ou interrupted.
- Resultado: resumo, referências de artefatos, validações executadas, erros e limitações. Texto convincente do modelo não determina sucesso.
- Reinício: tarefa não comprovadamente finalizada fica interrompida ou reconciliada; nunca presumir sucesso. Reexecutar efeitos externos exige checagem específica.
Antes de código em TaskService, TaskRecord ou suas migrações, aprovar uma nota de design própria. RuntimeStore é a fonte única dos estados de turno, leases, fencing, send_state e reconciliação; AuditRun é a fonte do ciclo de coleta sem turno artificial. TaskRecord é projeção com FK para runtime_turn_id e/ou audit_run_id, vínculo de projeto e regra de composição explícita. Não mantém fila própria, não decide terminalidade paralela e não dispara/reenvia trabalho. Admissão usa o mecanismo comum existente; a nota descreve a extensão necessária para auditorias. Cancelamento reconcilia o estado canônico e só conclui após confirmação de inatividade.

Usar chave de idempotência em submissões e efeitos que suportam esse contrato. Não prometer exactly-once para serviços externos que não o oferecem. Reconexão da interface não deve submeter novamente uma ação já aceita.

### Autorização de ação

A autorização vincula operador, projeto, recurso/destino, ação, parâmetros relevantes, prazo e política. Alterar esses dados invalida a autorização. Uma delegação recebe a interseção entre o permitido ao pai e o perfil do filho; nunca a união dos privilégios.

E1.4a fornece somente a fronteira necessária à primeira auditoria: Principal derivado do backend autenticado e AuthorizedTarget com projeto, identidade canônica/revisão do alvo e permissão de leitura validada antes da captura. E1.1 usa essa fronteira com exceções desabilitadas; não depende do fluxo de aprovação de AuditException. E1.4b acrescenta aprovação/revogação autenticada de exceções e adapta as aprovações de ações existentes.

Aprovações efêmeras do chat precisam de migração compatível para o vínculo persistido com ação/parâmetros, política, prazo e escopo. Aprovação humana deve usar a superfície autenticada, mostrar a ação concreta e distinguir executar, publicar, instalar, alterar credenciais e testar segurança. Voz transcrita, mensagens de terceiros e conteúdo de páginas não são aprovação de alto impacto.

### Evento e evidência

Eventos devem correlacionar event_id, horário, sessão, tarefa, tarefa pai, componente, ação, alvo sanitizado, decisão da política, resultado e revisão/configuração aplicável. Registrar justificativa resumida e chamadas de ferramentas, não raciocínio privado do modelo.

Segredos são removidos antes de persistir, enviar ao LLM ou mostrar na interface. Evidência bruta sensível, se indispensável, fica em armazenamento restrito separado, com retenção e acesso explícitos. Acesso a relatórios também é uma ação autorizada.

## 6. Experiência do assistente e dashboard


### Requisitos funcionais


- UI-01 — Entrada própria: link na interface atual, abertura direta e retorno ao Kairos. Não duplicar páginas de administração sem necessidade.
- UI-02 — Conversa compartilhada: texto e voz usam a mesma sessão e o mesmo serviço de interação; histórico não diverge entre canais.
- UI-03 — Resumo do dia: tarefas concluídas, bloqueios, próximos agendamentos e riscos recentes com links para suas fontes. Mostrar período, horário da atualização e dados indisponíveis.
- UI-04 — Centro de tarefas: estado, agente responsável, dependências, tempo, custo conhecido, evidências e controles de cancelamento.
- UI-05 — Aprovações: fila de ações concretas; negar não pode ser reinterpretado como permissão para tentar por outro caminho.
- UI-06 — Saúde e segurança: última auditoria, cobertura, falhas de coleta, achados novos/recorrentes e alertas. Ausência de dados não é status verde.
- UI-07 — Atualização: consumir eventos dos transportes atuais ou extensões compatíveis; reconexão recupera estado persistido.
- UI-08 — Acessibilidade: uso completo por teclado, indicação visual de gravação e estados que não dependem apenas de cor ou áudio.
O dashboard começa como projeção de dados existentes. Resumos por LLM são opcionais, identificados como síntese e vinculados às fontes. Dados de um projeto não aparecem em outro por conveniência do resumo.

O estilo visual será definido em etapa própria. Esta spec não aprova um HUD, framework frontend novo ou protótipo visual. Proposta: reutilizar TypeScript e contratos atuais; avaliar nova dependência apenas diante da implementação concreta.

## 7. Voz como canal, não novo agente

Pipeline: captura explícita → transcrição STT → mensagem no núcleo → resultado/eventos → síntese TTS opcional. Providers de STT/TTS são substituíveis, locais ou remotos; nenhuma escolha de modelo específico é fixada aqui sem benchmark na máquina.

- VOZ-01: começar com pressionar para falar. Wake word e escuta contínua ficam fora do primeiro incremento.
- VOZ-02: exibir transcrição editável antes do envio no modo inicial. Não falar ou transmitir áudio antes de consentimento/configuração.
- VOZ-03: estado visível para gravando, transcrevendo, processando e reproduzindo; erros voltam ao texto.
- VOZ-04: botão de interromper fala cancela TTS, não significa automaticamente cancelar a tarefa. Controles distintos devem indicar o efeito real.
- VOZ-05: ações sensíveis exigem confirmação autenticada da ação específica; não reconhecer a voz como fator de identidade.
- VOZ-06: não conservar áudio por padrão. Provedores remotos exigem aviso sobre envio de áudio; modo local nunca troca silenciosamente para nuvem.
- VOZ-07: propor limite inicial de 60 segundos por captura, sem biblioteca pesada obrigatória no boot. Limite pode ser ajustado após medição.
Teste de aceite: a mesma intenção por texto ou voz passa pela mesma política; transcrição contendo instrução de ignorar restrições não altera autorização.

## 8. Auditoria de vulnerabilidades e investigação


### 8.1 Quatro capacidades diferentes

1. Auditoria preventiva: código, dependências, segredos e configuração.
2. Detecção operacional: eventos de autenticação, política, execução e integridade.
3. Investigação: linha do tempo, correlação e evidências de um incidente suspeito.
4. Resposta: ação predefinida ou correção proposta, com aprovação conforme impacto.
Um scanner diário não enxerga automaticamente uma tentativa de invasão entre varreduras. Eventos precisam ser coletados no momento das ações. Mesmo assim, cobertura limitada não comprova que nenhum ataque ocorreu.

### 8.2 Corrigir o auditor existente primeiro


| Lacuna confirmada na revisão | Requisito de correção | Evidência de aceite |
| --- | --- | --- |
| Heurística de prompt baseada no tamanho e fallback genérico | Classificar como lint limitado; testar fronteiras reais com cenários adversariais | Conteúdo malicioso não autoriza ferramenta negada, mesmo quando o modelo aceita a instrução |
| Traversal testado por helper próprio | Exercitar ferramentas e workspace reais | Tentativas de escape/symlink/hardlink são recusadas pelo caminho efetivo |
| Guardrails verificados por nomes em constante | Verificar aplicação no executor/delegação | Filho sem permissão não executa ação por nenhum transporte |
| Regex de chave OpenAI malformada e .env tratado por sufixo | Corrigir detectores, nomes de arquivos e testes de regressão | Segredos sintéticos em arquivos temporários são detectados e nunca reproduzidos integralmente |
| Leitura/import falhando pode não gerar achado nem erro de cobertura | Registrar estado por verificação | Alvo ausente ou detector indisponível resulta em cobertura incompleta explícita |
| Score começa em 100 sem medir cobertura | Separar execução, cobertura e política de achados | Auditoria vazia/incompleta não aparece como certificado ou sistema seguro |
| Verificação API restrita a CORS | Declarar alcance e ampliar testes de controles selecionados | Auth, CSRF/origin e transporte avaliados pelo comportamento, não por presença de string |
| SAST próprio baseado em poucas regras | Manter como camada limitada; adaptar ferramentas maduras quando justificadas | Relatório informa detector/versão, limites e o que não foi analisado |


#### H0 — hotfix prioritário de segredos

Antecipar a correção dos defeitos existentes em entrega própria, antes do I1 e sem dependência do gate Mac. Escopo limitado: seleção de .env/.env.* por nome, regex OpenAI, sanitização de evidence e fixtures sintéticas que atravessem o detector real. Preservar os mecanismos atuais de autorização; esta entrega não introduz captura de novos alvos, exceções nem o contrato de cobertura de E1.1. Não afirma corrigir toda a travessia/leitura do scanner. Critérios: RED dos defeitos, GREEN da detecção, nenhum fragmento bruto do segredo em evidence/saídas e regressões integradas pertinentes. A execução do hotfix exige autorização da entrega.

Antes de mudar passed/security_score em E1.1, inventariar os consumidores internos e a interface pública: CLI, formatadores/JSON, código de saída e --fail-on, Web, cron, relatórios e consumidores externos documentados. Registrar ausência de consumidor quando verificada; não presumir que todos já existam. Versionar a saída e testar compatibilidade/códigos de política versus erro operacional.

Não usar pontuação única como autorização para deploy. Gate de segurança considera criticidade, cobertura obrigatória e exceções aprovadas com prazo.

### 8.3 Contrato de auditoria

AuditRun registra alvo/revisão, perfil, início/fim, versões dos detectores, estado da execução, checks esperados/executados, motivo de skips, achados e erros sanitizados.

Separar três dimensões:

- Execução: completed, partial, failed, cancelled.
- Resultado da política: pass, fail, unknown.
- Achados: coleção com fingerprint estável, severidade, confiança, categoria, localização sanitizada, detector e recomendação.
completed com achados não é falha do processo de coleta. partial não pode gerar pass quando falta uma checagem obrigatória. Exceções devem indicar responsável, motivo, prazo e revisão do escopo.

A linha de comando atual pode manter saída não zero para a política de CI. A integração agendada usa contrato estruturado ou adaptador que distingue achados de falha operacional; não alterar globalmente o scheduler para aceitar qualquer código de saída.

Política inicial proposta, preservando o limiar atual de severidade: achado alto/crítico não excepcionado produz fail; na ausência dele, falta de check obrigatório produz unknown; somente cobertura obrigatória completa sem bloqueios produz pass. Achados médios/baixos continuam visíveis e podem gerar prioridade de remediação, sem ampliar silenciosamente o limiar de bloqueio. Um fail com cobertura incompleta conserva ambas as informações. No lançamento de E1.1, exceções permanecem desabilitadas: nenhum campo do relatório, configuração, UI ou pedido do modelo pode retirar o bloqueio de alto/crítico. Somente E1.4b pode habilitá-las; então a exceção precisa de aprovação autenticada do operador, vigência e vínculo com fingerprint, alvo/escopo, revisão e motivo. Check obrigatório faltante continua unknown; exceção nunca dispensa redaction.

Perfis iniciais: (a) snapshot_static, com inventário/leitura do snapshot, detector de segredos e regras SAST declaradas obrigatórios; (b) dependency_advisories, com inventário de manifestos/lockfiles aplicáveis e consulta à base aprovada obrigatórios; (c) runtime_config, com controles de configuração/isolamento aplicáveis ao backend declarado obrigatórios. A cobertura depende do backend declarado e observado. Configuração inacessível é unknown. Um check só pode ser not_applicable mediante condição verificável; ausência de ferramenta ou acesso não é inaplicabilidade. Testes adversariais e de autorização compõem validação de regressão própria, não um resultado presumido do scanner estático. Cada relatório mostra o perfil: pass no snapshot não equivale a aprovação global do runtime. Políticas e checks adicionais entram versionados e com testes, sem reduzir cobertura obrigatória automaticamente para obter aprovação.

### 8.4 Integração recorrente

Reusar kairos_cron. Proposta inicial, desativada até configuração: auditoria leve diária de alvos cadastrados, análise mais ampla semanal e execução sob demanda. Revalidar frequência após medir custo e ruído. Não foi criado nenhum agendamento nesta especificação.

- Coleta técnica determinística não deve depender de LLM estar disponível.
- Deduplicar por fingerprint e conteúdo normalizado; ignorar timestamps/duração ao decidir se há mudança relevante.
- Alertar achado novo, agravamento, reincidência, falha de coleta e cobertura perdida. Resolução exige nova evidência, não desaparecimento causado por erro do scanner.
- Execução atrasada por suspensão/desligamento aparece como atrasada. Política inicial: recuperar a última execução devida, sem disparar toda a fila histórica.
- Um detector que precisa de mais tempo que o limite de fonte do monitor deve ser tarefa assíncrona supervisionada; o monitor consulta o resultado. Não aumentar timeout ou saída de todos os monitores.
- Resumo sem alteração relevante não exige nova chamada ao modelo.

### 8.5 Eventos e investigação

Adicionar eventos de negação de autenticação, negação de ferramenta, recusa de fronteira Docker, tentativa de acesso fora do workspace, violação de escopo de alvo, mudança de skill/política/credencial e falha de coleta. Sinalizar suspeita, não afirmar invasão confirmada apenas por uma regra.

O investigador recebe leitura de evidências sanitizadas e acesso por projeto. Pode construir linha do tempo e hipóteses com nível de confiança. Não recebe shell administrativo, poder para apagar logs ou aplicar patches em produção.

Propor retenção configurável inicial de 30 dias para eventos e relatórios sanitizados, com teto de armazenamento e alerta antes de descarte. Preservação de incidente é explícita. Rotação local e hash encadeado podem facilitar detecção de adulteração, mas não tornam logs invioláveis; resistência forte exige cópia em domínio de confiança separado.

## 9. Execução de testes de segurança autorizados

Perfis de execução

| Perfil | Escopo | Rede/privilégios | Fase |
| --- | --- | --- | --- |
| Análise estática | Snapshot controlado de projeto | Sem rede; sem segredos do host | Inicial |
| Consulta de advisories | Manifestos/lockfiles e serviço aprovado | Egress restrito ao serviço; sem acesso genérico à rede | Após contrato de auditoria |
| Teste web/API autorizado | Alvos cadastrados | Somente alvos/portas/protocolos autorizados; sem modo privilegiado | Depois de controle de escopo testado |
| Laboratório especializado | Ambiente dedicado e autorização específica | Capacidades justificadas por cenário | Posterior; fora do Mac como pressuposto |


O worker atual usa rede none; portanto não testa sites remotos diretamente. Criar política especializada separada, sem afrouxar o perfil padrão de programação. Kali é uma opção de imagem/ferramentas, não o mecanismo de isolamento nem requisito para toda análise.

Para alvos ativos, exigir registro de autorização com projeto/responsável, hosts/IPs, portas, método de teste, ambiente, janela de execução, limites e expiração. Estar na internet ou ser encontrado pelo agente não autoriza teste. Métodos destrutivos, carga/DoS e extração de dados ficam desativados nesta versão.

Aplicar escopo fora do LLM, no runner e na rede. Resolver DNS e validar IPs no momento da conexão; revalidar redirects e novas resoluções; bloquear loopback, link-local, redes internas e serviços de metadata salvo autorização exata do laboratório. Não usar correspondência por substring ou apenas o hostname como barreira. Ferramentas incapazes de obedecer às restrições não são habilitadas.

Sem mounts amplos do host, socket Docker no worker, --privileged ou chave do cofre. Artefatos passam por tamanho, tipo, path seguro e redaction antes de sair. Rede do próprio executor não deve permitir alcançar o plano de controle do Kairos.

### Mac Intel e isolamento

Docker Desktop usa uma VM Linux por baixo; container não significa ausência de virtualização. Há economia de composição em relação a uma VM Kali completa, mas o custo real deve ser medido.

A política revisada atesta AppArmorProfile específico; a captura de filesystem depende de /proc/self/fdinfo, O_PATH e demais operações observadas. Não está comprovado que isso funcione no Mac informado. Gate obrigatório: testar o cenário completo, nativo ou na VM Linux do Docker Desktop, incluindo filesystem, rede, usuário, capacidades, mounts, recursos e encerramento. Path.resolve ou a retirada da atestação não constituem adaptação equivalente. Falha implica perfil unavailable; não remover verificações para fazê-lo iniciar.

CI e sondas Linux por SHA/configuração/ambiente são evidência aceitável para desenvolvimento e regressão naquele backend; não encerram o gate Mac. Sem acesso ou capacidade comprovada no destino, execução/background que dependem desses controles ficam unavailable. Timebox proposto: cinco dias úteis a partir da abertura de E0.2, com início e data-limite registrados; nesse prazo entregar a matriz de capacidades/lacunas e uma decisão solicitada à usuária sobre acesso, adaptação equivalente ou backend alternativo. O vencimento não aprova o gate, não autoriza servidor Linux separado nem substitui a decisão. H0 continua independente desse gate.

Se o isolamento requerido não estiver disponível, manter análise local estritamente de leitura e bloquear execução de código não confiável; avaliar executor Linux separado sob nova decisão da usuária. Não há fallback silencioso para shell do Mac.

Kali Live/VM continuam alternativas externas opcionais para necessidades de hardware/kernel ou laboratórios específicos. O roadmap inicial não exige pendrive, reboot ou VM Kali dedicada.

## 10. Orquestração multiagente

**E4 — Prioridade Alta.** Entregar delegação após E0/E1 e os contratos comuns, antes de investir em E3. Voz é opcional/adiável e nunca pré-requisito de E4; a interface de leitura pode evoluir em paralelo após os contratos necessários.

Multiagentes não são vários prompts disparados livremente. A plataforma deve ter tarefas persistidas, relação pai/filho, limites de autoridade e mecanismos de revisão.

### Papéis iniciais


- Assistente coordenador: entende o pedido, consulta estado e propõe/delega tarefas autorizadas.
- Executor de projeto: atua em snapshot/workspace delimitado; produz artefatos e resultados verificáveis.
- Analista de segurança: interpreta resultados de detectores e evidências; solicita testes dentro de escopo aprovado.
Revisor de mudanças é um papel separado, acionado quando necessário, não outro processo permanentemente ativo. Usar o mesmo modelo em papéis diferentes não cria independência garantida.

### Requisitos


- AG-01: começar com um pai e um nível de delegação; filhos não criam outros agentes nesta versão.
- AG-02: concorrência inicial global de uma execução pesada; perfis distintos não significam processos sempre residentes.
- AG-03: filhos compartilham apenas contexto mínimo necessário, não o histórico integral, credenciais ou toda a memória do usuário.
- AG-04: todos os efeitos passam pelo mesmo serviço de política. Agente não chama ferramenta do host diretamente.
- AG-05: pai só encerra tarefa quando dependências e validações exigidas são satisfeitas. Resultado inconclusivo é visível.
- AG-06: cancelamento propaga aos filhos e ao runner, encerra processos e preserva evidência; efeitos externos já produzidos são informados, não magicamente revertidos.
- AG-07: prever timeout, orçamento, detecção de repetição e admissão persistente comum, sem fila própria em TaskService. Declarar por backend os limites impostos e o uso apenas observado; tokens/custo desconhecidos não são zero. Perfil que exige um limite sem enforcer verificável fica indisponível. Ao atingir limite, pausar/falhar de forma explícita, sem trocar para mecanismo menos restrito.
- AG-08: resultados entre agentes são dados não confiáveis. Recomendações não equivalem a autorização e não podem alterar política.
- AG-09: referências de artefatos são vinculadas à tarefa/projeto e verificadas antes da entrega; agente de um projeto não lê o de outro.
Primeiro caso de aceite: coordenador solicita auditoria de snapshot; executor produz relatório estruturado; analista resume com evidências; mudança de código fica como proposta revisável. Uma falha de coleta deve interromper conclusão de segurança, não gerar resumo tranquilizador.

## 11. Autonomia, memória e evolução


### Níveis de autonomia propostos


| Nível | Permitido | Exige aprovação adicional |
| --- | --- | --- |
| A0 — observar | Ler alvos e evidências já autorizados; produzir diagnósticos | Qualquer efeito fora desse escopo |
| A1 — executar rotinas | Rodar tarefas cadastradas em sandbox e orçamento aprovado | Novo destino, ferramenta, custo ou privilégio |
| A2 — propor melhorias | Criar candidato de skill/configuração/patch em área isolada | Promoção, publicação e mudança de produção |
| A3 — promover mudanças | Futuro: apenas classes reversíveis e comprovadas, com política própria | Segurança, credenciais e ampliação de autoridade sempre fora da autopromoção |


A0/A1 formam o início. A2 aproveita a infraestrutura de skills e revisão existente. A3 não fica habilitado nesta spec.

### Ciclo de melhoria

Observação → candidato identificado → procedência e escopo registrados → avaliação isolada → comparação com baseline → revisão/promoção autorizada → acompanhamento → rollback se houver regressão.

Memória deve distinguir preferências confirmadas, fatos com fonte, experiências observadas e hipóteses. Procedência e escopo são impostos na recuperação/contexto, além da gravação; memória global contém somente itens confirmados para esse alcance. Resultados de ferramentas, páginas e subagentes não entram como instruções de sistema. Dados de segurança e informações sensíveis exigem classe e política de retenção próprias.

Instalar ferramenta significa escolher versão/origem, verificar integridade e dependências, construir/testar imagem isolada e propor promoção. O agente não executa comandos arbitrários de instalação no host nem torna plugin recém-baixado automaticamente confiável.

Antes de iniciar E5, criar e executar o harness V0, com manifesto versionado de 10–20 cenários fixos, entradas sintéticas, saídas esperadas por contrato e baseline por SHA/ambiente/configuração. Incluir sucesso de tarefa, falhas de coleta, isolamento, autorização, cancelamento, reinício, limites e reconexão. Casos não executados ficam explícitos e não contam como sucesso. Falhas existentes podem integrar o baseline documentado para orientar correções; promoção exige cumprir os critérios obrigatórios, sem converter essas falhas em aprovação. Definir critérios de qualidade, regressão, custo/latência e reversão antes de avaliar um candidato; comparar o mesmo conjunto e registrar métricas indisponíveis. O plano define um conjunto inicial de 12 cenários, sem criar outro loop de execução.

Avaliação inicial deve cobrir: qualidade da tarefa, regressões de segurança, custo/latência e capacidade de reversão. Mais registros de memória não equivalem a melhor desempenho. Mudança só é considerada melhoria quando um conjunto de casos sustenta isso. Nenhum treinamento de pesos é necessário para esse ciclo.

## 12. Governança e controles transversais

Threat model e classificação de dados são artefato próprio, versionado por revisão de arquitetura, não apenas introdução desta SPEC. Entregar em E0.4 antes do design/implementação de E1.4a e atualizar antes de E1.4b, E4 e Trilha S. Inventariar ativos (credenciais, estado/journal, código/snapshots, artefatos, políticas e aprovações), fronteiras de confiança e fluxos, capacidades do atacante (página/resultado/filho malicioso, arquivo mutável, cliente não autenticado e dependência comprometida), premissas do host/mesmo UID e riscos residuais. Mapear cada ameaça a controle, teste e responsável, declarando lacunas.

Classificar dados como públicos, internos por projeto, pessoais/sensíveis e segredos/credenciais, com origem, destinos permitidos, ACL, sanitização e retenção; em conflito vale a classe mais restritiva. Segredos ficam no serviço local protegido existente e não entram em fixtures reais, logs, LLM ou UI. Este artefato não autoriza coleta de material sensível.

- Autorização determinística em cada ferramenta, não apenas no prompt ou na UI.
- Privilégio mínimo, tokens curtos/escopados quando aplicável e revogação.
- Política do filho não pode exceder a do pai; intervenção de administrador não pode ser simulada por texto.
- Segredos resolvidos no componente autorizado, nunca distribuídos como contexto geral.
- Separação entre repositório confiável, conteúdo externo e instruções aprovadas.
- Limites de entrada/saída, concorrência, subprocessos, disco, rede, tokens e tempo, distinguindo enforcement de observação. Retenção sanitizada proposta de 30 dias tem teto de armazenamento, alerta antes de descarte e hold de incidente; informar a retenção efetiva se o teto for atingido antes.
- Dependências e imagens reproduzíveis/pinadas; exceções e atualizações rastreáveis.
- API local por padrão; acesso remoto é decisão separada, com TLS e revisão do perímetro.
- Kill switch para novas admissões e cancelamento de execuções controladas; trilha do acionamento. Não prometer desfazer e-mails, publicações ou efeitos externos já realizados.
- Segurança não depende exclusivamente de detector de prompt injection: mesmo um detector que falha deve encontrar uma fronteira de execução que recusa ações fora do escopo.
Antes de compartilhar, implementar identidade por usuário, autorização por recurso/projeto, isolamento de memória/sessão/artefato, quotas, revogação e testes de acesso cruzado. Alternativa para primeiro piloto: instâncias realmente separadas por pessoa, com estado e credenciais separados, cuja separação também precisa ser validada. Não compartilhar o token atual como solução multiusuário.

Novos fluxos OAuth e integrações externas adicionais não fazem parte desta etapa. Preservar os mecanismos de autenticação já existentes.

## 13. Recursos e operação na máquina disponível

Estes são limites propostos para começar a medir, não promessa de consumo ou alteração automática da configuração atual.

| Item | Proposta inicial | Critério de ajuste |
| --- | --- | --- |
| Execuções pesadas | Uma por vez; fila explícita | Pressão de memória e latência de uso real |
| Workers | Manter perfil existente de 512 MiB onde funcionar; perfil de scanner separado até 1 GiB se necessário | OOM produz falha explícita; aumento não é automático |
| Docker Desktop | Testar orçamento total inicial de 3–4 GiB, contabilizando a VM e serviços residentes | Medir RAM/swap durante cenário representativo |
| Voz | Um job de transcrição por vez; TTS sob demanda | Responsividade e concorrência com ferramentas |
| Modelos de raciocínio | Reusar providers configurados; sem LLM local residente obrigatório | Custo, privacidade e benchmark específico |
| Artefatos e logs | Retenção e limites globais/perfil configuráveis | Disco livre, requisitos de investigação e alertas |


Medir baseline ocioso e cenários de uso: conversa+dashboard, transcrição, auditoria de projeto, execução isolada e recuperação após falha. Registrar hardware, tamanhos dos projetos e dependências para reproduzir a medição. Se a configuração não mantiver o uso interativo aceitável, reduzir concorrência/escopo antes de ampliar funcionalidades.

Proposta de prioridade: requisições interativas e cancelamentos têm precedência na admissão sobre novas tarefas em segundo plano. Não interromper um efeito externo já iniciado só para melhorar latência; ele mantém timeout/cancelamento e escopo próprios. Benchmark inicial é smoke de responsividade, sem estimativa robusta de cauda/SLA com 30 amostras. Métrica controle_local_ms: tempo do envio de um comando de controle autenticado até o ACK após validação e persistência da aceitação/negação, sem esperar o efeito terminar, com relógio monotônico no cliente e caminho identificado; exclui inferência/serviços externos do caminho medido, sem subtrair latência estimada. Métrica evento_painel_ms: do evento persistido até sua projeção renderizada com conexão ativa, correlacionado por event_id e relógios alinhados ou protocolo de medição documentado. Timeouts, erros e falhas de correlação ficam no resultado, sem descarte que melhore artificialmente a amostra.

Smoke threshold proposto: p95 empírico de controle_local_ms até 2 s e propagação evento_painel_ms até 3 s; nenhum OOM do núcleo ou processo órfão. Coletar pelo menos 30 controles por cenário durante auditoria representativa, publicar amostras, máximo, percentil e método (nearest rank: ceil(0,95 × n)); com n=30, p95 é a 29ª observação ordenada, próxima do máximo. Registrar RAM/swap, hardware, cenário e lacunas. Uma conclusão sobre cauda de produção exige plano de amostragem próprio e maior; não inferi-la deste smoke. E0 só encerra após registrar medições e aceitar esses limites ou uma revisão explícita antes de habilitar background/voz. Se o hardware não sustentar a meta, primeiro serializar tarefas e reduzir o perfil; não remover isolamento nem adotar nuvem silenciosamente.

Não estimar latência de voz, capacidade de inferência local ou duração de varredura sem dados do equipamento. Execução periódica local depende de o computador/serviço estar ativo; disponibilidade contínua exigiria outra infraestrutura, fora da autorização atual.

## 14. Roadmap por entregas verificáveis

Este é um roteiro de capacidades e dependências. O plano operacional de implementação, com alterações precisas, testes e commits, deve ser produzido após aprovação desta spec e atualização do baseline.

| Etapa | Entrega | Dependência | Gate para avançar |
| --- | --- | --- | --- |
| H0 — hotfix P0 de segredos | .env/variantes, regex OpenAI e evidence sanitizada | Autorização da entrega; independente do Mac/I1 | RED/GREEN sintético e saídas sem fragmentos do segredo; não aprova cobertura global |
| E0 — confirmar a base | Inventário, capacidades/benchmark do destino e threat model | Acesso autorizado ao destino; timebox de decisão registrado | Lacunas explícitas; Mac não aprovado por evidência Linux |
| E1 — tornar segurança confiável | Contratos, cobertura, redaction, cron/eventos | E0; I1: E1.4a → E1.1 sem exceções → E1.2 → E1.4b | Política/cobertura distintas; exceções só após E1.4b autenticado |
| E2 — leitura na fase mínima | Projeções autenticadas de tarefas/segurança, evidências e reconexão | Contratos/fontes de E1; sem esperar voz | Dados reais, estados degradados, isolamento de projeto e acessibilidade |
| E4 — Prioridade Alta | Delegação, limites, cancelamento e evidência | E0/E1, nota TaskService e contratos comuns; independe de E3 | Cenário ponta a ponta, sem execução duplicada nem escalada |
| E3 — voz opcional/adiável | STT/TTS consentidos sobre a mesma sessão | Autorização própria; extensão interativa de E2 | Sem envio silencioso; benchmark no Mac; não bloqueia E4 |
| E5 — evolução controlada | Candidatos e promoção revisada | Autorização própria, E4, harness V0 executado e baseline | Melhoria mensurável, procedência e rollback |
| E6 — piloto | Instâncias separadas ou multiusuário | Autorização própria e etapas estáveis | Isolamento, onboarding, revogação e limites testados |


Teste web/API ativo só é habilitado após o executor especializado e o controle externo de escopo estarem demonstrados. Ele não é requisito para começar a usar o assistente ou para a primeira auditoria estática.

Primeiro incremento I1 reordenado: E0 → E1.4a → E1.1 sem exceções → E1.2 → E1.4b. E0 inclui threat model e o gate do destino; falta de acesso/capacidade mantém E0 pendente. H0 pode ser entregue antes, com autorização limitada e sem aguardar Mac, E1.1 ou autoridade de E1.4. Aceitar I1 não conclui todo E1: perfis adicionais, persistência, eventos e cron têm entregas próprias. A próxima autorização de implementação deve delimitar H0 e/ou I1; E2/E4 e extensões não são autorizados implicitamente.

## 15. Plano de validação e critérios globais

Respeitar Python 3.11, uv, convenções e pipeline do repositório. Usar o roteiro de CI existente, incluindo testes Docker pertinentes quando houver ambiente. Testes opt-in não executados devem constar como não executados, nunca como aprovados.

### Matriz de aceite


| Cenário | Resultado exigido |
| --- | --- |
| Scanner sem diretório ou com detector indisponível | Erro/cobertura incompleta, sem aprovação enganosa |
| Segredo sintético em .env | Detecção por fixture e evidência redigida |
| Auditoria concluída com vulnerabilidade | Relatório e alerta de achado; não falha genérica da coleta |
| Relatório igual com outro timestamp | Sem alerta duplicado nem chamada desnecessária ao modelo |
| Tentativa de escape de workspace | Recusa pela ferramenta real, com evento sanitizado |
| Página ou filho pede ação não autorizada | Executor recusa, independentemente do texto do modelo |
| Token/escopo expirado ou parâmetros alterados | Nova autorização necessária |
| DNS/redirect aponta fora do alvo autorizado | Conexão recusada antes de alcançar o destino |
| Docker sem controle de isolamento exigido | Worker indisponível; nenhum fallback ao host |
| Serviço reinicia durante tarefa | Estado reconciliado/interrompido; sem duplicação silenciosa de efeitos |
| Usuária interrompe fala | TTS para e tarefa mantém estado coerente |
| Usuária cancela tarefa | Filhos/processos controlados encerrados; efeitos já ocorridos informados |
| LLM indisponível | Detectores técnicos continuam; síntese marca indisponibilidade |
| UI reconecta | Recupera estado sem reenviar ação aceita |
| Tentativa de acesso a outro projeto/usuário | Negação no backend, não apenas ocultação visual |
| Skill candidata falha na avaliação | Sem promoção; versão aprovada anterior permanece ativa |


Fixtures de scanners devem usar conteúdo sintético em diretórios temporários. Testes afirmam resultados de funções/ferramentas e invariantes; não leem arquivos de implementação para verificar se determinada linha/string está presente. Incluir testes de integração no caminho real para controles de autorização e isolamento.

A revisão de rastreabilidade dos documentos é gate editorial. Não substitui revisão independente de código, execução dos testes/controles ou aceite técnico do destino.

Definição global de pronto: efeito real demonstrado, política aplicada no backend, falhas explícitas, dados/segredos protegidos, testes relevantes executados, documentação atualizada e rollback definido. CI verde é condição necessária, não suficiente para alegar segurança.

## 16. Referências e rastreabilidade


### Kairos: fonte de verdade desta proposta

Links fixados na revisão examinada; o conteúdo presente na branch pode evoluir.

- Orientações do projeto.
- Composição do núcleo e serviço de interação.
- Auditor, modelos do relatório, verificações.
- Scheduler, execução de fontes e eventos operacionais.
- Política do worker e sandbox do chat.
- Servidor Web e frontend existente.

### Referências externas: ideias, não dependências aprovadas


- OpenJarvis: estudar separação de componentes, adapters de voz, rastros de execução e avaliação de mudanças. A revisão foi amostral e não executou sua suíte. Não transferir promessas de marketing para requisitos comprovados do Kairos.
- Documentação de arquitetura do OpenJarvis: contexto complementar dos componentes. Reaproveitar padrões, sem duplicar registros ou mecanismos existentes no Kairos.
- Networking do Docker Desktop: referência para a VM Linux e limites da fronteira de rede no desktop.
- TimLukaHorstmann/J.A.R.V.I.S.: referência fornecida pela usuária; a revisão anterior não confirmou acesso ao código do original, apenas a um fork identificado. Não usar isso como prova da arquitetura atual do original.
Qualquer reaproveitamento futuro de código, assets, modelos ou distribuição de dependências exige checagem de licença e compatibilidade. Inspiração arquitetural não autoriza copiar conteúdo indiscriminadamente.

## 17. Decisões pendentes para o momento adequado

Não impedem a validação desta direção; impedem apenas habilitar a funcionalidade correspondente.

1. Validar controles do Docker no Mac de destino e escolher backend alternativo somente se necessário.
2. Selecionar STT/TTS após benchmark e decisão de privacidade local/nuvem.
3. Definir alvos reais, autorização e janela antes de qualquer teste ativo.
4. Escolher identidade visual antes da implementação da nova superfície visual.
5. Escolher isolamento por instância ou multiusuário antes de convidar terceiros.
6. Confirmar orçamento financeiro antes de tarefas recorrentes com providers pagos.
Nenhuma dessas decisões deve ser tomada silenciosamente por um agente para concluir uma tarefa.
A direção e o plano são mapa; cada autorização de implementação deve nomear entregas, efeitos e gates. A prioridade alta de E4 é decisão confirmada nesta revisão. Implementar H0, I1, a fase mínima ou extensões exige o escopo correspondente; incorporar esta revisão documental não executa nenhuma dessas etapas.
