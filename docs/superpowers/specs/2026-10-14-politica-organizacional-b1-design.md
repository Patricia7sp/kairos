# B1 — política organizacional nos serviços de skills

Status: spec aprovada pelo usuário em 2026-10-14; implementação pendente.
O plano B1 terá revisão própria antes da execução. B2 permanece separado.

Complementa a [proposta B1/B2](2026-10-11-politica-publicacao-skills-design.md)
e separa B1 da coordenação de publicação de B2. A2 foi integrada por
`d730822`; suas provas de origem e adoção continuam sendo contratos próprios.
Este documento define decisões novas sob D-CLI.SKILL.ORG. Não altera as specs
Reversa nem atribui a elas um formato de política que não especificam.

## Objetivo e critério de sucesso

O operador consegue vincular uma skill de um home a uma organização e impedir
remoções ou substituições destrutivas pelos serviços participantes. Configuração
indisponível, trocada durante a operação ou inválida recusa efeitos. Um veto
duravelmente registrado não desaparece porque a entrada foi retirada da política.

A garantia é continuidade entre operações cooperativas. Mesmo UID com escrita
em código, banco ou configuração pode contorná-la. Não há autenticação de
organizações, isolamento do runtime ou instalação de política em produção.

## Alternativas e escolha proposta

1. **Fonte local explícita e guard compartilhado:** preserva a composição dos
   serviços e permite revalidar a fonte antes dos efeitos. É a opção proposta.
2. **Sidecar/frontmatter na skill:** colocaria a decisão no próprio conteúdo
   removível; não satisfaz continuidade nem distingue origem de autoridade.
3. **Serviço administrativo com outro UID e assinatura:** oferece outra fronteira
   de confiança, mas exige enrollment, operação e implantação fora deste recorte.

## Configuração e formato

A composição recebe um contexto imutável com modo `none` ou `file`. `file`
exige caminho absoluto para um JSON UTF-8 regular, sem symlink em sua cadeia,
sem hardlinks e com tamanho máximo de 1 MiB. `none` é escolha explícita da
composição; erro ao carregar `file` nunca produz `none`. Construtores de serviços
mutantes exigem contexto, e os chamadores internos e testes são atualizados.
Não haverá parâmetro `force`, seleção de emissor ou override por ferramenta.

Na CLI, uma variável de ambiente de inicialização
`KAIROS_SKILL_ORG_POLICY` seleciona o caminho. Ausência seleciona `none`;
valor vazio ou relativo é configuração inválida. Ler a variável uma vez na
composição, antes de abrir estado para escrita; não reler durante uma operação.
O home seleciona o alvo, nunca a referência de política. Isso é disciplina
cooperativa: alterar o ambiente em outra execução não cria isolamento por UID.

Formato proposto:

```json
{
  "version": 1,
  "bindings": [
    {
      "home": "/srv/kairos/equipe",
      "name": "procedimento-interno",
      "organization_id": "equipe-exemplo",
      "active": true
    }
  ]
}
```

Rejeitar chaves desconhecidas e duplicadas, inclusive no parser JSON, versão
diferente de inteiro 1, booleanos no lugar de versão, bindings fora de lista,
tipos incorretos e duplicatas de `(home, name)`. `active` é booleano obrigatório:
inatividade não libera o veto, apenas descreve o binding.

`home` é absoluto, sem `..`, com grafia normalizada e sem componentes symlink;
precisa coincidir com o home observado do serviço. Reutilizar a identidade
`catalog_home_id` depois dessa validação, sem mudar IDs de ledgers antigos.
Não resolver aliases de symlink para transferir bindings entre homes.
`name` usa o validador público existente; `organization_id` é um rótulo ASCII
de 1–64 caracteres `[a-z0-9][a-z0-9-]*`, sem semântica de credencial.
Bindings para homes ausentes podem existir; não criam diretórios.

`skills/_org/` e descendentes permanecem reservados em ambos os modos,
independentemente de sidecar, binding ou estado ativo. Caminhos de operações
em arquivos devem verificar componentes antes de reduzir o alvo a um nome.

## Observação e decisão

Um guard compartilhado recebe contexto, home, nome e ação. Abre a fonte por
descritores, captura cadeia de diretórios, identidade/stat e digest dos bytes,
valida o documento e guarda a observação até a conclusão da operação.
Revalidar cadeia, identidade, conteúdo e contexto antes de cada movimento,
exclusão ou conclusão que publique autoridade. Troca byte-idêntica de inode
também é divergência. Uma nova execução pode aceitar uma nova fonte válida;
troca durante a execução corrente exige tentativa nova.

O veto é a união de namespace reservado, binding corrente e proteção
registrada. Não depende de frontmatter, manifesto bundled, origem declarada,
adoção USER ou confirmação `--yes`. Mais de uma organização pode permanecer
registrada para um nome ao longo do tempo; nenhuma nova entrada apaga a antiga.

Erros públicos usam categorias existentes: `denied` para veto observado,
`input` para seleção/formato inválido, `unavailable` para fonte configurada
inacessível e `conflict` para mudança durante a observação. Corrupção de estado
é `corrupt`. Diagnósticos não incluem caminho privado, conteúdo da política
ou exceção de backend. Logging estruturado contém apenas ação, categoria,
ID de operação quando existente e motivo sanitizado; falhas são propagadas.

## Persistência e compatibilidade

Adicionar uma família de proteção no próximo schema aditivo do estado, com
eventos imutáveis de home, nome, organization_id, ID da instalação/operação,
digest da política e referência da observação. A referência não contém o
caminho da fonte. Projeção e eventos devem concordar; corrupção ou família
parcial bloqueiam escritores. Leitores não migram nem gravam observações.

