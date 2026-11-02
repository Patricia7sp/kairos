# Avaliação da especificação evolutiva do Kairos

Data: 2 de novembro de 2026. Revisão documental atualizada para a [SPEC v0.2](../specs/2026-11-02-kairos-evolucao-v0.2.md); E4 em **Prioridade Alta**, conforme decisão da usuária.

A direção da SPEC é coerente com o Kairos: manter um único núcleo de execução e autoridade, acrescentando uma experiência própria, voz opcional, segurança observável e delegação controlada. A fase mínima é E0 + E1 + E2 de leitura + E4. Delegação tem Prioridade Alta e antecede investimento em voz; E3, E5, E6 e Trilha S permanecem extensões com autorizações próprias. H0 corrige segredos em entrega limitada independente do Mac; I1 valida o destino e entrega o auditor por etapas, sem esperar exceções autenticadas para lançar E1.1.

Esta avaliação e o plano são entregas documentais. Aprovar sua produção não aprova instalar dependências, alterar o produto, criar agendas, executar testes ativos, publicar serviços ou compartilhar uma instância.

## Base revalidada

- A [SPEC original v0.1](../specs/2026-10-25-kairos-evolucao-v0.1.txt), seções 1–17, foi preservada. A v0.2 consolida as recomendações e é referência atual; o plano contém matriz de rastreabilidade das mudanças.
- Revisão citada pela SPEC: 89020109ad1d063f06dba9b685681e15cb22590a.
- Base inicial consultada: 4fe697bb9e05e5cc1c3cce7f4d9476cd851f42a2. A #130 integrou apenas teste/plano por squash em 23b4442603bec50481f9ca623436dfe76e3c2f8b; CI/deploy/smoke foram conferidos na entrega anterior. O checkout dos documentos continua em 4fe697b; a revisão documental não reexecuta a validação técnica.
- O diff desde a revisão original inclui catálogo e autoria de skills no chat, /learn, proteção de fronteiras de arquivos e preservação de cancelamento durante limpeza. Essas mudanças precisam ser reaproveitadas.
- A inspeção atual é de código e contratos. O host desta sessão é Linux; o Mac Intel com 16 GB permanece o alvo informado pela usuária. Resultados Linux não aprovam funcionamento, isolamento ou consumo no Mac.

## Achados que mudam o plano

