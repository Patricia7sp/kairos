# Remoção segura de plugins e remoção reversível de skills

Data: 2026-10-10. Base: `main` em `7de35cae90d4540966160e10c2efc74a75f0d068`.
Estado: especificação escrita aprovada pelo usuário em 2026-10-10; planos de implementação aguardam revisão.

## Objetivo e referências

Completar `plugins remove NOME --yes` e `skills remove NOME --yes` pela CLI.
O usuário consegue retirar uma instalação escolhida, preservando outros
componentes e dados. Toda remoção de skill mantém o conteúdo anterior e pode
ser revertida por `skills rollback ID`. O desenvolvimento usa multiagentes;
a integração, revisão e os gates de entrega têm coordenação única.

Rastreabilidade: `_reversa_sdd/plugins/requirements.md`, RF-04b/RF-07;
`plugins/tasks.md`, T-09/TT-06; `_reversa_sdd/skills/tasks.md`, T-05/T-06/T-08/T-09;
`skills/design.md`, portões de escrita e invariantes de propriedade;
`docs/decisoes.md`, D-10.5; desenho de autoria manual em
`2026-10-08-autoria-skills-design.md`. Os caminhos de spec são relativos a
`../hermes-agent/`, a partir da raiz do repositório principal.

Instalação, atualização, hub/taps, remoção em lote, automação pelo modelo,
curadoria, absorção entre skills e descarregamento de plugins em processos
já iniciados ficam fora desta entrega.

## Contrato dos comandos

| Comando | Efeito e confirmação |
| --- | --- |
| `plugins remove NOME --yes` | Retira e apaga somente `<home>/plugins/NOME`. Preserva `plugin-data/`, configuração, demais plugins e fontes bundled. |
| `skills remove NOME --yes` | Retira somente `<home>/skills/NOME`, gera ID no histórico e guarda snapshot completo antes de qualquer retirada. |
| `skills history [--name NOME]` | Exibe criações, rollbacks, remoções e restaurações numa ordem comum, com seus IDs e estados reais. |
| `skills rollback ID` | Para criação, conserva a reversão atual; para remoção concluída, restaura seu snapshot no nome original, sem substituir instalação existente. |

Sem `--yes`, os comandos de remoção recusam antes de criar lock, abrir banco
para escrita ou modificar arquivos. Não há confirmação interativa implícita.
O rollback de remoção é uma restauração sem sobrescrita e não exige `--yes`.
Ator permitido nos serviços de skills: `Actor.USER_FOREGROUND`, validado antes
de I/O. Esta entrega não publica ferramenta de remoção para o agente.

NOME é o nome de um filho imediato da raiz instalada. Skills usam o validador
kebab-case existente, até 64 caracteres. Plugins aceitam
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, recusando também `..`. Não aceitar caminhos,
categorias, glob, URLs ou escolher o alvo pelo nome interno de um manifesto.
Plugin com manifesto quebrado pode ser removido sem carregar seu código.

Saída JSON de remoção de skill: `nome`, `operation_id`, `action`, `state`,
`removido` e `restauravel`. Somente estado `committed` autoriza informar `removido: true`.
Restauração informa seu próprio ID e o ID de remoção revertido. A saída de
plugin informa `nome`, `removido` e `requer_reinicio: true`; este último
significa que processos já iniciados podem conservar callbacks carregados.
Erro parcial de plugin informa `retirado: true`, `residuo_id` e nunca
`removido: true`. Conteúdo, variáveis de ambiente e erros brutos de backend
não entram nos diagnósticos. Mensagens informam IDs conhecidos de conflito.

Exit codes: 0 para efeito concluído ou repetição comprovadamente idempotente;
2 para uso/nome inválido; 77 para falta de confirmação ou ator não permitido;
69 para garantia necessária indisponível; 1 para ausência, conflito,
configuração/ledger incoerente ou erro operacional. O erro usa stderr e não
imprime o envelope JSON de sucesso.

## Fronteiras de arquivos e proteção de origem

Operações abrem diretórios por descritores, com `O_NOFOLLOW`, validam a cadeia
de ancestrais e reconferem dev/inode. Movimentos usam rename sem substituição
e `fsync` dos pais. Falta de suporte seguro causa recusa, sem fallback para
`resolve()` seguido de exclusão por caminho.

