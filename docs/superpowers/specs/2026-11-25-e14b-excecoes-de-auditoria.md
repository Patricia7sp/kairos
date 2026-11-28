# E1.4b — aprovação e revogação de exceções de auditoria

**Estado:** desenho revisado em 26/11/2026 sobre `83b39c0` (E1.2, [#139](https://github.com/Patricia7sp/kairos/pull/139)). Primeiro recorte interno integrado em `f5c913d` ([#140](https://github.com/Patricia7sp/kairos/pull/140)). A [revisão técnica](#revisão-técnica-da-implementação-27112026) identificou duas lacunas; as [correções](#correções-após-a-revisão-técnica) passaram por revisão independente sem achados no escopo. O aceite de eficácia segue aberto. Fontes: [autorização e contrato de auditoria da SPEC v0.2](2026-11-02-kairos-evolucao-v0.2.md#autorização-de-ação), [plano-mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md#e14b-exceções-autenticadas-e-ações-existentes), [E1.4a](2026-11-22-e14a-autoridade-de-leitura.md) e [modelo de ameaças](2026-11-02-fase2-threat-model.md#revisão-para-e14b--exceções-de-auditoria).

## Efeito e escopo

Uma exceção registra que a operadora aceitou temporariamente **um achado de uma exportação exata**. O achado continua visível, inclusive sua severidade. A exceção só retira o bloqueio desse achado na avaliação de `snapshot_static`; não muda cobertura, não concede leitura e não autoriza execução, deploy ou alteração no projeto. O `security` da CLI continua sendo o diagnóstico local, com seu contrato de saída e sem exceções. A adaptação das confirmações genéricas do chat é o segundo recorte de E1.4b, com desenho e testes próprios.

Hoje [o despacho interno](../../../kairos_web/audit_authority.py) entrega uma cópia autorizada ao [auditor estático](../../../kairos_security/snapshot.py). O `SecurityReport` resultante é mutável e não constitui prova de origem nem registro persistido. [O fingerprint atual de `Finding`](../../../kairos_security/models.py) não inclui projeto, revisão, versão do detector ou severidade; sozinho, ele não autoriza suprimir um achado. Um JSON exportado, mesmo idêntico a um relatório verdadeiro, não é uma fonte de achados para aprovação.

### Premissas e não objetivos

- O gate de E1.4b é o mesmo do I1: Service HTTPS privado no Linux, navegador no Mac, autoridade/relógio/SQLite no servidor. Multiusuário, identidades por pessoa e revogação individual de sessão ficam fora.
- `web:operador` representa a instância autenticada; credencial compartilhada não fornece atribuição por pessoa. A UI e os registros devem tratar esse limite como fato operacional, não como detalhe implícito.
- A exceção não cria um canal de leitura: o despacho de exceção só seleciona projeções/IDs já emitidos pelo servidor sob `read_snapshot` vigente. Não reabre a exportação, não lê bytes do snapshot e não aceita conteúdo bruto como entrada.
- Não há aprovação por chat, modelo, header, ticket WSS, CLI, config, fixture de produção ou payload desserializado. O relatório exportado é artefato informativo, não prova.
- O primeiro recorte não inclui API pública/rota estável de auditoria, fila/reexecução, TaskRecord, migração das aprovações genéricas do chat nem mudanças no contrato do `security` da CLI.

## Autoridade e confirmação

Reusar o Service HTTPS privado já aceito no E0. O servidor exige POST, origem HTTPS configurada exata e cookie `kairos_session` válido; deriva `web:operador` pelo adaptador de E1.4a. Header, ticket WSS, CLI, runtime e mensagens não podem confirmar por essa interface. Essa identidade representa a instância de uma operadora; não distingue pessoas que compartilhem a credencial. Não presumir revogação individual de sessão pelo logout atual.

| Ação | Autoridade e entrada |
| --- | --- |
| Executar auditoria ou abrir sua projeção sanitizada | `read_snapshot` vigente para o principal e exportação exatos. IDs opacos só selecionam registros; não são credenciais. |
| Preparar e confirmar exceção | `approve_audit_exception`, concedido explicitamente pela administração local para principal, projeto e revisão, além do `read_snapshot` ainda vigente que originou a prova. A operadora seleciona o achado de uma execução registrada e escolhe motivo e expiração. Confirma depois de ver a ação exata. |
| Revogar exceção | `revoke_audit_exception`, independente da leitura e do grant que aprovou a exceção. O servidor resolve o ID e confere a mesma exportação. A administração local também pode invalidar a política pelo mecanismo existente. |

Os grants de exceção têm o mesmo modelo de persistência, epoch, prazo e revogação do store atual; a ação difere. `approve_audit_exception` e `revoke_audit_exception` não concedem leitura, não concedem uma à outra e não podem ser inferidos do cookie/login. A revogação de exceção é separada para permitir resposta operacional sem reabrir a capacidade de aprovar.

O backend prepara uma intenção curta de uso único: resolve `run_id` e `finding_ref`, fixa ação, principal, transporte/origem, projeto, revisão, prova do achado, versão da política, grants, motivo e prazo no estado privado. O segundo POST envia somente a referência da intenção para confirmar. O servidor revalida os vínculos e confirma o registro exato que exibiu. Alterar motivo, prazo, alvo ou ação exige outra intenção. Revogação identifica uma exceção concreta; dupla submissão de revogação devolve o estado revogado sem novo efeito. O fluxo não usa `/tool-approvals`, nem oferece ferramenta de modelo ou aprovação por configuração.

Schemas fechados e limitados; identidade, decisão de política, fingerprint e caminhos nunca são aceitos como afirmações do cliente. A apresentação usa identificadores e metadados sanitizados: projeto/revisão, regra, severidade, ID opaco do arquivo, linha, ordinal das ocorrências reportadas na mesma linha, motivo e validade. O ordinal segue a ordem da coleta; não é coluna ou trecho do arquivo. O primeiro corte usa motivos de catálogo (falso positivo verificado, risco temporariamente aceito com mitigação externa), como declaração da operadora. Detalhes da investigação ficam no canal privado apropriado. Não armazenar justificativa livre, snippets, argumentos brutos, cookies, conteúdo de arquivos ou caminhos neste fluxo.

### Contrato mínimo do despacho

O recorte herda o contrato de `dispatch_capture`: método POST, origem HTTPS configurada exata, cookie `kairos_session` válido derivando `web:operador`, `Content-Type: application/json` sem parâmetros extras, corpo limitado, JSON sem chaves duplicadas e sem `query_string`. O corpo usa schema fechado por ação; chaves desconhecidas, tipos errados, strings vazias/longas ou valores fora do catálogo são recusa.

| Ação interna | Entrada do cliente | Saída sanitizada |
| --- | --- | --- |
| Registrar prova | Seletores exatos da exportação já aceitos pela captura (`project`, `revision`, `grant` de leitura) | `run_id`, metadados da execução e referências opacas de achados; sem conteúdo bruto |
| Abrir prova | `run_id` | Projeção e avaliação histórica datada, mediante o grant original ainda vigente; referências aprováveis só durante a vigência da prova |
| Preparar exceção | `run_id`, `finding_ref`, `reason_code`, `expires_at` | Intenção curta com resumo exato do que será confirmado; sem echo de campos sensíveis |
| Confirmar exceção | `intent_ref` | Decisão persistida ou estado já confirmado para a mesma intenção consumida |
| Revogar exceção | `exception_id` ou referência opaca equivalente resolvida no servidor | Estado revogado/consultável, sem dados de outro escopo |

Erros de autorização, inexistência, expiração ou conflito retornam resposta genérica sem distinguir existência de projeto/achado entre escopos não autorizados. Logs e métricas ficam no registro operacional privado, sem payload, sem cookie e sem caminhos.

## Prova de origem e vínculo de aplicação

1. **Registro da observação.** Somente o despacho autenticado de E1.2 pode apresentar uma observação para registro. Ele congela a projeção do resultado sobre a própria cópia atestada e vincula principal, grant de leitura, projeto, revisão, identidade cadastrada da exportação, fingerprint da captura, versão do perfil, estado/checks e descritores sanitizados dos achados. O servidor emite `run_id` e referências de achado aleatórias. Inventário inválido ou execução cancelada não emitem seletores utilizáveis. Coleta parcial com inventário válido pode registrar achados observados. Registrar a prova e disponibilizar o seletor só depois de confirmar a transação. Não materializar bytes nem reabrir a árvore para emitir essa prova.
2. **Seleção inequívoca.** A chave de aplicação é versionada e inclui projeto, revisão, identidade cadastrada e fingerprint da exportação, perfil e sua versão, detector e sua versão, severidade e `Finding.fingerprint`. O backend monta a chave com dados da coleta, em formato canônico, sem evidência bruta. Para aprovar ou aplicar, o check produtor precisa estar completo e a chave precisa identificar um único achado no resultado. Se dois achados tiverem a mesma chave, a exceção não se aplica a nenhum deles. Uma regra inteira, todos os achados de um arquivo ou uma revisão futura não são escopos aprováveis.
3. **Persistência estreita.** Usar o [store de autoridade existente](../../../kairos_tools/audit_store.py) para prova, intenções e decisões; sem segunda fonte de autorização. Cada exceção vincula a chave, origem da prova, principal, ação, grants, epoch da política, motivo, emissão, validade e estado de revogação. A aprovação consome a intenção e grava a decisão na mesma transação. Confirmação repetida não cria outro registro nem renova prazo; duas intenções concorrentes não podem deixar duas exceções vigentes para a mesma chave e principal. A prova é uma projeção mínima de coleta concluída ou parcial, sem fila, reexecução, TaskRecord ou repositório concorrente de `AuditRun`. E1.5 define a persistência geral e sua referência a essa prova.

### Modelo de dados mínimo

Campos lógicos mínimos, todos privados ao serviço e serializados em formato canônico antes de hash/persistência:

| Registro | Campos mínimos | Observações |
| --- | --- | --- |
| Prova de observação | `run_id`, `principal`, `read_grant_ref`, `project`, `revision`, `export_identity`, `capture_fingerprint`, `profile`, `profile_version`, `execution_state`, `checks[]`, `findings[]` sanitizados, `created_at_utc`, `created_boot_ns` | A projeção versionada guarda chave canônica, severidade, regra/detector/versão, localização opaca, linha e ordinal; o cliente não recebe a chave. |
| Intenção | `intent_ref`, `action`, `principal`, `origin`, `transport`, `run_id`, `finding_key`, `approve_grant_ref` ou `revoke_target_ref`, `policy_epoch`, `reason_code`, `requested_expires_at_utc`, `issued_at_utc`, `deadline_boot_ns`, `consumed_at_utc?` | Uso único; confirmação altera estado na mesma transação. |
| Exceção | `exception_id`, `finding_key`, `proof_run_id`, `principal`, `approve_grant_ref`, `read_grant_ref`, `policy_epoch`, `reason_code`, `issued_at_utc`, `expires_at_utc`, `expires_boot_ns`, `revoked_at_utc?`, `revoked_by_principal?`, `revocation_reason?` | ID opaco; revogação terminal. |
| Avaliação com exceção | `assessment_id`, `run_id`, `policy_outcome`, `coverage_complete`, `threshold`, `applied_exception_ids[]`, `evaluated_at_utc`, `evaluated_boot_ns`, `commit_confirmed` | A mesma transação congela, na projeção, o vínculo por ocorrência com ID, motivo, emissão e validade da decisão. O histórico é datado e não autoriza efeito futuro. |

## Vigência, concorrência e falhas

- Proposta inicial: uma prova admite nova aprovação por até 24 horas; a intenção vence em até 10 minutos e não pode ser confirmada com prova vencida. A exceção vence em até 24 horas da confirmação, limitada também pelo vencimento dos grants de leitura e aprovação de origem. Use hora UTC e prazo monotônico na mesma geração de boot, como na defesa temporal de E1.4a. Sem relógio válido, após reboot ou diante de prazo divergente, não aplicar. Renovação exige nova confirmação; revogação é terminal para aquele ID.
- Ao registrar a prova, confirmar ou avaliar com exceção, ler vínculos frescos sob o `flock` comum. Em cada avaliação, exigir `read_snapshot` atual para o despacho, grants de origem ainda vigentes e do mesmo principal, epoch da política, exportação ativa e todos os vínculos de ocorrência. Um grant novo não substitui um grant revogado no vínculo persistido. Revogar qualquer grant de origem, retirar o projeto ou invalidar a política impede novo uso. A revogação grava o evento e confirma o commit sob o mesmo lock. O avaliador registra o resultado e IDs aplicados nessa seção crítica, usando o instante da validação final; só publica se o commit for confirmado. Captura (com seus próprios guards), detecção e resposta HTTP ficam fora da seção de decisão. Se a revogação vencer o lock primeiro, nenhuma avaliação posterior usa a exceção; se a avaliação vencer, seu resultado é histórico, datado, e não pode servir como permissão para um efeito futuro.
- Abrir o store existente; migração versionada e explícita sob o lock, com serviço fora de atendimento. Falha de relógio, store, registro obrigatório ou revalidação da exceção devolve avaliação sem exceções quando a leitura/entrega segue autorizada, com indisponibilidade indicada. Falha na própria autoridade de leitura impede a entrega Web. Quando o adaptador está indisponível, um bloqueante continua `fail`. Nenhuma recuperação reaplica decisão incerta: o registro da exceção conserva a referência à intenção consumida para consulta após uma resposta perdida, sob autorização atual. Se a referência não for reconhecida, recusar; não recriar a intenção.
- Projeção e decisões são dados internos por projeto no serviço Linux; acesso à prova exige grant e nunca vai ao LLM, workspace, journal genérico ou worker. Proposta para implementação: teto explícito de linhas/bytes e retenção de até 30 dias para prova e decisões, com limpeza sob lock e descarte de intenções vencidas. Preservar a trilha das exceções vigentes; se o teto impedir gravação obrigatória, recusar nova aprovação/uso em vez de apagar registro necessário. Guarda de incidente e retenção geral ficam em E1.7. Rollback desativa o adaptador; downgrade de esquema ou restauração exigem operação local que invalide grants/exceções anteriores antes de reabrir o serviço. Nunca voltar a servir com uma cópia antiga da autoridade que possa ressuscitar revogações.

### Recuperação e operação

| Falha | Comportamento | O que não pode acontecer |
| --- | --- | --- |
| Commit da exceção incerto / resposta perdida | Consulta autenticada pelo `intent_ref` consumido retorna o estado persistido; sem intenção reconhecida, recusar | Criar segunda exceção, renovar prazo ou reaplicar sem prova |
| Reinício / reboot | Reabrir store; antes de usar exceção, exigir relógio válido e prazo monotônico da mesma geração | Aplicar exceção com prazo divergente ou após boot sem validação |
| Migração de esquema | Migração explícita e versionada sob lock, serviço fora de atendimento, com backup local | Migração parcial em atendimento ou downgrade implícito |
| Restauração de backup | Procedimento local: manter serviço fechado, restaurar, invalidar grants/exceções anteriores e exigir recadastro/reconcessão antes de reabrir | Ressuscitar exceção revogada ou grant antigo |
| Teto de armazenamento atingido | Recusar nova prova/intenção/exceção; preservar histórico necessário | Apagar decisão vigente para abrir espaço silenciosamente |

### Runbook local do primeiro recorte

O adaptador `dispatch_audit_exception` é interno. O estado fica em
`audit-authority.sqlite3` e a exclusão mútua em `audit-authority.lock` no
diretório privado da autoridade. Use `AUTHORITY_HOME` com o caminho absoluto
desse diretório e execute com o mesmo UID do serviço. O diretório deve ser
`0700` e os arquivos, regulares e `0600`. Mantenha os backups sob a mesma custódia.
Para conceder autoridade, use a administração local `AuditAuthority.grant`
com principal `web:operador`, projeto cadastrado, revisão, expiração UTC e
uma ação explícita: `read_snapshot`, `approve_audit_exception` ou
`revoke_audit_exception`. Só o seletor do grant de leitura vai ao cliente.

1. **Migração v1 → v2.** Pare todos os processos que usam a autoridade e
   guarde um backup consistente do SQLite. Execute a migração abaixo. O
   cadastro de exportações é preservado; os grants v1 não têm geração de boot
   e são descartados. Reconceda apenas as ações necessárias antes de retomar o
   despacho. O serviço recusa v1 e versões incompatíveis durante o atendimento.

   ```bash
   uv run python -c 'import sys; from pathlib import Path; from kairos_tools.authorization import AuditAuthority; AuditAuthority(Path(sys.argv[1])).migrate()' "$AUTHORITY_HOME"
   ```

2. **Restauração.** Pare todos os consumidores. Preserve o store anterior para
   investigação. Prepare uma cópia consistente v2 do backup em **outro inode**
   no diretório privado e instale-a por renomeação; mantenha o arquivo de lock
   existente. Nunca sobrescreva o SQLite em uso nem reutilize sidecars da cópia
   anterior. Com o serviço ainda fechado, execute:

   ```bash
   uv run python -c 'import sys; from pathlib import Path; from kairos_tools.authorization import AuditAuthority; AuditAuthority(Path(sys.argv[1])).reset_after_restore()' "$AUTHORITY_HOME"
   ```

   A operação invalida a política, encerra exceções antigas, remove todos os
   grants e cadastros e vincula a base ao lock e ao inode atuais na mesma
   transação. Recadastre a exportação e reconceda os grants explicitamente;
   confirme que os seletores antigos são recusados antes de reabrir. Se a
   preparação ou o reset falhar, mantenha o serviço fechado.

3. **Reboot e rollback.** Grants, intenções e exceções de outro boot não são
   reutilizados. Para retomar a operação após reboot, retire o cadastro antigo
   com `AuditAuthority.retire`, recadastre a exportação e emita novos grants
   sob o relógio válido do servidor. Para voltar ao
   auditor sem exceções, use o despacho E1.2. Para voltar a um binário que só
   aceita v1, arquive v2 offline e inicialize um store v1 novo, com novo
   cadastro e grants. Não coloque uma cópia v1 antiga em atendimento.

4. **Capacidade.** O adaptador admite 4.096 linhas no conjunto da trilha,
   64 KiB por projeção e 16 MiB somados em projeções. A limpeza sob lock nas
   gravações elegíveis descarta intenções pendentes vencidas e trilhas a partir
   de 30 dias da prova. Se não houver lugar para registrar a avaliação, o
   despacho entrega a política sem exceções, desde que a leitura ainda esteja
   autorizada. Revogação atualiza a decisão existente e não depende de espaço
   no teto lógico para uma linha adicional.

## Política de auditoria

O adaptador resolve tudo acima antes de oferecer registros validados à política. A interface proposta aceita exceções vazias por padrão; só o caminho interno de avaliação autorizada passa correspondências validadas para aquela observação, nunca payloads, fixtures de produção ou dados desserializados de relatório. Evitar duas interpretações de `passed`: o formatador do novo fluxo recebe a avaliação já calculada, com instante e correspondências aplicadas. Os formatadores legados continuam avaliando sem exceção.

| Situação, nesta ordem | Resultado |
| --- | --- |
| Resta algum achado que cruza o limiar e não tem exceção válida | `fail`, inclusive com cobertura parcial. |
| Nenhum bloqueante sem exceção, mas falta check obrigatório ou a execução não terminou | `unknown`. A exceção não completa a coleta. |
| Nenhum bloqueante sem exceção e cobertura obrigatória completa | `pass` para a observação e o perfil exibidos. Os achados e as exceções aplicadas seguem visíveis. |

O limiar e a pontuação não são modificados pela exceção. Mudança relevante de política ou semântica do detector exige nova aprovação. A leitura posterior de uma decisão histórica mostra o instante e os vínculos daquele veredito; uma decisão atual requer avaliação atual.

### Invariantes de implementação

- **Sem bypass por formato.** `SecurityReport`, JSON, cache ou payload de cliente não carregam autoridade de exceção. O avaliador só aceita exceções validadas pelo store/autoridade para a observação corrente.
- **Sem ambiguidade.** Se a chave não resolve exatamente um achado do check produtor completo, a exceção não se aplica.
- **Sem downgrade de cobertura.** Exceção não transforma `partial/failed/unavailable` em `completed` e não remove achado do relatório.
- **Sem efeito futuro implícito.** Avaliação histórica não é permissão; cada uso futuro recalcula vínculos e vigência.
- **Sem reaproveitamento cruzado.** Projeto, revisão, exportação, perfil, versão de detector, severidade e fingerprint fazem parte da chave.

## Critérios para revisão e implementação

Exercitar o despacho HTTPS do app com o auditor, o store SQLite e uma exportação temporária reais. Observar os registros e sua aplicação, não só o status HTTP. Usar relógio controlado para expiração e barreiras entre processos para a ordem dos commits.

| Caso | Evidência exigida |
| --- | --- |
| Operadora abre resultado do servidor, confirma motivo/prazo e audita de novo a mesma exportação | Uma decisão durável e rastreável; somente o achado aprovado perde o bloqueio; evidências e contagens não mudam. |
| Cliente envia identidade, fingerprint, relatório ou seletor inventado; usa só grant de leitura, origem errada ou outro transporte | Nenhuma decisão gravada; não divulgar dados de outro escopo. |
| Mesmo commit em outro projeto, outra revisão/exportação, versão de detector/perfil ou severidade diferente; chave não inequívoca | Achado bloqueante continua `fail`. |
| Exceção válida deixa outro achado bloqueante ou falta outro check obrigatório distinto do produtor | Respectivamente `fail` e `unknown`, com cobertura explícita; nunca remover achado para simular aprovação. Check produtor incompleto impede aplicar a exceção. |
| Revogação, expiração, epoch alterado ou grant trocado antes da aplicação | Não suprimir o achado. No teste de concorrência, comprovar ambas as ordens permitidas pelo lock. |
| Replay de intenção, duas confirmações concorrentes, alteração dos dados exibidos, falha de commit, resposta perdida ou reinício | Nenhuma segunda exceção vigente nem renovação implícita; nenhuma decisão com exceção sem registro confirmado. |
| Teto de armazenamento, esquema incompatível, relógio divergente, reboot do host ou restauração | Nenhuma revogação ressuscitada nem perda silenciosa de evidência; recursos afetados ficam indisponíveis ou usam política sem exceções conforme a fronteira da falha. |
| Sentinelas de segredo em conteúdo, nome e erro; leitura de prova fora do grant | Nenhum dado bruto na projeção/decisão, logs públicos ou LLM; acesso não autorizado negado. |

## Revisão de desenho (26/11/2026)

Esta revisão aprova o desenho como base para implementação do **primeiro recorte** de E1.4b, condicionado aos invariantes abaixo. Não é aceite de eficácia, não autoriza habilitar exceções por configuração/fixture e não cobre a migração das aprovações genéricas do chat.

| Ponto | Decisão de revisão | Impacto na implementação |
| --- | --- | --- |
| Fonte de verdade | Store de autoridade + despacho HTTPS interno; relatório/JSON nunca viram prova. | Não desserializar exceção de `SecurityReport`; avaliador recebe correspondências validadas. |
| Vínculo do achado | Chave versionada com projeto/revisão/exportação/perfil/detector/versão/severidade/fingerprint. | Ambiguidade ou check produtor incompleto => exceção não aplicada. |
| Concorrência | `flock` comum + revalidação fresca + commit confirmado antes de publicar efeito. | Testes devem cobrir ordem revogação/avaliação e falha após commit incerto. |
| Custódia | Projeção sanitizada mínima; sem conteúdo bruto, sem caminho, sem segredo, sem payload em log. | Redaction antes de persistir/exibir; negativa genérica fora do escopo autorizado. |
| Recuperação | Sem replay de decisão incerta; restauração/downgrade invalida autoridade antiga antes de reabrir. | Runbook local obrigatório antes de expor serviço após restore. |

A revisão da implementação deve verificar `test_audit_exception_authority.py` e as regressões de autoridade, snapshot, política e redaction. As confirmações genéricas do [chat](../../../kairos_integration/interaction_service.py) e as [aprovações duráveis do runtime](../../../kairos_runtime/service.py) mantêm seus protocolos; a migração do chat terá seu próprio vínculo de efeito, validade e recuperação sem replay antes de fechar E1.4b.

## Revisão técnica da implementação (27/11/2026)

Base verificada: `f5c913d`, com [CI pós-merge e deploy verdes](https://github.com/Patricia7sp/kairos/actions/runs/45942162040). Reexecução focada: 107 testes e 86 subtestes aprovados com `uv run pytest -q tests/test_audit_exception_authority.py tests/test_audit_target_authority.py tests/test_security_snapshot.py tests/test_security_policy.py tests/test_security_redaction.py tests/test_runtime_projects.py`.

O [harness de testes](../../../tests/test_audit_exception_authority.py) exercita o despacho interno com requisição ASGI, cookie, store, auditor e exportação reais. As provas de concorrência usam processos distintos. Isso verifica a fronteira interna; o `https://` no `TestClient` não exercita TLS, e o smoke de produção confirma a saúde do serviço, não o fluxo de aprovação pelo navegador. O adaptador ainda não está ligado a uma rota de produção. Esta revisão foi feita no mesmo contexto da implementação e não substitui a revisão independente do gate I1.

| Pendência | Evidência reproduzida | Critério para resolver |
| --- | --- | --- |
| Distinguir a ocorrência no resumo da confirmação | Duas ocorrências sintéticas da mesma regra em linhas distintas do mesmo arquivo geram chaves e seletores diferentes, mas a [projeção pública](../../../kairos_security/audit_exceptions.py) mostra a mesma regra, severidade, detector e localização de arquivo para ambas. O seletor aleatório distingue os registros ao servidor, mas não explica a diferença à operadora. | Incluir no resumo um discriminador sanitizado da ocorrência, sem nome de arquivo ou conteúdo, e provar que confirmar uma das duas conserva o bloqueio da outra. |
| Preservar o vínculo no histórico durante a retenção | Em uma exportação temporária com relógio controlado, uma avaliação usou a exceção 30 segundos após a prova de origem. Aos 30 dias mais 1 segundo a limpeza removeu a prova original e a decisão em cascata. `open_proof` ainda retornou a avaliação posterior com o ID aplicado, mas a decisão já não existia no store. | Conservar o vínculo histórico sanitizado enquanto a avaliação for consultável, respeitando os limites de retenção e capacidade e sem estender a vigência da exceção. Cobrir a limpeza com relógio controlado. |

O primeiro recorte segue pendente de aceite. Resolver essas lacunas e submeter a evidência à revisão independente antes de tratar o fluxo como aceito; o desenho de migração das aprovações genéricas do chat permanece a entrega seguinte.

### Correções após a revisão técnica

- A confirmação exibe linha e ordinal, inclusive para achados distintos na mesma linha. A correspondência continua usando a chave canônica, e colisões continuam inelegíveis. O teste com três achados da mesma regra confirma um deles e verifica que os demais mantêm `fail`.
- O histórico conserva o resumo sanitizado da decisão no próprio achado da avaliação que a aplicou. A limpeza pode remover a prova e a decisão de origem no prazo original; a avaliação posterior conserva o vínculo até o seu próprio prazo. O resumo conta no teto da projeção. Se ele não couber, o despacho não publica avaliação com exceção.
- O SQLite continua no esquema v2. A mudança de versão da política e da projeção exige nova observação e confirmação para decisões anteriores. O histórico anterior continua datado e mantém os IDs originais, mas indica `historical_links_complete: false` quando não contém os resumos por ocorrência. Nenhum vínculo anterior é reconstruído por suposição.
- Verificação local da correção: 156 testes e 86 subtestes passaram, incluindo as quatro regressões novas e as suítes de autoridade, snapshot, política, sanitização, projetos e aprovações do chat/Web/runtime. Ruff, formatação e `uv lock --check` passaram.
- Revisão independente das correções sobre `f5c913d`: GPT‑5.6 Luna, em sessão isolada e somente leitura, sem achados no escopo. Executou `uv run pytest -q tests/test_audit_exception_authority.py tests/test_audit_target_authority.py tests/test_security_snapshot.py tests/test_security_policy.py tests/test_security_redaction.py`: 87 testes e 86 subtestes passaram. O teto agregado não foi exercitado em caso próprio; a regressão nova verifica o teto por projeção. O parecer não atesta TLS real, navegador ou uso em produção.
- O aceite de eficácia permanece pendente.