| Prioridade | Evidência atual | Consequência |
| --- | --- | --- |
| P0 | [SecurityReport](../../../kairos_security/models.py) calcula passed apenas por severidade e inicia security_score em 100. [SecurityAuditor](../../../kairos_security/auditor.py) pode omitir fonte e converte falha de importação da API em origins vazio. | E1 precisa separar coleta, cobertura e política antes de publicar saúde de segurança no dashboard. |
| P0 | [Detector de segredos](../../../kairos_security/checks/secrets.py) mantém a expressão OpenAI malformada, trata .env por suffix, ignora erros e põe início da linha em evidence. | H0 antecipa detecção/evidence sanitizada com fixtures sintéticas, independente do Mac; E1.2 integra o snapshot seguro. Um achado não pode vazar a credencial que detectou. |
| P0 | [Captura de árvores](../../../kairos_filesystem/tree.py) usa identidade de montagem em /proc/self/fdinfo e operações específicas de Linux. [WorkerPolicy](../../../kairos_runtime/experimental/worker_policy.py) atesta AppArmorProfile e demais controles concretos. | O gate Mac inclui filesystem, além de Docker/AppArmor. Nenhuma troca por Path.resolve ou remoção de atestação pode ser tratada como portabilidade concluída. |
| P0 | [Aprovações do chat](../../../kairos_integration/interaction_service.py) são futuras em memória associadas à conversa; o runtime já tem aprovações, idempotência, leases e journal persistidos. | A aprovação de tarefa precisa de vínculo persistente com ação, parâmetros, política e escopo. Reutilizar as duas superfícies com adaptação explícita, preservando o protocolo atual. |
| P1 | [Monitor](../../../kairos_cron/monitor.py) compara bytes exatos; [SourceResult](../../../kairos_cron/source.py) considera saída não zero erro operacional. | Auditoria precisa de fonte tipada e representação sem timestamps/duração. Não relaxar os monitores genéricos. |
| P1 | [Journal operacional](../../../kairos_observability/service_events.py) permite códigos e campos limitados, com rotação por tamanho. | Acrescentar correlação, eventos de segurança, retenção por tempo e estado explícito de falha de gravação; conservar compatibilidade com registros existentes. |
| P1 | [Interface servida](../../../kairos_web/server.py) usa kairos_web/ui e [rotas DOM](../../../kairos_web/ui/js/app.js); web/ contém TypeScript e Vitest. | A nova experiência deve estender a interface efetivamente servida. Não planejar uma SPA concorrente com estado próprio. |
| P1 | [Limites de execução](../../../kairos_runtime/execution.py) declaram que MAX_TOOL_CALLS não é um enforcer das chamadas internas do Codex. | Diferenciar orçamento realmente imposto de uso apenas observado. Backend sem capacidade de impor um limite obrigatório fica indisponível para o perfil. |
| P1 | [ExperienceStore.find](../../../kairos_memory/__init__.py) não recebe filtro de projeto; [MemoryStore](../../../kairos_tools/memory.py) preserva entradas curadas, mas não o modelo de procedência completo proposto. | E5 deve impor escopo na recuperação e distinguir fatos, preferências, experiências e hipóteses sem criar outra memória concorrente. |

Não foi demonstrado pipeline integrado STT/TTS nem delegação nativa com tarefas pai/filho, orçamento e cancelamento persistidos nos módulos consultados. Nomes de políticas ou hooks não são evidência dessas capacidades.

## Avaliação das 17 seções