Recusar symlinks, hardlinks de arquivos, arquivos especiais, pontos de
montagem e mudança de identidade em qualquer fronteira/descendente do alvo.
Verificar identidade de montagem observada por descritor, incluindo bind
mounts; igualdade de `st_dev` isoladamente não basta. Se essa observação não
estiver disponível, recusar a operação. Diretórios externos, árvores de
origem bundled e mirrors linkados/montados nunca são alvos.

Remoção de skill admite origem comprovada pelo histórico de autoria vigente
ou por inventário bundled verificável. Ausência no ledger não é prova de
origem do usuário. Origem desconhecida/organizacional é recusada; não deduzir
propriedade pelo frontmatter, nome ou conteúdo. Não adicionar opção de bypass
nesta entrega. Registrar precisamente essa limitação em `docs/decisoes.md` e
na ajuda: o guard organizacional geral do legado permanece pendente.

Prova manual: a projeção aponta para create ou restore concluído neste home;
o diretório atual tem exatamente o dev/inode registrado dessa instalação,
e sua cadeia de pais é segura. Arquivos editados dentro desse diretório não
apagam a origem comprovada; todos os bytes atuais entram no snapshot novo.
Um diretório substituído, mesmo byte-idêntico, perde essa autorização. Restore
conserva a origem registrada pelo remove e vincula as identidades novas da
cópia publicada; não concede origem USER a snapshot originalmente desconhecido.

Prova bundled sem autoria registrada: usar exclusivamente o bundle instalado
junto do pacote Kairos, nunca uma raiz fornecida pela CLI/configuração. O
nome deve corresponder a uma entrada única nesse bundle e no manifesto v2
do home; a árvore instalada deve corresponder byte a byte à árvore bundled
atual, pelo inventário completo SHA-256. O MD5 legado do manifesto não é uma
atestação de segurança. Manifesto v1, malformado, bundle indisponível ou árvore
local divergente não comprovam origem; recusar sem alteração. Portanto,
skills bundled editadas e instalações de hub sem prova anterior podem exigir
uma futura etapa de identificação de origem e não são removidas neste recorte.

Snapshot de skills aceita arquivos regulares binários, arquivos ocultos e
diretórios vazios, incluindo `references/`, `scripts/` e `templates/`.
Não exigir semver ou formato de nova criação para conteúdo legado. Exigir
`SKILL.md` regular na raiz. Limites: 64 MiB de conteúdo por árvore, 16 MiB por
arquivo auxiliar, 64 KiB para `SKILL.md`, 4096 entradas e profundidade 32;
limites incluem arquivos ocultos e são verificados antes da alocação integral.
Para remoção de plugins, o inventário por descritores aceita até 256 MiB,
10000 entradas e profundidade 32, sem manter bytes de todo o plugin em RAM.

Preservar bytes, caminhos relativos, diretórios vazios e permissões POSIX
ordinárias. Recusar bits especiais, ACLs ou atributos estendidos não vazios
que não podem ser preservados por esse contrato. Não restaurar proprietário
arbitrário nem executar arquivos para validar conteúdo. Arquivos privados
usam 0600 e diretórios privados 0700. O snapshot registra as permissões que
serão aplicadas na árvore restaurada.

## Persistência de skills e compatibilidade

Ampliar o journal por migração aditiva. Preservar tabelas, IDs, blobs, eventos
e invariantes atuais de `create|rollback`. Acrescentar tabelas append-only de
operações `remove|restore`, eventos, manifestos e blobs binários de snapshot.
Não falsificar uma criação ou rollback antigo para representar remoção.

Uma timeline append-only comum referencia cada evento antigo ou novo exatamente
uma vez, por sequência inteira global. A migração importa eventos antigos
na ordem existente; triggers alimentam a timeline na mesma transação das
novas escritas. As referências têm constraints de exclusividade, unicidade
e integridade. Não ordenar transições por timestamp. Registrar as novas
tabelas canônicas no mecanismo existente de checkpoint/backup/reparo.

