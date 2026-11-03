# E0.1 — baseline da fase 2 após H0

Data da coleta: 02/11/2026, UTC. Base de produto: `c6a083f51e12aaddeac8720185ecc55a27d010de` (main, PR #131). Árvore: `d44c3ec75e35568e085202d07e799b3912c958fb`.

Este relatório inicia o E0 autorizado pela instrução de seguir os próximos passos. É inventário e análise de evidências existentes; não habilita I1, delegação, voz, novos alvos, agendas ou exceções. O [plano mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md) e a [SPEC v0.2](../specs/2026-11-02-kairos-evolucao-v0.2.md) continuam sendo os contratos de escopo.

## Revisão, checkout e dependências

- Referência original da SPEC: `89020109ad1d063f06dba9b685681e15cb22590a` (#117).
- Base consultada na elaboração documental: `4fe697bb9e05e5cc1c3cce7f4d9476cd851f42a2`.
- Estabilização da recuperação (#130): `23b4442603bec50481f9ca623436dfe76e3c2f8b`.
- H0 integrado e implantado (#131): `c6a083f51e12aaddeac8720185ecc55a27d010de`.
- Worktree de inventário criado limpo a partir dessa main, branch `docs/fase2-e0`; o checkout original e os arquivos recuperados do H0 foram preservados.
- Python exigido: 3.11. A coleta administrativa usou Python 3.11.16; nenhuma dependência foi instalada nesta rodada documental.
- SHA256 de `uv.lock`: `1bcfd55b47e5fabb6721832d1134e2dbe92e3d0e7215b899e135962a965c27d8`.

Comandos de identificação executados:

```bash
git rev-parse c6a083f51e12aaddeac8720185ecc55a27d010de c6a083f51e12aaddeac8720185ecc55a27d010de^{tree}
git log --format='%h %s' 89020109ad1d063f06dba9b685681e15cb22590a..c6a083f51e12aaddeac8720185ecc55a27d010de
git diff --stat 89020109ad1d063f06dba9b685681e15cb22590a..c6a083f51e12aaddeac8720185ecc55a27d010de
git diff --name-status 4fe697bb9e05e5cc1c3cce7f4d9476cd851f42a2..23b4442603bec50481f9ca623436dfe76e3c2f8b
git status --short --branch
```

O delta desde `8902010` contém 112 arquivos, 11.907 adições e 367 remoções. Esses números identificam a inspeção, não constituem teste nem contrato de contagem.

## Mudanças posteriores que devem ser reaproveitadas

| Integração | Alteração relevante | Consequência para a fase 2 |
| --- | --- | --- |
| #119 / `224532e` | Índice automático de skills e catálogo no turno | E2/E4 devem usar o catálogo existente. |
| #120 / `f106ba8` | Teste dos limites reais de execução | Distinguir teto aplicado de constante apenas documental. |
| #121–#123 / `f474368`, `52f51fe`, `68077a0` | `/learn`, fechamento do fluxo, editor de skills ancorado e backups de memória | Reusar confirmação humana, identidade do alvo e recuperação; não recriar os serviços. |
| #124–#126 / `aa1200e`, `08b213f`, `a2987cc` | STARTTLS, destino explícito de Sonar e inicialização segura de configuração | Preservar recusas, diagnósticos sanitizados e distinção entre configuração ausente/inválida. |
| #127 / `4fe697b` | Cancelamento externo preservado durante limpeza | A projeção de tarefas não pode encerrar antes da inatividade confirmada. |
| #130 / `23b4442` | Recuperação de teste por snapshot terminal | Delta desde `4fe697b`: somente `tests/test_runtime_recovery.py` e seu plano; não reimplementar runtime. |
| #131 / `c6a083f` | `.env`, regex OpenAI e evidence omitida nos detectores/SAST | H0 concluído. Cobertura do auditor, captura segura e autoridade continuam em E1. |

## Evidências de execução reutilizadas

As evidências abaixo são do produto, anteriores a estes novos documentos. A inspeção de código desta rodada não é apresentada como nova execução da suíte.

| Origem e vínculo | Resultado observado | Escopo e limite |
| --- | --- | --- |
| [CI da main em `c6a083f`](https://github.com/Patricia7sp/kairos/actions/runs/41539207915) | Workflow e Sonar concluídos com sucesso; benchmark opt-in pulado | Linux; não aprova o Mac nem perfis não executados. |
| [Job unitário dessa main](https://github.com/Patricia7sp/kairos/actions/runs/41539207915/job/125598756336) | 4.363 testes e 6.657 subtestes aprovados; 24 skips, 34 deselects; 815,28 s | `coverage run -m pytest`, com runtime_live excluído e RealImageTests separados. Os skips não contam como teste executado. |
| [Imagem e integração](https://github.com/Patricia7sp/kairos/actions/runs/41539207915/job/125598756178) e [sessões Docker offline](https://github.com/Patricia7sp/kairos/actions/runs/41539207915/job/125598756394) | Jobs aprovados | Evidência de integração Linux; não afirmar sandbox interna ou chamada a provider real. |
| [Deploy da main](https://github.com/Patricia7sp/kairos/actions/runs/41539207915/job/125605211676) | Update Komodo concluído com sucesso | O log não informa `commit_hash` do Update; não é uma atestação de toda a imagem pelo SHA Git. |
| Verificação do H0 nesta sessão | Health HTTP 200, `status: ok`; serviço/broker healthy; hashes dos três arquivos do hotfix no container iguais aos aprovados | Confirma esses arquivos e a resposta do serviço, não todos os fluxos funcionais nem a procedência completa da imagem. |
| [Registro local do H0](../plans/2026-11-02-h0-segredos.md) | 4.366 testes e 6.657 subtestes aprovados com Codex 0.154.0 verificado; imagem/worker/frontends/lint aprovados | Árvore de produto idêntica à de `c6a083f`; cobre sondas offline adicionais, sem runtime_live. |

A coleta de cobertura tem limitação explícita: o job unitário da main agregou 105 arquivos, descartou 192 duplicatas e relatou seis fragmentos inválidos; o XML foi gerado. No ensaio local do H0 foram dez. Exit 0 de combine não significa ausência desses erros nem completude dos filhos. Não usar esse resultado para certificar cobertura global.

## Inventário de capacidades

Classificação: **demonstrada** significa comportamento delimitado exercitado nas evidências vinculadas; **parcial** indica controles reais com lacunas para a fase 2; **ausente** indica que o contrato pretendido não foi localizado no escopo inspecionado; **indisponível** indica ausência de acesso/capacidade comprovada no destino. Nenhuma classe significa certificação de segurança.

| Área / estado | Código e contrato observados | Evidência comportamental existente | Lacuna ou próximo responsável |
| --- | --- | --- | --- |
| Interação compartilhada — demonstrada no contrato atual | [composition](../../../kairos_integration/composition.py), `InteractionService` e `InteractionEnvelope` | [composição](../../../tests/test_interaction_composition.py), [contrato](../../../tests/test_interaction_contract.py), [admissão](../../../tests/test_interaction_admission.py) | Reusar o núcleo; novo centro de tarefas não está entregue. |
| Runtime/sessões — parcial para a fase 2 | [service](../../../kairos_runtime/service.py), [store](../../../kairos_runtime/store.py): turnos, fila, leases, fencing, idempotência e reconciliação | [recuperação](../../../tests/test_runtime_recovery.py), [leases](../../../tests/test_runtime_leases.py), [host](../../../tests/test_runtime_host.py) | TaskRecord/AuditRun e a composição de estados ainda precisam de design. |
| Aprovações/workspace — parcial | [workspace](../../../kairos_tools/workspace.py), [política de diretório](../../../kairos_runtime/policy.py); aprovações do runtime duráveis; aprovações genéricas do chat em futures | [ferramentas reais](../../../tests/test_workspace_tools.py), [política](../../../tests/test_runtime_policy.py), [aprovações](../../../tests/test_runtime_approvals.py) | Principal/AuthorizedTarget/ActionGrant com revisão, prazo e revogação não implementados como fronteira de auditoria; E1.4a/b. |
| Credenciais — parcial por plataforma | [CredentialService](../../../kairos_security/credentials/service.py), [factory](../../../kairos_security/credentials/factory.py), referência versus segredo | [cofre](../../../tests/test_credential_vault.py), [credencial ativa](../../../tests/test_active_credentials.py), [migração](../../../tests/test_credential_migration.py) | Cofre sintético e seleção estão cobertos; keyring/integração reais do Mac não foram validados nesta rodada. |
| Auditor — parcial | [models](../../../kairos_security/models.py), [auditor](../../../kairos_security/auditor.py), [H0](../../../tests/test_security_redaction.py) | [auditoria](../../../tests/test_security_audit.py), regressão real auditor/CLI/dataclass/repr | `passed` depende de severidade; faltam execução/cobertura/política, perfis versionados e autoridade do alvo; E1.1/1.2/1.4a. |
| Captura de filesystem — parcial por backend | [tree](../../../kairos_filesystem/tree.py), [descriptors](../../../kairos_filesystem/descriptors.py): descritores, identidade, mount, limites, captura/restauração/remoção | [TreeIOTests](../../../tests/test_tree_io.py), [fronteiras reais](../../../tests/test_security_file_boundaries.py), imagem Linux | Primitivas Linux e filesystem atestado; não há equivalência nativa Mac demonstrada. |
| Agenda/monitores — parcial | [Scheduler](../../../kairos_cron/scheduler.py), [SourceResult](../../../kairos_cron/source.py), [monitor](../../../kairos_cron/monitor.py) | [fontes](../../../tests/test_cron_source.py), [monitores](../../../tests/test_cron_monitors.py), [superfícies](../../../tests/test_cron_surfaces.py) | Monitor compara hash de bytes e fonte não zero é erro operacional; faltam fonte de auditoria tipada e deduplicação sem timestamps; E1.5/1.6. |
| Eventos — parcial | [service_events](../../../kairos_observability/service_events.py), journal privado, catálogo/counters e rotação por tamanho | [journal](../../../tests/test_service_events.py), [produtores async](../../../tests/test_async_service_event_producers.py) | Não constitui correlação de tarefa/projeto, retenção temporal ou redaction universal de texto; E1.7. |
| Memória/experiências — parcial | [ExperienceStore.find](../../../kairos_memory/__init__.py) e [MemoryStore](../../../kairos_tools/memory.py) | [experiências](../../../tests/test_experiences.py), [memória](../../../tests/test_memory_tool.py) | `find` não recebe projeto; procedência e isolamento de recuperação propostos em E5 não estão entregues. |
| Skills — demonstrada no escopo Linux atual | Catálogo, `/learn`, mutações, proveniência, proteção organizacional, publicação e rollback existentes | [learn](../../../tests/test_learn_interaction.py), [publicação](../../../tests/test_skill_publication_service.py), [política](../../../tests/test_skill_org_policy.py), imagem | Preservar identidade e capacidades existentes; não implica autopromoção/A3 ou portabilidade Mac. |
| Web — parcial para E2 | [server](../../../kairos_web/server.py) serve `kairos_web/ui`; [runtime transport](../../../kairos_web/runtime_transport.py) | [autenticação/rotas](../../../tests/test_web.py), [transporte](../../../tests/test_web_runtime_transport.py), Vitest/tsc | E2 deve estender a UI realmente servida; centro de tarefas, correlação e acessibilidade têm aceite próprio. |
| Delegação nativa E4 — ausente no contrato AG-01–AG-09 | [policy](../../../kairos_tools/policy.py) tem listas de delegação; [redaction](../../../kairos_runtime/redaction.py) reconhece estados de agentes do protocolo | Testes atuais de política/wire não demonstram tarefa pai/filho com admissão e orçamento canônicos | TaskService/TaskRecord, grants herdados e cancelamento persistido de filhos ainda não foram localizados como implementação; E4 após E0/E1. |
| Voz integrada — ausente no escopo inspecionado | Referências a modelos de transcrição em adapters não formam pipeline STT/TTS da sessão | Nenhum aceite VOZ-01–VOZ-07 foi demonstrado | E3 opcional; não bloqueia E4 e não justifica escolher provider agora. |
| Detecção de intrusão — ausente no contrato pretendido | Journals/monitores genéricos fornecem insumos, não detector de ataques | Não há evidência de detector operacional com regras/correlação cobrindo a SPEC | E1 deve declarar cobertura; Trilha S é autorização posterior, não pentest implícito. |
| Compartilhamento — parcial, operador único | [token de sessão e tickets](../../../kairos_web/server.py); peer/arquivos privados no runtime | [SessionTokenTests](../../../tests/test_web.py), [segurança runtime](../../../tests/test_runtime_security.py) | Não demonstra identidade multiusuário e ACL por recurso/projeto; E6 ou instâncias separadas por decisão própria. |
| Mac/E0.2 — evidência inicial parcial; benchmark/E0.3 pendente | [Matriz e protocolo](2026-11-02-fase2-plataforma.md), atualizados com coleta manual da usuária em 03/11 | Identificação de macOS/Intel/16 GiB e CLI; consultas ao daemon falharam; nenhuma amostra autenticada/renderizada | Engine/Desktop, controles e medições pendentes; E0 não está concluído. |

O inventário é delimitado aos módulos/consumidores citados e ao delta por SHA. Uma busca por nomes conceituais não é prova isolada de ausência: a classificação considera os serviços e caminhos de execução existentes; se surgir implementação equivalente, ela precisa de vínculo e teste antes de mudar a classe.

## Lacunas que delimitam a próxima implementação

1. E0.2/E0.3 dependem de evidência do destino. A coleta manual de 03/11 identifica o Mac, mas não alcança o daemon Docker; o Linux observado não substitui os controles e medições ainda pendentes no destino.
2. O [modelo de ameaças E0.4](../specs/2026-11-02-fase2-threat-model.md) separa proteção atual de controle exigido. Sua revisão é pré-requisito de E1.4a; não implementa autorização.
3. `SO_PEERCRED` ausente faz `_check_peer` retornar sem executar a verificação do UID. Isso deve constar do design de portabilidade; permissões do socket não são evidência suficiente de uma verificação equivalente.
4. `sanitize_payload` usa allowlists estruturais, mas preserva conteúdo textual/JSON permitido. H0 não transforma esse mecanismo num removedor global de segredos.
5. Não criar TaskService, migração ou fila alternativa nesta entrega. A nota de design própria continua obrigatória antes de E4.

## Estado de aceite

- E0.1: relatório elaborado e revisado independentemente; pronto para revisão da usuária.
- E0.4: modelo elaborado e revisado como design; pronto para revisão da usuária, sem autorização de coleta nem atestado de eficácia.
- E0.2: identificação manual do Mac recebida em 03/11; Engine/Desktop e validação dos controles pendentes. E0.3: protocolo de medição e execução real pendentes.
- Gate E0 e habilitação de I1: **pendentes**. E4 mantém Prioridade Alta depois de suas dependências; E3 continua opcional.

Revisão documental independente em 02/11/2026: GPT-6 Luna/high, contexto isolado e somente leitura; sem achados Critical/Important/Minor materiais nem pergunta nova necessária. Conferiu os quatro documentos e afirmações relevantes contra o código/contratos citados, sem executar testes ou atestar o destino. O aceite do gate E0 não foi concedido por essa revisão.

Não houve alteração de produto, instalação, benchmark em produção, novo scan ativo, agendamento ou publicação de serviço nesta preparação.
