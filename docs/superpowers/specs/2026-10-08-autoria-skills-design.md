# Criação de skills pela CLI com proveniência e rollback

Status: recorte aprovado em conversa em 2026-10-08; especificação escrita para
revisão da usuária antes do plano de implementação.

## Objetivo e entendimento aprovado

Permitir que o usuário instale um `SKILL.md` que escreveu, veja a evidência
da criação e reverta essa criação sem perder alterações posteriores. A skill
criada deve ser consumível pelos caminhos reais `--skill` e `--skills-catalog`.
Origem e recuperação vêm antes de aprendizagem por modelo ou curadoria.

O recorte aprovado compreende `skills add --file SKILL.md`, histórico auditável
e rollback da criação. A operação recusa nomes existentes; registra propriedade
do usuário e testa gravação, concorrência, reinício e consumo pelo adapter.
Nenhuma chamada a provedor é necessária para administrar esses arquivos.

Base: main `0463a5c04aac368bd6aa06f4368fa4645111c257`, que entregou o catálogo
no PR #96. Checkout original preservado. Documento na branch
`feat/autoria-skills`, worktree `.worktrees/skills_authoring`.

## Rastreabilidade

As referências ficam nas specs de origem; novos comentários de código não
substituem essa rastreabilidade.

| Referência | Contrato preservado | Recorte desta entrega |
|---|---|---|
| `_reversa_sdd/skills/requirements.md`, unit skills, RF-04 | Propriedade vem da origem da escrita | CLI fixa `user_foreground` → `Provenance.USER`; conteúdo não escolhe origem |
| Mesma unit, RF-05, RF-06 | Formato validado antes da escrita e barreiras ordenadas | Validação estrita de criação, autorização do ator e contenção antes de publicar |
| Mesma unit, RF-08 | Mutação auditável e reversível | Criação e sua reversão geram entradas duráveis, sem apagar evidência |
| `_reversa_sdd/skills/tasks.md`, T-03–T-06, T-08 | Privacidade, proveniência e ledger precedem automação | Autor não inferido; histórico/rollback antes de `/learn` |
| `_reversa_sdd/domain.md`, §2.3; `kairos_domain.ownership` | Trabalho do usuário protegido de atores autônomos | Origem registrada e ausência de origem tratada conservadoramente como usuário |
| `docs/decisoes.md`, D-09.1–D-09.7 | Sync preserva autoria, diretório inteiro é relevante, autor privado | Não sobrescrever skill existente; não registrar hash de origem bundled para a nova skill |
| `2026-10-08-catalogo-skills-design.md`, RF-09/RF-17 | Snapshot fixado por sessão, cache verificável | Autoria não modifica snapshots nem invalida assets históricos |

RF-01/RF-02 (`/learn`), edição/exclusão genéricas e as quatro barreiras completas
de todas as ações de `skill_manage` permanecem fora deste recorte. Não declarar
esses requisitos integralmente implementados pela criação manual.

## Alternativas consideradas

1. **Serviço de criação com ledger SQLite e publicação de arquivo recuperável.**
   Recomendado: reutiliza migração, backup e reparo do estado, mantendo o formato
   instalado compatível. Exige coordenar banco e filesystem por journal; não há
   transação única entre eles.
2. **Ledger JSONL e blobs independentes.** Aproxima o armazenamento descrito no
   legado, mas cria outro domínio de backup, lock, validação e recuperação. Não
   oferece vantagem para este recorte, que já usa `state.db`.
3. **`/learn` e escrita por modelo nesta entrega.** Dá um fluxo visível mais amplo,
   mas depende desta mesma fundação e de autorização de escrita fora do workspace.
   Fica para etapa posterior, sem acrescentar ferramenta ao core agora.

## Interface pública proposta

```bash
kairos skills add --file ./SKILL.md
kairos skills history --name revisar-docs --limit 20 --json
kairos skills rollback ID_DA_CRIACAO
```

- `add` exige `--file` com caminho de arquivo local; não aceita diretório, URL ou
  `-` como stdin. O nome do destino vem do frontmatter validado. Ler a fonte não
  implica copiar seus anexos ou executar seu conteúdo.
- Publicação em `<KAIROS_HOME>/skills/NOME/SKILL.md`. Recusar qualquer entrada
  já existente para NOME, inclusive diretório vazio, arquivo, link ou skill
  apagada anteriormente mas ainda presente no manifesto bundled.
- `history` aceita nome opcional e limite inteiro de 1 a 100, padrão 20; mostra
  entradas mais recentes primeiro, com ordenação por sequência do ledger.
  Ausência de histórico produz lista vazia em JSON, sem falso erro.