Histórico, pendências e projeção de instalação consultam ambas as famílias
pelo mesmo repositório/coordenador. `add`, rollback antigo, remoção,
restauração e sync compartilham o lock existente de skills. Nenhum escritor
pode ignorar uma preparação/conflito da outra família. A nova versão de
schema impede escrita por executáveis antigos; triggers sozinhos não tornam
segura a projeção de um cliente antigo.

Projeção separa instalação corrente de origem: create ativa sua instalação;
remove concluído a desativa; restore concluído ativa uma instalação com ID
e identidades novos. Add do mesmo nome após remove só é permitido se o
destino estiver ausente, sem pendências e respeitando as reservas bundled.
Rollback de criação exige que essa criação ainda seja a instalação corrente
e mantém as verificações atuais de inode/conteúdo; não apaga restauração ou
reinstalação posterior. IDs antigos continuam legíveis com as mesmas provas.

Snapshots ficam em BLOBs do SQLite e participam do backup existente. Manifesto
determinístico registra tipo, caminho relativo, tamanho, hash SHA-256 e modo;
inclui diretórios vazios. Leitura valida limites, unicidade, caminhos, tipos,
hashes e total antes de reconstruir conteúdo. Bytes anteriores permanecem
no banco após restauração; não mover a única cópia de recuperação para o
catálogo. Não introduzir coleta automática de snapshots nesta entrega.

Uma remoção concluída gera tombstone consultado pelo sync antes de copiar.
Ele sobrevive ao desaparecimento/reaparecimento da skill no bundle e só é
encerrado por restauração concluída. Não apagar o manifesto bundled para
simular opt-out, nem modificar `.no-bundled-skills`. Sync pode consultar o
estado em modo somente leitura; não cria/migra banco só para procurar
tombstones num home antigo. Se houver estado incompatível ou ilegível,
recusa seeding em vez de presumir ausência de exclusões.

## Remoção, restauração e recuperação de skills

Remoção: validar confirmação/ator/nome → adquirir lock → reconciliar IDs
pendentes conhecidos → validar alvo/origem e capturar snapshot estável →
confirmar snapshot e evento `prepared` duráveis → reconferir alvo → mover
árvore original para retirada privada exclusiva do ID → validar novamente
identidade/inventário/bytes → `committed`. A cópia privada original não é o
snapshot imutável: processos com descritores abertos podem continuar editando-a.

Alteração após retirada tenta devolver a versão somente se o nome estiver
ausente, sem substituir nada. Divergência ou nome ocupado conserva as versões
privadas, registra conflito e retorna erro com ID. Não confirmar exclusão
apenas porque o diretório instalado ficou ausente.

Repetição de remove por nome ausente retorna o ID anterior somente se a
projeção corrente for esse remove committed, seu snapshot e provas privadas
continuarem íntegros e não houver pendência ou instalação posterior. Ausência
sem essa prova é erro. Havendo nova instalação, a chamada captura uma nova
operação, sujeita às proteções de origem; nunca reutiliza o ID antigo.

Restauração: validar ID de remoção concluída/snapshot/projeção → construir
nova árvore privada com limites e permissões capturadas → validar e fsync →
registrar preparação com identidades da nova cópia → publicar sem substituir
→ validar cópia instalada → `committed`. Tombstone continua em vigor durante
preparação/falha/conflito. Repetição de uma restauração já concluída devolve
essa evidência sem tocar numa instalação posterior. IDs de restore não
introduzem uma cadeia ilimitada de desfazer/refazer.
Uma restauração nova exige que o ID informado seja a remoção corrente ainda
não revertida daquele nome. Remoções anteriores continuam consultáveis;
não substituem uma versão retirada depois. A recusa informa o ID corrente,
sem descartar os snapshots anteriores.

| Preparação conhecida | Estado observado íntegro | Reconciliação |
| --- | --- | --- |
| Remove | Original instalado; retirada privada ausente | Aborted; alvo preservado |
| Remove | Original retirado no ID esperado; nome ausente; snapshot válido | Fsync e committed |
| Restore | Nova cópia publicada; staging ausente; provas correspondentes | Fsync e committed |
| Restore | Cópia íntegra ainda em staging; nome ausente | Aborted; snapshot preservado |
| Qualquer | Identidade/bytes divergentes, ambas versões presentes, ausência de ambas ou snapshot incoerente | Conflict/erro; preservar evidências |