Registrar proteção somente junto a uma instalação efetivamente concluída
por escritor participante. O registro precisa estar durável antes do commit
que torna a instalação utilizável, com vínculo preparado no journal para
recuperação. Não alterar eventos A1/A2 nem converter proteção em proveniência
ou concessão de autoridade. Um veto durável é por home/nome e sobrevive à
troca de inode e à ausência posterior de política, inclusive modo `none`.

Uma consulta, recusa ou leitura de binding de instalação legada não cria
registro. Se o operador retirar esse binding antes de existir uma instalação
registrada por escritor participante, não há continuidade histórica adicional.
Essa limitação deve aparecer no diagnóstico. Não há `unprotect` nesta fase.

Backup/restauração e validação de capabilities precisam incluir a nova família;
restauração parcial é recusada. Homes antigos sem essa família continuam
legíveis com cobertura declarada; escritores migram pelo fluxo existente.

## Cobertura de operações e divisão B1/B2

| Caminho | Comportamento B1 |
|---|---|
| Remoção de skill; rollback que retira criação | Revalidar veto antes de preparar e antes de retirar a árvore |
| Recuperação que retiraria ou recolocaria árvore | Revalidar política corrente; sem autorização, preservar versões e pendência |
| Restauração autorizada em destino ausente | Manter autoridade exigida por A2 e registrar proteção aplicável na conclusão |
| Criação manual em destino ausente | Aplicar reserva de namespace; binding não concede autoria, mas gera proteção da instalação concluída |
| `identify` | Informar veto corrente/registrado e cobertura; sem escrita e sem fonte privada |
| Adoção e revogação USER | Não conceder autoridade destrutiva sobre alvo vetado; revogação USER continua sem remover veto |
| Sync e promoção | Guard obrigatório nas APIs públicas, inclusive chamada direta; recusar publicação em alvo com binding ou proteção registrada até B2 |

A última recusa inclui destino ausente: B1 não publica uma instalação
organizacional por um escritor que ainda não sabe registrar proteção no journal
comum. Sync de alvos pessoais mantém seu comportamento atual. Não relatar
sucesso de cópia/atualização se o guard recusou. Um lote pode ter passos anteriores
concluídos; o resultado deve explicitar a falha sem afirmar atomicidade global.

Hoje não existe operação pública de skills `remove_file` no Kairos. O guard
deve ter contrato testável para essa ação, mas B1 não cria uma nova ferramenta
para satisfazer a matriz herdada. Não declarar cobertura de um caminho público
ausente. `edit`, `patch` e `write_file` in-place permanecem fora do veto novo.

B2 continuará responsável por permitir publicações protegidas de forma
coordenada, registrar publicações e provas de origem, substituir sem apagar
diretamente destino ocupado e recuperar as fronteiras de staging/rename/fsync.
B1 não declara corrigida a coordenação geral de publicação de alvos pessoais.

## Recuperação

Toda recuperação recebe o mesmo contexto da composição, adquire o lock comum
e carrega a política corrente. Nenhum callback de rollback recebe um contexto
implicitamente sem política. Se a fonte ficar indisponível, não executar limpeza
ou restaurar uma árvore por suposição: manter pendência e erro explícito.

Veto novo após movimento impede conclusão autorizadora e também impede
compensação destrutiva automática. Preservar original, staging e destino
observados e marcar conflito recuperável com identificação sanitizada. Cleanup
de staging privado só é permitido com identidade preparada comprovada, sem
retirar alvo protegido. Retentativa exige reobservar política e identidades.

## Aceite e ordem do próximo plano

O plano deverá organizar quatro entregas dependentes: parser/contexto/guard;
schema e continuidade de proteção; integração de serviços, leitores e
recuperação; integração dos escritores legados e aceite completo.
Cada entrega terá testes comportamentais e revisão; não ler fonte em testes.

Casos obrigatórios:

- Fonte ausente, ilegível, corrupta, links, duplicatas JSON, versão/tipos
  incorretos; recusa sem apagar árvore ou criar registro de instalação.
- Mesmo nome em homes diferentes; home por symlink recusado; binding inativo;
  `_org` protegido sem arquivo de política e sem sidecar.
- Troca byte-idêntica da fonte, mudança no ancestral e alteração após preparo;
  revalidar também em chamadas diretas de serviços e APIs de publicação.
- Proteção registrada continua após remoção do binding, modo `none`, troca
  de inode e revogação USER; consulta/recusa isoladas não geram persistência.
- Falha entre registro preparado e conclusão, recuperação com política nova,
  fonte indisponível e compensação vetada; preservar versões sem falso sucesso.
- Backup completo e restauração; família parcial/corrupta; leitura de home
  antigo sem migração; regressões de autoridade e linhagem A2 preservadas.
- Sync/promoção vetados preservam destino e quarentena; alvos pessoais mantêm
  contratos existentes. Nenhum teste simula guard apenas no adaptador CLI.

Antes de merge: revisão independente obrigatória, `scripts/ci.sh` integral,
imagem e integrações aplicáveis, jobs remotos verdes. Para esta etapa documental
e a medição A2, não afirmar que esses gates de implementação já foram cumpridos.

## Gate para prosseguir

Revisar e aprovar esta spec escrita antes de produzir o plano B1. Revisar o
plano antes de implementar. A aprovação não instala política em produção e
não aprova B2 ou isolamento contra o usuário de execução.