| Seção | Avaliação | Aplicação no plano |
| --- | --- | --- |
| 1. Decisão central | Preservar integralmente a plataforma única e o uso pessoal primeiro. As escolhas técnicas continuam propostas. | Restrições globais e gates de autorização; interface usa o núcleo existente. |
| 2. Base e inventário | A base de 25/10 envelheceu. Vários controles evoluíram; o auditor principal permaneceu com as lacunas indicadas. | E0.1 revalida o diff por SHA e classifica capacidade demonstrada, parcial, ausente ou indisponível. |
| 3. Objetivos e fracasso | Os sete objetivos são compatíveis entre si se segurança, recursos e dependências forem gates reais. | Matriz de cobertura, limites de admissão e testes de falso sucesso, duplicação e aumento de autoridade. |
| 4. Arquitetura | Evolução modular é a alternativa adequada à base atual. Serviço separado ou migração de framework acrescentariam reconciliação e operação sem necessidade demonstrada. | Um núcleo, SQLite e transportes existentes; nenhum broker, banco vetorial ou framework novo por padrão. |
| 5. Contratos comuns | São necessários antes de tarefas visíveis, delegação e auditoria supervisionada. Há contratos úteis de turno, mas tarefa não deve virar sinônimo de mensagem. | E1 estabelece resultado de auditoria e correlação; E4 estende a execução existente com vínculos pai/filho e limites. |
| 6. Assistente e dashboard | Fase mínima de leitura, autenticada e com fontes/estados indisponíveis. | E2.1 e projeção/reconexão de E2.3; conversa/controles novos em extensão de E2. |
| 7. Voz | Canal adicional, sessão compartilhada, consentimento e privacidade estão bem delimitados. Falta evidência para escolher provider/modelo. | E3 cobre VOZ-01–VOZ-07 após benchmark e escolha local/nuvem; texto continua utilizável. |
| 8. Auditoria e investigação | É a prioridade funcional. A distinção entre varredura, detecção, investigação e resposta evita promessas falsas. | E1 contempla perfis, política, coleta sem LLM, eventos, deduplicação, recorrência, retenção e investigação de leitura. |
| 9. Testes autorizados | O perfil padrão sem rede deve permanecer. Aprovação textual do modelo não pode abrir egress. | Trilha S, posterior ao gate do executor: registro de alvo, enforcement de rede, DNS/redirect, artefatos e expiração. |
| 10. Multiagentes | **Prioridade Alta**, objetivo central da fase mínima; não espera voz/UI nova. | E4 cobre AG-01–AG-09; nota TaskService antes de código, sem fila própria. |
| 11. Evolução e memória | O ciclo com baseline, avaliação e rollback aproveita autoria, publicação e experiências existentes. | E5 mantém A0/A1 como início, A2 isolado e promoção humana. A3 permanece desabilitado. |
| 12. Governança | Threat model/classificação como artefato próprio; autenticação atual não constitui multiusuário. | E0.4 antes de E1.4a, atualizar antes de E1.4b/E4/S; política/revogação/limites no backend. |
| 13. Recursos | Smoke threshold, sem SLA de cauda com 30 amostras. Linux não aprova Mac. | E0.3 define envio→ACK persistido e evento→renderização, amostras/máximo/p95 empírico e lacunas; E0.2 define timebox/decisão. |
| 14. Roadmap | Mínima E0 + E1 + E2 leitura + E4; **E4 em Prioridade Alta**, E3 adiável. | H0 separado; I1 E0 → E1.4a → E1.1 sem exceções → E1.2 → E1.4b; extensões com autorização própria. |
| 15. Validação | Os cenários globais são bons critérios de comportamento real. CI verde continua necessário e insuficiente para alegar segurança. | Matriz cenário → tarefa → teste → evidência; opt-ins ausentes registrados como não executados. |
| 16. Referências | Repositório e SPEC são as fontes de autoridade. Projetos externos continuam referências, com licença e capacidade a verificar. | Perfiles por SHA para o código; documentação primária para plataforma/rede; nenhuma adoção automática de OpenJarvis ou JARVIS. |
| 17. Decisões pendentes | Devem bloquear só a habilitação correspondente. Não impedem elaborar este plano integral. | Registro de decisões com gate, informação necessária e efeito de continuar pendente. |

## Ajustes de precisão incorporados à SPEC v0.2

1. Acrescentar ao gate Mac a fronteira de filesystem introduzida depois da revisão original. Validar o cenário de implantação completo: backend na VM do Docker Desktop e/ou suporte nativo explicitamente projetado. Um servidor Linux separado só entra por decisão nova da usuária.
2. Definir explicitamente qual projeção de tarefa envolve os turnos existentes e como ela referencia auditorias. Evitar uma segunda máquina de execução com reenvio e reconciliação próprios.
3. Documentar a migração das aprovações efêmeras do chat para ações persistidas, mantendo os contratos e a recusa existentes durante a transição.
4. Tornar a cobertura do perfil dependente do backend declarado. Configuração inacessível é unknown, não not_applicable. Lint de prompt e verificação de constantes não podem satisfazer controles de execução.
5. Definir quais limites o backend consegue impor antes de cada efeito. Métrica de tokens/custo desconhecida não vira zero; um limite obrigatório sem enforcer impede a admissão do perfil.
6. Explicitar que retenção de 30 dias tem teto de armazenamento: alertar e mostrar retenção efetiva antes de descartar, preservando incidentes marcados. Não prometer 30 dias se o teto for atingido antes.
7. Manter duas decisões distintas: aprovação do plano/direção e autorização de uma entrega de implementação. As decisões de plataforma, voz, visual, testes ativos, custos e compartilhamento permanecem nos gates adequados.