- `rollback` exige ID de uma criação concluída neste home. Reverte apenas essa
  criação, se ainda for a versão comprovada; gera outro ID ligado à criação.
  Repetir uma reversão já concluída retorna a evidência existente sem nova
  remoção ou entrada duplicada. Reverter um rollback não faz parte da interface.
- Saída humana e JSON mostram ID, nome, ação, estado, origem, hash e vínculo de
  reversão. Não imprimir corpo, autor ou caminho absoluto da fonte/home.
- Código 0 somente para efeito concluído ou repetição de rollback comprovada;
  2 para argumento/formato inválido; 1 para conflito, I/O, corrupção ou estado
  pendente que não possa ser reconciliado. Plataforma sem publicação segura
  recusa explicitamente com o código de indisponibilidade 69 já usado na CLI.
- Nenhuma opção `--force`, `--replace`, `--origin` ou inferência de autor.
  `list`/`sync` continuam com seus contratos; os demais subcomandos não
  implementados conservam a recusa existente.

## Validação e fronteira de escrita

1. Ler a fonte por descritores, sem seguir links em nenhum componente; somente
   arquivo regular com um link, UTF-8, até 64 KiB. Limitar a leitura antes de
   decodificar e verificar tamanho/mtime/ctime antes e depois. Arquivo especial,
   hardlink, symlink, substituição observada ou excesso recusa sem escrever.
2. Usar parser estrito existente, com limites de complexidade e sem aliases.
   Reusar `validate_frontmatter(new_skill=True)`: nome kebab-case até 64,
   descrição até 60 e terminada em ponto, versão semver. Corpo não vazio e nome
   exatamente igual ao diretório escolhido. Preservar os bytes UTF-8 originais.
3. A CLI fornece ator confiável `USER_FOREGROUND`; o serviço exige ator
   explicitamente e recusa atores autônomos neste recorte. Nenhum frontmatter,
   parâmetro de ferramenta, variável de ambiente ou contexto copiado pode
   transformar essa origem em `background_review`/`sediment`.
4. Conter acesso ao home/skills por descritores, sem normalizar escapes como
   caminhos aceitáveis. Destinos são derivados só do nome validado e ID opaco.
   Não ampliar ferramentas de arquivo, sandbox, aprovações, MCP ou Web.
5. Recusar destino existente e tombstone bundled antes de preparar a criação;
   publicação também deve garantir ausência de substituição de forma atômica.
   Uma checagem `exists()` seguida de rename que sobrescreve não satisfaz isso.

O serviço pode criar `skills/` e sua área interna privada quando ausentes em
um home válido. Arquivos/áreas privados têm permissões 0600/0700. Não preencher
`author` a partir de login, git config ou ambiente; ausência permanece ausência.

## Persistência e propriedade

Migração aditiva cria tabelas próprias, sem alterar seleção, credenciais,
transcript ou tabelas do catálogo. Os nomes finais e SQL ficam no plano; os
contratos de dados são:

- **Operação imutável:** ID opaco, sequência, home_id, ação `create|rollback`,
  nome, ator/origem confiáveis, instante, ID revertido quando houver, hashes e
  identidade do diretório preparado para distinguir publicação própria.
- **Eventos append-only:** `prepared`, `committed`, `aborted` ou `conflict`,
  vinculados à operação. Estado é derivado de eventos válidos; não reescrever
  a criação antiga para simular que nunca existiu.
- **Conteúdo verificável:** bytes/texto integral da skill, SHA-256 e tamanho;
  no máximo 64 KiB por conteúdo. Rollback conserva a evidência recuperável.
- **Proveniência corrente:** nome/home, criação original e `Provenance.USER`.
  Pode ser projeção reconstruível dos eventos; se materializada, atualizar
  junto com o evento terminal na mesma transação SQLite.

Identidade inclui device/inode do diretório e de `SKILL.md`; comparar também
hash/bytes e inventário completo. Igualdade de nome/hash sozinha não comprova
publicação própria. mtime/ctime/tamanho servem à verificação de leitura em
andamento, sem exigir que um rename conserve ctime de diretório.

Transições de eventos: `prepared` → `committed|aborted|conflict`. Um conflito
pode receber evento terminal somente após reconciliação observar a versão
própria íntegra ou comprovar aborto sem alteração de terceiros. `committed`
e `aborted` são finais; rollback é uma operação nova, nunca atualização desses
eventos. Um erro de leitura temporário não autoriza registrar aborto como se
a publicação estivesse ausente.

Não reutilizar o ledger de obrigações de entrega de mensagens: identidade,
eventos e efeitos são diferentes. Registrar as tabelas canônicas na infraestrutura
de reparo. IDs, relações, estados, hashes e tamanhos são validados antes de
autorizar escrita/rollback; corrupção recusa, sem sintetizar origem ou conteúdo.

