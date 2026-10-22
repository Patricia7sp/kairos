# Resolução de evidências e adoção de skills — fatia A

Status atualizado em 21/10/2026: A1 integrada pelo PR #102 (`33b6e77`),
A2 pelo PR #104 (`d730822`) e B1 pelo PR #108 (`f9f9a6d`). B2 permanece proposta,
com [desenho detalhado para revisão](2026-10-21-publicacao-skills-b2-design.md).
A base original desta spec foi `c77c92c` (PR #99); os contratos abaixo preservam
a divisão aprovada e os seis apontamentos incorporados do PR #100.

## Objetivo e divisão

Explicar a autoridade sobre uma instalação sem confundir autoria histórica,
distribuição e permissão atual. Permitir adoção explícita e revogável de uma
instalação antiga, conservando bytes, provas e contratos de recuperação.

Dividir a entrega em fases com planos e gates de revisão próprios:

| Fase | Entrega | Gate antes da fase seguinte |
|---|---|---|
| A1 | Resolução e `identify` somente leitura | Revisão do comportamento observado; suíte local e CI exigidos pelo projeto |
| A2 | `adopt`, `unadopt` e integração mínima com remove/restore/history | Revisão de linhagem e recuperação; suíte local, CI e backup/restauração |
| B1 | Política organizacional local e guards ampliados | Spec e plano próprios aprovados; garantia limitada validada |
| B2 | Coordenação das publicações de sync/quarentena | Plano próprio e tabela de recuperação exercitada na imagem |

B1/B2 ficam na [proposta separada da fatia B](2026-10-11-politica-publicacao-skills-design.md).
A aprovação de A não aprova B. Não haverá um plano único para todas as fases.
A execução será multiagente, com propriedade de arquivos por tarefa.

Em A2, validar primeiro a vertical `identify` + `adopt`, em fixtures isoladas.
Essa vertical é um marco de revisão interno: não liberar o comando adopt em
produção antes de unadopt e da integração de recuperação. Uma adoção não será
armazenada como autoridade dormente que uma atualização posterior ativa sem
nova confirmação. A1 pode ser entregue e revisada independentemente.

Não alterar sync, promoção de quarentena, edição por modelo, `/learn`, hub,
administração de organizações ou curadoria automática em A. A troca de inode
pelo sync continua invalidando a prova anterior; A recusa com diagnóstico,
sem prometer corregir publicações ainda fora do journal. Não adicionar tool.

## Rastreabilidade: herdado e não herdado

Fontes em `../hermes-agent/_reversa_sdd/`, relativo ao repositório principal.
A rastreabilidade permanece em `source_ref`, `unit` e `enforced_at` nas fontes.

| Fonte | Contrato herdado |
|---|---|
| `adrs/009-proveniencia-de-skill.md`, Decisão/Alternativas; `skills/requirements.md`, RF-04 | Origem nasce no ato de escrita; frontmatter não escolhe propriedade |
| `skills/tasks.md`, T-04/TT-04/05/06 | Somente background_review cria SEDIMENT auto-curável; cron/subagente não basta |
| `domain.md`, §2.3; `permissions.md`, §3.2/3.4 | Autônomos nunca fazem hard-delete; foreground passa pelos demais guards |
| `skills/requirements.md:54`; `skills/design.md:93` | `_org_mirror_write_guard` em delete/remove_file; não exige veto geral à edição |
| D-09.1–D-09.7, D-CLI.SKILL.AUTHOR/REMOVE | Edição/exclusão local preservadas; manifesto não é autoridade de segurança; SQLite/identidade/recuperação conservadores |

### Não herdado: decisões propostas do Kairos

Registradas em [docs/decisoes.md](../../decisoes.md), com status de proposta:

- **D-CLI.SKILL.PROVENANCE:** identificar/adotar/revogar, três dimensões,
  linhagem revogável e exclusão futura de sync independente do proprietário.
- **D-CLI.SKILL.ORG:** política local futura, reserva conservadora de `_org`
  e ampliação futura do veto a substituições; detalhes pertencem a B.
- **D-09.GATES:** resolver a contagem contraditória pelas ações da matriz.
  A matriz resulta em três guards para delete, pois frontmatter de criação
  só cabe a create; não reescrever o Reversa nem alegar regra nova herdada.

As fontes não definem enrollment, emissor, política administrativa ou override.
A reserva de `_org` também não é formato normativo herdado. Em A, sua aplicação
é somente uma recusa conservadora de alvo nesse namespace; não implementa
mirror, consumo ou política externa e não conclui o guard organizacional geral.

## Resolução e primeira vertical A1

Separar três dimensões em toda resposta e registro novo:

| Campo | Significado |
|---|---|
| `historical_creation_origin` | Ato original comprovado, ou desconhecido; adoção não o inventa |
| `installation_source` | Local/bundle do pacote/aquisição comprovada/desconhecida; declaração não é prova |
| `authorization_basis` | Escrita comprovada, adoção vigente ou nenhuma, sujeita aos vetos conhecidos |

Manter USER/BUNDLED/SEDIMENT para propriedade e curadoria. Fallback USER
protege legado da curadoria, mas não prova autoria nem autoriza remoção.
URL, trust_level e conteúdo não conferem autoridade. Não acrescentar HUB
como proprietário nem apagar desconhecimento histórico após adoção.

```bash
kairos skills identify NOME --json
```

Consultar instalação e provas canônicas por cópia privada estável DB+WAL.
Não criar home, banco, lock, migração, registro ou sidecar. Retornar os três
campos, propriedade, instalação corrente, validade da identidade, proteção
conhecida, `removivel` e motivos. Em A1, não há leitura de política externa.
Para proteção não atestada, informar explicitamente o limite de cobertura;
não produzir `org_guard_complete=true` ou inferir ausência de organização
pelo conteúdo. Não imprimir arquivos, credenciais ou caminhos privados.

Uma origem desconhecida é diagnóstico válido, com `removivel=false`.
Ledger corrupto/incompatível ou identidade impossível de atestar são erros.
Uma instalação sem journal só prova BUNDLED quando corresponde ao inventário
completo do bundle confiável do pacote e ao manifesto v2 íntegro.
Root arbitrária e hash MD5 do manifesto isolado não bastam.

## Adoção e revogação A2

```bash
kairos skills adopt NOME --yes --json
kairos skills unadopt NOME --yes --json
kairos skills unadopt NOME --expect-adoption ID --yes --json
```

Foreground e confirmação são verificados antes de abrir home/banco/criar lock.
Os guards pertencem ao serviço, incluindo chamadas diretas; CLI não é a única
fronteira. Preservar os vetos disponíveis e recusar namespace organizacional,
proteção comprovada no histórico, pendências e árvore insegura. Essa proteção
conservadora não detecta mirrors planos sem evidências nem autentica sua origem;
o guard geral e a continuidade de política configurada dependem de B1.

Adopt registra o ato presente de assumir administração local, com ID A,
home, identidade da raiz (device/inode), inventário e snapshot. Não cria um
create fictício, não concede SEDIMENT, não exige semver de conteúdo legado
e não reclassifica autoria histórica. A prova committed concede USER por
decisão atual, com `authorization_basis=user_adoption` e `adoption_id=A`.

Edições internas preservam essa base; substituição externa da raiz, mesmo
byte-idêntica, invalida autoridade. Uma concessão USER/BUNDLED independente
e vigente não muda por adopt: devolver a prova existente, sem novo ativador.
Restore derivado de adoção revogada não é tal concessão e não impede readoção B.
Repetição de adoção
vigente devolve A; após revogação é necessário novo ato confirmado B.
Não aceitar origem, ator ou force escolhidos pelo modelo.

Unadopt resolve pelo nome a autoridade corrente sob o lock comum. Captura ID
da instalação I, adoção A, linhagem e raiz; revalida esses valores antes de
registrar U. `--expect-adoption A` é uma condição de comparação para scripts:
se a adoção corrente for B, recusar em vez de revogar B. Mesmo sem esse flag,
uma mudança entre resolução e commit recusa; não selecionar outra autoridade
durante a mesma operação. History e identify expõem A, I e sua vigência.

Revogação altera somente autoridade: conserva bytes, modos, snapshots, IDs
anteriores e tombstones. Nome ausente, concessão independente, adoção já
revogada, raiz substituída, linhagem incompleta ou pendência recusam sem efeito.
Revogar concessão sem instalação presente fica fora desta interface.
Rollback de A/U recusa e indica unadopt/adopt; não reverte arquivos.

## Linhagem: tabela de estados e invariantes

| Transição | Instalação corrente | Autoridade | Efeito |
|---|---|---|---|
| Adopt A | I0, raiz D0/N0 | A vigente | Registra assunção; conserva arquivos |
| Remove M | Ausente | A preservada na linhagem de M | Retira I0; snapshot conserva A |
| Restore R de M | I1, raiz D1/N1 | A vigente transportada por R | Publica I1; não cria concessão independente |
| Unadopt nome, esperando A | I1 validada | A revogada por U | Somente journal; conserva I1 |
| Remove após U | I1 | Nenhuma autoridade derivada de A | Recusa, apesar de R committed |
| Adopt B após U | I1 validada | B vigente | Nova decisão foreground; A continua revogada |
| Unadopt esperando A após B | I1 | B permanece vigente | Recusa por comparação; não revoga B |

Invariantes obrigatórias:

1. RESTORE de adotada conserva `adoption_id=A`, histórico e fonte. R comprova
   publicação/identidade; não converte desconhecida em autoria USER independente.
2. Resolver linhagem e vigência antes de autorização. Nunca usar o simples
   fato de R committed como fallback que contorne U.
3. U registra nome/home, A, I corrente e device/inode observados. O conjunto
   precisa corresponder à projeção e à árvore, sob lock, até o commit.
4. U revoga A e autoridade dos seus descendentes restaurados. Não modifica
   eventos de restore nem concessões independentes ou futuras.
5. Restore de snapshot ligado a A revogada recusa: não ressuscitar autoridade
   por outro remove antigo. Uma aquisição/criação independente segue suas
   próprias provas; nunca é assumida por semelhança de nome ou conteúdo.
6. Substituição não comprovada da raiz invalida a cadeia; edição interna não.
   Linhagem ausente/corrupta nunca degrada para fallback autorizador.

## Integração limitada dos escritores

| Superfície em A2 | Mudança |
|---|---|
| Remove | Resolver adoção vigente além das provas atuais; conservar A e fonte no snapshot |
| Restore de remove | Revalidar A e vetos conhecidos; publicar identidade nova conservando a cadeia |
| History/get/pending | Expor todas as famílias e IDs; ordenar pela sequência global |
| Add/rollback de create | Manter suas provas independentes; compartilhar pendências/guards conservadores |

Não refatorar publicação de sync/quarentena em A. Sync continua respeitando
lock, pendências e exclusões; uma prova vencida não é renovada por inferência.
Uma adoção posterior de raiz trocada exige novo ato e passa pelos guards.

Tombstones antigos mantêm significado. Como decisão nova proposta, remoções
futuras de adotadas registram exclusão local de sync mesmo sem prova BUNDLED:
se o bundle passar a oferecer esse nome, não ressuscitar a retirada. Somente
restore committed encerra a exclusão vinculada. Não retroagir sobre remoções
USER antigas nem apagar exclusão por adoção, revogação ou nova criação.

## Persistência e recuperação de A2

Família aditiva de adoção/revogação no SQLite canônico, com timeline comum,
provas/snapshots imutáveis e capability atualizada. Não reescrever schema,
IDs, metadata ou blobs antigos. Clientes antigos recusam capability nova
antes de mutar; backup/reparo incluem todas as tabelas novas.

Adopt prepara snapshot/prova duravelmente, revalida árvore e vetos e conclui.
Recuperação: íntegra e autorizada conclui; ausente aborta sem reconstrução;
divergente gera conflito preservando provas; veto conhecido novo aborta sem
alterar arquivos; impossibilidade de atestação conserva pendência. Unadopt
é transação de journal, sem movimento de arquivos. Pendências de qualquer
família bloqueiam escritores comuns. Não limpar órfãos desconhecidos.

Restore reutiliza recuperação de remoção existente, conservando a linhagem
também nos estados prepared. Se A for revogada ou surgir veto durante uma
publicação inconclusa, não concluir autoridade nem retirar automaticamente
árvore divergente/protegida; preservar versões em conflito. Não inferir
concessão independente para resolver uma pendência.

## Aceitação e gates por fase

A1: comportamento real de identify em legado, USER/BUNDLED comprovados,
conteúdo editado, root trocada, DB/WAL presentes/ausentes e ledger corrompido.
Leitura não cria arquivos, migra banco ou altera DB/WAL; não concede USER por
declaração. Validar subprocesso CLI e conteúdo de diagnóstico sanitizado.

A2: validar a vertical identify/adopt preservando bytes/modos; negar sem
confirmação antes de I/O; repetir A sem outro ativador; chamadas de biblioteca
com os mesmos guards. Exercitar A→M→R→U→remove recusado, múltiplos restores,
B após U e comparação esperando A. Concessão independente não é revogada.
Testar root trocada e linhagem parcial; pendências cruzadas; SIGKILL/fsync nas
fronteiras reais de adoção/restore; restauração de backup com hashes e BLOBs.
Não escrever testes que leiam fonte nem congelar contagens de catálogo.

Códigos: 2 uso/nome inválido; 77 confirmação/ator/veto conhecido negados;
69 primitiva atestável indisponível; 1 ausência/conflito/corrupção/falha.
Sucesso de adopt/U exige registro committed real. JSON informa IDs correntes,
linhagem e vigência; desconhecimento histórico permanece explícito.

Cada fase requer revisão independente, suíte integral local e CI verdes antes
de merge, com opt-ins pertinentes à superfície. A1 não precisa de kill tests
de publicação; A2 não valida policy nem se anuncia como guard org completo.
Catálogos/assets de turnos iniciados e metadata antiga permanecem intactos.

## Próxima etapa

Revisar esta spec A e as decisões propostas, depois aprovar um plano somente
para A1. A2 terá plano/revisão próprios após o gate A1. B permanece proposta
futura e exige aprovação separada. Este PR é documentação; não autoriza
adoção em produção nem mutações em lote.
