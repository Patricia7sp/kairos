# B2 — publicação coordenada de skills

Status em 22/10/2026: desenho escrito aprovado explicitamente pela usuária,
com referência ao conteúdo de `7fd4a12` no PR #111. O
[plano de implementação](../plans/2026-10-22-publicacao-skills-b2.md) está para
revisão; não há implementação B2 nem plano de execução aprovado.
Base integrada: `f9f9a6d` (PR #108); a correção do PR #111 permanece dependência.

## Objetivo e continuidade

Atualizar uma skill pessoal por sync ou promoção sem perder a instalação
anterior quando houver recusa, erro de I/O ou interrupção. Explicar qual
publicação corresponde à árvore instalada, sem confundir origem da aquisição,
autoria histórica e autoridade atual. Uma execução que não consiga comprovar
seu resultado preserva as versões e informa conflito ou pendência.

A1 (`33b6e77`) e A2 (`d730822`) já fornecem identificação, adoção revogável e
linhagem. B1 (`f9f9a6d`) fornece política local observada e proteção durável.
A [correção de promoção](../../decisoes.md#d-cliskillpromote--promoção-sem-substituição-destrutiva)
recusa destino ocupado até existir coordenação segura. B2 é o próximo recorte,
com aprovação própria; não retoma tarefas já integradas.

## Rastreabilidade e limites

A [proposta B](2026-10-11-politica-publicacao-skills-design.md#b2-escritores-e-publicação)
é a origem desta ampliação. `_reversa_sdd/skills/requirements.md` RF-04/RF-08,
ADR 009 e ADR 013 fundamentam proveniência, mutação auditável e preservação da
edição local; não especificam o journal SQLite nem este protocolo de publicação.
D-CLI.SKILL.PROVENANCE e D-CLI.SKILL.ORG continuam aplicáveis. Os registros
canônicos A1/A2/B1 são imutáveis.

Não entram download, busca/update do hub, OAuth, `/learn`, curadoria, grafo,
edição genérica de arquivos, instalação de política em produção ou isolamento
contra adulteração pelo mesmo UID. B2 não cria exceção ao veto organizacional,
`force` ou permissão administrativa nova. Publicar um alvo vetado continuará
recusado; um fluxo administrativo de distribuição exige contrato separado.
A remissão histórica de B1 a publicações protegidas foi esclarecida no
checkpoint de 21/10; não constitui autorização futura nem capacidade implícita.

## Alternativas

1. Manter apenas a recusa de destinos ocupados: menor superfície, mas não
   permite atualização e não coordena o sync existente.
2. **Journal comum por publicação, com staging e versão anterior preservada**:
   proposta recomendada; usa as fronteiras de lock e estado já existentes,
   com recuperação explícita e provas duráveis antes dos movimentos.
3. Serviço privilegiado de distribuição: cria outra fronteira de confiança,
   mas exige identidade administrativa, implantação e operação fora do recorte.

## Contexto de publicação e autoridade

A composição, nunca conteúdo/frontmatter ou argumento do modelo, fixa um
contexto imutável por operação: escritor (`bundled_sync` ou `quarantine_promote`),
home observado, nome, ID de publicação, evidência da fonte e autoridade que esse
escritor pode comprovar. O serviço exige a sessão de política B1 correspondente.

Sync recebe a raiz confiável do pacote a partir da composição da instalação.
Uma raiz arbitrária fornecida a uma função não concede BUNDLED. A raiz e seu
inventário são observados antes do staging e revalidados nas fronteiras.
Promoção exige contexto de chamador e recibo sanitizado da aquisição; rótulos
`source` e `trust_level` não provam propriedade nem autorizam substituição.
Ausência de contexto obrigatório resulta em recusa explícita, sem efeitos.

Publicação nova tem identidade instalada nova. Não herda adoção USER anterior
pelo nome, conteúdo idêntico ou inode reaproveitado. Não transforma uma
substituição em RESTORE para transportar autoridade. Registrar apenas provas
sustentadas pelo contexto. Uma mudança administrativa de metadata não cria
instalação. Snapshots de catálogo de sessões iniciadas continuam imutáveis.

## Recibo de aquisição da quarentena

O emissor é o serviço local de aquisição, chamado pela composição confiável
que grava e escaneia a quarentena; B2 não implementa download nem autenticação
de hub. Esse serviço observa a árvore concluída por descritores e registra
um recibo imutável no estado do home antes de disponibilizá-la para promoção.
Recibo não é frontmatter, sidecar fornecido pelo bundle nem JSON aceito do
modelo. A API de publicação recebe uma referência e carrega o registro canônico;
um objeto criado pelo chamador não substitui essa consulta.

O registro contém versão de formato, ID de aquisição, escritor/composição
emissora, home de origem, categoria sanitizada da fonte, autoridade máxima
comprovada do chamador, identidade da raiz de quarentena, inventário de caminhos,
tamanhos e hashes, e resultado do scan vinculado ao digest desse inventário.
Não armazena credenciais, URL privada ou conteúdo da skill como prova de origem.
Origem não autenticada permanece desconhecida; aquisição não concede USER,
BUNDLED, proteção organizacional nem autorização para substituir uma instalação.

O coordenador valida existência e integridade do registro, contexto emissor
permitido, vínculo com home/fonte e equivalência da árvore atestada, tanto na
preparação quanto antes dos efeitos. Copiar um recibo para outra árvore, usar
scan de bytes anteriores ou apresentar somente seu ID não é autorização.
O PREPARED referencia o ID e fixa a prova observada. Uma retomada usa esse
registro e nova política; não emite outro recibo para legitimar o efeito antigo.
Quarentenas legadas sem recibo são recusadas sem efeitos. Não há adoção
retroativa por nome/caminho nem comando genérico para certificar uma pasta
arbitrária. Isolamento contra código malicioso com o mesmo UID continua fora
da garantia, assim como em B1.

## Protocolo proposto

1. Observar política e identidades; adquirir o lock comum do home. Consultar
   pendências de todas as famílias antes de preparar efeitos no nome.
2. Validar a publicação e capturar inventário/identidade da fonte e do destino.
   Preservar edição local, exclusão por usuário, opt-out e tombstones do sync.
3. Preparar staging privado no filesystem do destino e sincronizar seus dados
   e diretórios. Não movimentar a quarentena de outro filesystem diretamente:
   copiar por descritores para staging atestado, mantendo a fonte original
   até conclusão. Links, arquivos especiais, limites excedidos ou montagens
   sem primitivas seguras são recusados explicitamente.
4. Gravar o PREPARED durável com provas e referências de staging/snapshot antes
   de mover qualquer árvore instalada. Persistir por publicação, não por lote.
5. Em substituição, mover o original para snapshot privado sem sobrescrita;
   revalidar política, cadeia e identidades antes/depois de cada movimento.
   Publicar a árvore nova por rename sem substituição e sincronizar os pais.
6. Concluir publicação, prova de instalação e proteção aplicável na mesma
   transação autorizadora. Revalidar a política antes do commit; recibo
   preparado não permite ignorar veto novo ou fonte indisponível.
7. Só retornar sucesso após filesystem e estado terminal duráveis. Conservar
   snapshot anterior. A limpeza da quarentena original exige prova de identidade
   e política corrente; falha de limpeza é resultado parcial explícito, nunca
   sucesso integral. Não repetir a publicação para esconder essa falha.

Não prometer atomicidade entre filesystem e SQLite: o journal permite
reconciliação observacional. Não há atomicidade de lote. Pendência de um nome
bloqueia os escritores participantes desse nome, incluindo add/remove/restore,
adopt/unadopt, sync e promoção; leitura continua diagnóstica, sem migração.
A API pública não pode contornar o coordenador por chamar o helper legado.

## Estados duráveis e transações

Os estados canônicos propostos são PREPARED, COMMITTED, ABORTED e CONFLICT.
PREPARED admite posições intermediárias das árvores; o journal fixa as provas
antes dos movimentos, e a recuperação decide pela observação da tabela abaixo,
sem presumir que um rename ocorreu porque foi planejado. ABORTED exige ausência
de publicação nova ou reposição comprovada do original. CONFLICT preserva as
provas e bloqueia escritores até reconciliação explícita.

Há duas transações SQLite principais, separadas dos efeitos no filesystem:
preparar o registro e as referências duráveis; depois, atestar o resultado e
registrar COMMITTED, nova identidade instalada, provas e proteção aplicável
juntos. Uma recusa após rename e antes desse segundo commit deixa PREPARED
pendente; nunca converte a árvore movida em sucesso nem a apaga como compensação.
O estado anterior de instalação deixa de ser corrente somente no commit que
registra a nova identidade. Leitores diagnósticos expõem a pendência enquanto
o filesystem não corresponde à instalação canônica anterior.

Publicar o manifesto e limpar a fonte têm confirmações duráveis próprias,
posteriores ao commit canônico. COMMITTED com projeção ou limpeza pendente
continua bloqueando escritores do nome; retomar não cria outra instalação.
Sucesso integral exige ambas concluídas ou explicitamente não aplicáveis.
Não há transação SQLite aberta durante cópia, scan ou movimento de árvore.
Os eventos concluídos não são reescritos para atualizar essas confirmações.

## Manifesto do sync e projeções

O manifesto legado também participa do protocolo: registrar no PREPARED o
valor anterior e o esperado para o item, com a prova da observação de origem.
O journal de publicações concluídas passa a ser a fonte canônica das novas
entradas; o manifesto continua uma projeção compatível, sem produzir autoridade
por si só. Entradas legadas permanecem legíveis e não ganham autoria retroativa.

Após o commit canônico, publicar a projeção por escrita atômica e fsync sob o
mesmo lock. Registrar a conclusão dessa projeção duravelmente. COMMITTED com
projeção pendente bloqueia novos escritores do nome e retorna resultado parcial;
retomada reconcilia a projeção com o registro canônico sem mover a árvore de novo.
Divergência frente à observação preparada é conflito, sem sobrescrever edição
externa por suposição. Alterações de outros itens já concluídos são preservadas.
Conclusão integral do item exige instalação e projeção coerentes. Opt-out,
exclusões e tombstones continuam verificados antes de novos efeitos.

## Tabela de recuperação

Nomes e bytes não bastam: cada estado requer home, árvore, device/inode,
inventário e referências duráveis esperados. A retomada ocorre sob o lock,
com contexto compatível e nova observação de política. Não reutiliza autorização
antiga para contornar veto atual.

| Publicação | Estado observado | Resultado permitido |
|---|---|---|
| Inicial | Destino ausente; staging esperado | Abortar preservando ausência; limpar apenas staging comprovado e permitido |
| Inicial | Destino novo esperado; staging ausente | Concluir somente com provas e política válidas |
| Substituição | Original no destino; staging novo esperado | Abortar preservando original |
| Substituição | Destino ausente; original privado e staging esperados | Repor original e abortar se guards permitirem; caso contrário, conflito com ambas as versões conservadas |
| Substituição | Destino novo e original privado esperados; staging ausente | Concluir se autorizado, preservando snapshot anterior |
| Ambas | Veto novo, fonte indisponível, identidade divergente, duplicação ou prova incompleta | Preservar versões, registrar pendência/conflito e bloquear escritores; sem exclusão compensatória de árvore protegida |

Órfão desconhecido não é adotado, apagado nem convertido em autoria para
resolver o conflito. Não retirar automaticamente uma árvore recém-protegida.
Distinguir COMMITTED com limpeza pendente de publicação ainda não autorizada;
retomada de limpeza não renova prova, ID ou autoridade da instalação.

## Persistência, migração e compatibilidade

Adicionar família de publicações à timeline e projeções existentes, com ID,
escritor, evidência sanitizada da fonte, identidade/inventário observado,
staging/snapshot, recibo preparado, estado terminal, projeção e limpeza pendentes. O plano
fixará o próximo schema aditivo e suas assinaturas após revisão desta spec.
Não renumerar nem reescrever eventos antigos.

Todos os escritores participantes precisam entender a família e seus bloqueios;
clientes antigos devem recusar escrita em banco novo. Leitores reconhecem homes
anteriores sem migrar nem gravar. Migração pertence à composição dos escritores,
com política observada antes de abrir estado mutante, como documentado para B1.
Backup/reparo incluem a nova família e suas pendências. Projeção estruturalmente
incompleta recusa; uma pendência válida é preservada como pendência, sem
convertê-la em publicação concluída. Testar
restauração em home isolado sem transferir bindings por nome ou caminho.

## Backup e restauração das árvores privadas

Staging e snapshots pertencem a uma área privada dentro do home, no mesmo
filesystem do destino. O journal usa referências relativas validadas a essa
área; nomes absolutos, escapes e links não servem para reencontrar artefatos.
O backup integral inclui árvore instalada, manifesto, recibos de aquisição,
journal e todos os artefatos privados referenciados, inclusive os de operações
pendentes e snapshots conservados de publicações concluídas. Fonte de
quarentena externa ao home não entra implicitamente no arquivo de backup.

O backup adquire o lock comum durante a observação do journal, snapshot SQLite
e leitura das árvores participantes; valida o inventário antes e depois do
arquivamento e recusa alterações concorrentes ou artefato obrigatório ausente.
Preservar o backup anterior e recusar o novo arquivo incompleto. Essa garantia
vale para escritores cooperativos e observações verificadas; não promete
detectar troca ABA por um adversário com o mesmo UID.

Restaurar o arquivo integral em home isolado conserva eventos e provas
históricas, mas não reutiliza device/inode antigos como identidade corrente.
A validação de restauração relaciona os artefatos pelos inventários e registra
uma observação local distinta para diagnóstico; não transfere binding,
proveniência ou autoridade pelo caminho de destino. Recuperar pendência exige
nova política, contexto compatível e uma operação explícita que ateste as
identidades locais antes de qualquer efeito. Cópia externa byte-idêntica não
é, por si só, restauração reconhecida nem instalação autorizada.

Um restore somente do banco nunca é recuperação integral de publicação.
Referências sem árvores, recibo ausente, inventário divergente ou origem externa
indisponível permanecem diagnosticados e bloqueiam os nomes afetados; não
fabricar árvores nem concluir limpeza. Reparo de projeções SQLite não remove
essas pendências. A limpeza de quarentena externa após restauração exige a
identidade original ainda comprovada; se ela não existir, conserva a pendência
em vez de escolher outra pasta pelo nome.

## Erros, diagnóstico e desempenho

Propagar falhas com categoria e ID da publicação. Logging estruturado inclui
escritor/ação, fase, categoria e motivo sanitizado; não inclui caminho privado,
credencial, conteúdo da skill ou rótulo de organização. JSON não apresenta
pendência ou conclusão parcial como sucesso integral. No sync, conservar os
resultados reais por item e distinguir recusado, pendente e concluído.

A issue #109 trata validações repetidas. Medir antes de escolher cache. Qualquer
reuso exige limite e invalidação comprováveis para a observação do banco; não
reutilizar validação entre operações nem suprimir revalidações de política e
identidade antes dos efeitos. Otimização geral fica em recorte próprio, sem
condicionar segurança a um número de benchmark.

## Aceite obrigatório e próxima etapa

- Recibo ausente, forjado, de outra árvore/home ou com scan desatualizado recusa
  promoção; emissão e validação usam aquisição e publicação reais.
- Atualização legítima renova somente as provas sustentadas por seu contexto;
  adoção antiga não atravessa substituição. Raiz arbitrária não concede BUNDLED.
- Publicação inicial e substituição preservam versões em falha de movimento,
  troca byte-idêntica de raiz, política alterada e quarentena em outro filesystem.
- SIGKILL antes/depois de journal, cada rename, fsync e commit exercita a tabela
  pela API real e em imagem real, incluindo substituição e limpeza pendente.
- Veto novo, fonte indisponível, resíduos desconhecidos, corrupção de projeção e
  clientes antigos recusam efeitos; todos os escritores respeitam pendências.
- Interrupção entre commit e manifesto retoma só a projeção, sem republicar
  nem apagar alterações de outros itens; falha parcial não aparece como sucesso.
- Edição local, exclusão por usuário, snapshots de sessão e opt-out sobrevivem.
  Backup/restauração e reparo exercitam a família nova sem reescrever eventos,
  incluindo staging/snapshot pendentes, artefato ausente, banco isolado e fonte
  de quarentena externa indisponível; nenhum caso ganha binding pelo destino.
- Revisão independente e integração pelo Root; suíte integral local, Docker e
  jobs remotos verdes antes de merge. Runtime live só com credenciais/opt-in.

O aceite escrito desta spec foi registrado em 22/10/2026. O próximo gate é a
revisão do plano de execução, com arquivos, interfaces, migração e casos
RED/GREEN. Não há código B2 nem autorização de merge/deploy neste registro.