Skills existentes sem proveniência continuam consumíveis. Sua ausência de
registro nunca as torna elegíveis a curadoria; eventual consumidor de propriedade
deve assumir USER. Este recorte não as importa nem altera sua origem.

## Banco, filesystem e recuperação

Não prometer atomicidade entre SQLite e filesystem. O contrato é journal
durável, publicação sem sobrescrita e recuperação idempotente baseada no estado
observado. Operações compatíveis neste mesmo home usam um lock exclusivo com
tempo de espera limitado a cinco segundos; timeout recusa sem efeitos novos.

Área privada `<home>/.skill-mutations/` contém staging e arquivos
retirados do catálogo; nomes internos são somente IDs opacos. O serviço não
varre ou remove conteúdo desconhecido. Exigir que área privada e instalação
estejam no mesmo filesystem, recusando com 69 se houver mounts distintos;
não copiar por cima do destino como fallback. O lock vive em
`<home>/.skills-write.lock`, fora do catálogo. `sync_bundled_skills` passa a participar
do mesmo lock nas mutações, preservando todos os resultados e regras atuais.
Leitura do catálogo não ganha um lock ou uma migração adicional por request.

### Criação

1. Validar entrada/ator e obter lock; reconciliar operações pendentes próprias.
2. Preparar diretório privado com o SKILL integral, fechar/fsync arquivo e
   diretório; obter sua identidade. Preparação sem ledger ainda não é criação
   publicada e não autoriza recuperação por descoberta de arquivos soltos.
3. Gravar operação, conteúdo e evento `prepared` numa transação SQLite durável.
4. Publicar o diretório completo no nome final por operação atômica sem
   substituição, no mesmo filesystem; fsync dos diretórios envolvidos.
5. Verificar identidade e conteúdo publicados; registrar `committed` e
   proveniência na mesma transação. Só então devolver sucesso.

Erro antes da publicação preserva destino ausente e registra aborto quando a
preparação durável existir. Depois da publicação, falha de confirmação deixa
operação pendente e retorna erro com ID; não apagar a skill para esconder a
falha. Um comando de mutação posterior reconcilia a publicação própria intacta
e confirma sua evidência. Conteúdo divergente permanece intocado e gera conflito.
Erro de validação não cria registro de criação nem diretório de skill.

### Rollback da criação

1. Validar ledger/conteúdo e obter lock; reconciliar pendências próprias.
2. Exigir a mesma identidade publicada, somente `SKILL.md`, bytes/hash originais
   e ausência de links. Arquivo editado, diretório substituído, anexos adicionados
   ou origem incoerente recusa preservando tudo. Arquivo já ausente por ação
   externa não conta como reversão comprovada.
3. Registrar operação/evento `prepared` de rollback; mover o diretório inteiro
   para destino privado exclusivo, sem sobrescrita; fsync e revalidar o conteúdo
   retirado antes de confirmar.
4. Registrar `committed` e atualizar a projeção de proveniência na mesma transação.
   O nome instalado fica ausente; conteúdo e evidência continuam recuperáveis.

Se a revalidação após o movimento detectar edição concorrente, tentar devolver
o diretório inteiro somente se o nome original continuar ausente. Se o nome
estiver ocupado, conservar o diretório retirado e registrar conflito com ID
acionável; não sobrescrever a nova entrada, descartar conteúdo ou reportar
sucesso. Este é um limite explícito da coordenação com editores externos.

Recuperação distingue staging, publicação e retirada pela operação/identidade,
não só por igualdade de texto. Uma pendência divergente bloqueia novas mutações
do mesmo nome e retorna erro; nomes sem conflito continuam utilizáveis. Estado
em falta não é reconstruído a partir de diretórios que o journal não reconhece.

Para rollback em conflito, o histórico identifica o ID que nomeia o diretório
privado preservado. A documentação orienta salvar esse conteúdo e resolver a
ocupação/divergência antes de repetir o comando. Só reconciliar automaticamente
se identidade, inventário e conteúdo esperados voltarem a conferir; nunca
descartar a versão modificada para atingir o estado esperado.

Histórico é leitura e mostra pendências/conflitos sem reconciliar ou publicar
arquivos. Criação e rollback são os comandos que executam recuperação sob lock.

## Compatibilidade, backup e catálogo

- Migração pode rodar duas vezes; preservar tabelas antigas e abrir o banco com
  a versão anterior do pacote sem alteração dos dados novos por esse código.