Recuperação usa somente IDs conhecidos e provas registradas; não adota nem
apaga diretórios privados órfãos por descoberta. Antes de `prepared`, uma
morte deixa a instalação intacta; um staging órfão não autoriza limpeza
genérica. Lock coordena participantes, não bloqueia editores externos.

## Remoção de plugins

Usar lock próprio `.plugins-write.lock`, com prazo limitado e abertura segura.
Não importar plugins, chamar `load_plugins`, hooks, Git ou subprocessos.
Validar/inventariar a instalação; retirar por rename sem substituição para
`<home>/.plugins-retired/ID/tree`, fora da árvore que o loader percorre. Criar
o diretório privado exclusivo `ID` antes da retirada; ele contém as provas e
o destino `tree`, inicialmente ausente. Reconferir
identidade/inventário antes de apagar por descritores somente essa árvore.
`.git/` regular pertence à instalação; um arquivo `.git` é apenas arquivo,
nunca uma instrução para acessar/remover seu destino externo.

Antes do rename, persistir metadata imutável no diretório privado do ID:
ID UUID, nome validado, identidade original do diretório e inventário com
identidades/hashes dos seus descendentes. Não guardar caminhos externos.
Depois de terminar, persistir resultado terminal uma vez, sem reescrever
as provas. Após morte, nova chamada confirmada do mesmo nome consulta esses
registros conhecidos: original íntegro instalado e árvore retirada ausente
permite marcar tentativa abortada; árvore retirada presente informa resíduo
e ID com exit 1. Ambos presentes/divergentes ou ambos ausentes sem resultado
terminal constituem conflito. Não concluir remoção pela simples ausência.
Não retomar automaticamente uma exclusão parcial; preservar seu resíduo.

Exclusão recursiva não é atômica. Falha de cleanup conserva o resíduo e
informa retirada parcial com ID e exit 1. Não prometer restauração completa
de plugin após apagar parcialmente seus arquivos. Diretórios retirados
desconhecidos não são limpos automaticamente. Uma repetição por NOME ausente
não finge sucesso nem apaga resíduos de outro ID.

Dados em `plugin-data/`, configuração e outras instalações permanecem
intactos. Após nova inicialização, loader não descobre o plugin removido.
Callbacks carregados em processos existentes exigem reinício; o comando
não promete hot reload nem reinicia o gateway implicitamente.

## Aceitação e entrega

Testes comportamentais em unittest, sem leitura de código-fonte nem listas
congeladas. Cobrir confirmação/ator recusados sem efeitos, remoção real,
preservação dos demais dados, plugin quebrado removível sem importar módulo,
links/mounts/trocas de inode, limite de lock e falhas reais de I/O no padrão
de monkeypatch já usado pelo projeto.

Skills: snapshots com binários/diretórios vazios/modos; remover/restaurar por
ID; nome reutilizado; add posterior; rollback antigo não apagar reinstalação;
IDs/provas anteriores preservados; migração de banco legado com operações
terminais e pendentes; integridade, checkpoint e clientes antigos recusados;
sync após remoção, desaparecimento/reaparecimento bundled e restauração;
snapshot corrompido e origem desconhecida recusados; morte de subprocesso
real em cada fronteira prepared/rename/commit e reconciliação subsequente.
Plugins: morte antes/depois do rename e durante cleanup, com diagnóstico de
ID recuperável; provar ausência de import e preservação de dados/configuração.
Catálogo novo reflete ausência/restauração; snapshots de turnos já iniciados
permanecem estáveis e não são apagados pela remoção.

Implementação dividida entre persistência/recuperação de skills, filesystem
e remoção de plugins, e integração CLI/sync/testes. Dependências de contrato
são fixadas no plano antes das edições paralelas. Revisão independente por
tarefa e revisão final; TDD e suíte completa local, `scripts/ci.sh`, jobs
remotos de verificação verdes antes de squash merge. Backup consistente
com restauração conferida antes do deploy; Komodo pós-merge, saúde HTTP e
smoke dos comandos na imagem publicada. Sem `runtime_live` ou credenciais
reais para afirmar comportamento não exercitado.