As sete precisões foram incorporadas, preservando a v0.1. As novas recomendações estão rastreadas no plano: H0 prioritário limitado; E1.4a/E1.4b separados; E4 em Prioridade Alta; fase mínima delimitada; nota TaskService antes de código; harness V0 com 10–20 cenários/baseline antes de E5; threat model/classificação próprios; métricas e p95 de E0.3 rotulados como smoke; inventário dos consumidores/exit code antes do novo contrato; saída/timebox do gate Mac. Elas delimitam entregas e gates; não habilitam funcionalidades por editar o documento.

A revisão de rastreabilidade anterior foi editorial, sem checkout/testes, e não conta como assurance independente do produto. Sua conclusão histórica sobre I1 está superada por esta reordenação; os gates técnicos continuam obrigatórios.

## Dependências e ordem de entrega

H0 pode anteceder E0 sob autorização limitada. E0 confirma base/destino, benchmark e threat model. I1 segue E1.4a → E1.1 sem exceções → E1.2 → E1.4b; os demais itens de E1 têm entregas próprias. E4 tem Prioridade Alta após E0/E1/contratos, sem esperar E3/UI nova; E2 de leitura pode avançar em paralelo quando houver fontes. E3 é adiável; E5 exige harness V0/baseline e autorização própria; E6 depende de estabilidade e decisão de compartilhamento.

A trilha S de testes ativos depende do controle especializado de escopo e rede e da autorização de cada alvo. Ela não bloqueia o assistente, a auditoria estática ou a voz.

## Decisões que permanecem com a usuária

| Decisão | Quando se torna necessária | Se continuar pendente |
| --- | --- | --- |
| Validar o Mac e escolher backend compatível | E0.2, timebox proposto de cinco dias úteis desde abertura | Registrar unavailable/lacunas e solicitar decisão; prazo não aprova Mac nem escolhe Linux. H0 independente. |
| STT/TTS e local/nuvem | E3, depois de medir candidatos autorizados | Voz desativada; texto disponível. |
| Alvos, responsável e janela | Antes de qualquer teste ativo da trilha S | Perfil ativo desativado. |
| Identidade visual | Antes de implementar a nova superfície de E2 | Backend e contratos podem ser preparados; não impor HUD ou framework novo. |
| Instâncias separadas ou multiusuário | Antes de E6 | Instância pessoal, sem convites ou token compartilhado. |
| Orçamento financeiro | Antes de recorrência com providers pagos | Coleta técnica independente do LLM; nenhuma recorrência paga habilitada. |

## Evidências e limites de validação

A avaliação reutiliza a inspeção do código indicado e do diff por SHA. Não executa scanners contra produção nem testes de intrusão. O aceite de cada etapa exige seus testes reais e revisão; o aceite Mac continua pendente de acesso autorizado e medição no equipamento.

A documentação do Docker confirma a VM Linux no Desktop e a dependência de perfis AppArmor carregados no kernel; isso não demonstra que a política atual funciona no Mac da usuária. [Permissões no Mac](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/), [AppArmor](https://docs.docker.com/engine/security/apparmor/).

Para a trilha S, validação de aplicação e restrição de rede precisam trabalhar juntas; um hostname aprovado não resolve sozinho redirects ou resoluções que mudam. [OWASP SSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html). A configuração efetiva de portas e rede também depende do Docker Desktop. [Networking no Desktop](https://docs.docker.com/desktop/features/networking/).

## Entrega documental e próximos escopos

Recomendações incorporadas à v0.2 e ao plano mestre, com E4 em Prioridade Alta por decisão da usuária. A fase mínima é E0 + E1 + E2 de leitura + E4; E3/E5/E6/S ficam como extensões com autorizações próprias. O hotfix H0 tem escopo limitado e independente do Mac. O I1 reordenado é E0 → E1.4a → E1.1 sem exceções → E1.2 → E1.4b; o auditor pode ser entregue com exceções fechadas antes de E1.4b. Esta rodada altera somente documentos; hotfix e implementação da fase mínima são entregas posteriores autorizadas por escopo.