- Reparar índices derivados não remove operação, eventos, conteúdo ou origem.
- Backup SQLite inclui o conteúdo e o ledger. Ele não equivale a snapshot dos
  diretórios privados/instalados: restauração do banco deve recusar rollback
  quando a identidade filesystem não conferir. Não repor arquivos ao iniciar
  o Chat nem mudar uma skill atual apenas porque existe blob no backup.
- Sync preserva a nova skill local, que não recebe entrada de origem bundled;
  rollback não altera `.bundled_manifest` ou o opt-out. Tombstones de exclusão
  bundled permanecem respeitados.
- A criação aparece para `--skill` imediatamente e para catálogo de sessão
  nova. Sessão com snapshot anterior continua byte-estável. Rollback não apaga
  páginas persistidas nem reescreve histórico/cache de conversas anteriores.
- Credenciais, isolamento entre provedores e suporte a modelos locais permanecem
  iguais. O consumo de teste usa adapter fictício, sem credencial real.

## Privacidade e observabilidade

Histórico é metadata; corpos são retidos no banco e na área privada para
recuperação e devem ser documentados como parte do backup. Não criar logs com
conteúdo, frontmatter, valores de chaves ou caminhos absolutos. Falhas de I/O
viram mensagem pública própria, sem traceback de biblioteca expondo a fonte.
Conflito identifica nome validado e ID de operação, indicando preservação dos
arquivos e necessidade de resolver o conflito; não afirmar recuperação completa
quando ela não ocorreu.

## Aceitação e evidências exigidas

1. CLI real cria arquivo válido; comprovar publicação, ID/ledger/origem USER;
   `--skill` e `skill_view` entregam texto real ao adapter fictício.
2. Descrição de 61 caracteres, semver inválido, tipos YAML errados, aliases,
   corpo vazio e UTF-8/excesso recusam antes de qualquer criação publicada.
3. Link em cada componente da fonte/home/destino, hardlink, FIFO e troca de
   arquivo durante read recusam sem ler/gravar fora da raiz; FIFO usa timeout.
4. Nome já existente, diretório vazio e tombstone bundled preservados; duas
   criações em processos distintos produzem um único vencedor e nunca substituem
   a versão vencedora. Sync concorrente respeita o mesmo protocolo de lock.
5. Falhas antes/depois de preparar, publicar e confirmar; encerramento real de
   processo nos pontos controlados; reabertura reconcilia uma única vez sem
   reportar sucesso incompleto. Não limitar testes a exceções antes do efeito.
6. Rollback intacto retira do catálogo e cria evidência vinculada; repetição
   devolve a mesma reversão. Editor concorrente, alteração de corpo, anexo,
   diretório substituído ou ocupação do destino preservam todo conteúdo.
7. Campos de arquivo/CLI não escolhem origem; ator autônomo recusado, origem
   ausente em skill antiga equivale a USER e regras existentes não a arquivam.
8. Reabertura mantém ledger/proveniência; adulterar hash, relação ou estado
   recusa operação. Migração, reparo e backup preservam dados canônicos;
   banco restaurado com filesystem divergente não autoriza rollback.
9. Catálogo de sessão existente e assets cacheados persistem após add/rollback;
   nova sessão enxerga a criação, e após reversão deixa de anunciá-la.
10. History humano/JSON, limites, lista vazia, códigos e mensagens públicas
    verificados; stdin não consumido, nenhum corpo/autor/path secreto nos avisos.

Testes comportamentais com dados fictícios e SQLite/filesystem reais, sem ler
código-fonte em testes. TDD, revisão independente e `scripts/ci.sh` completo;
opt-ins Docker offline e skips/deselects explícitos. Merge só com nove checks
remotos verdes; squash, diff pós-merge, deploy e smoke instalado seguem o fluxo
já autorizado. Nenhum `runtime_live` ou provider real sem seus pré-requisitos.

## Componentes previstos e limites do recorte

- `kairos_skills`: validação de criação, publicação por descritores, coordenação
  do journal e proveniência; módulos separados por responsabilidade.
- `kairos_state`: migração e repositório de operações/eventos/conteúdo.
- `kairos_cli`: parser e handlers finos de add/history/rollback.
- Sync: participação no lock de mutação sem reescrever sua política.
- Documentação: comandos, retenção/backup, limites de recuperação e divergência
  deliberada de SQLite em vez de ledger JSONL.

Fora: editar/remover skills arbitrárias, instalação de terceiros, referências
ou scripts na criação, `/learn`, ferramentas de gestão para modelo, Web,
telemetria, curador automático e grafo de aprendizado.

## Próxima etapa

Revisar esta especificação. Após aprovação do documento, escrever o plano com
contratos entre tarefas, testes de falha e método de execução. A aprovação do
recorte já recebida autoriza este documento; implementação começa após revisão
do documento e do plano.
