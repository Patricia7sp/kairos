# Skills explícitas nos turnos da CLI

Status: proposta escrita para revisão da usuária; implementação ainda não iniciada.

## Objetivo e entendimento aceito

A usuária autorizou detalhar o próximo recorte do roadmap: usar skills nos
turnos do Chat. O resultado esperado é que uma skill instalada e selecionada
explicitamente chegue ao modelo e possa orientar uma tarefa, preservando
workspace, aprovação de ferramentas e isolamento entre execuções.

Este recorte começa pela CLI com `--skill NOME`, repetível. Não seleciona
skills automaticamente e não adiciona ferramentas ao core. É uma extensão
da composição e persistência do turno compartilhado; não é integração de
skills nativas do Codex/Agent Runtime.

Base examinada: `main` em `52e0de0f31d92970774fbf045e8ff0ce00c20245`, após
os PRs #90–#93. Checkout original preservado; documentação em worktree
`.worktrees/skills_runtime`, branch `feat/cli-skills`.

## Rastreabilidade e recorte das referências

| Referência | Contrato relevante | Aplicação neste recorte |
|---|---|---|
| `../hermes-agent/_reversa_sdd/skills/requirements.md`, unit `skills`, RF-05 | Frontmatter validado | Reutilizar validação de leitura, incluindo descrição até 60 caracteres |
| Mesma spec, RF-09 e RF-17 | Índice compacto estável e corpos sob demanda | Primeiro caminho de seleção explícita; índice automático, cache em disco e carga de referências permanecem pendentes |
| `../hermes-agent/_reversa_sdd/skills/design.md`, estado interno e dependências | Corpo em `SKILL.md`, runtime em `<home>/skills` | Ler somente skills já instaladas no home usado pela CLI |
| `../hermes-agent/_reversa_sdd/adrs/009-proveniencia-de-skill.md` | Proveniência decide propriedade, não conteúdo | Consumo não cria, reclassifica, poda ou altera skills |
| `docs/decisoes.md`, D-09.1 a D-09.7 | Preservação de autoria, formato e contenção | Não sincronizar durante um turno; leitura sem seguir links |
| `docs/decisoes.md`, D-CLI.WS e D-CLI.AUTO | Workspace por turno e aprovação explícita | Contexto de skill não muda schemas, permissões ou decisões |
| `kairos_state/repositories/messages.py`, `MessageRepository.append_turn_message` e `for_api` | Separação `content`/`api_content` e histórico ativo | Preservar o pedido exibido e persistir a versão efetivamente enviada ao provedor |

O recorte não será apresentado como conclusão integral de RF-09/RF-17 nem
como entrega do ciclo de aprendizado da unit `skills`.

## Alternativas consideradas

1. **Selecionar explicitamente e persistir o conteúdo no turno — escolhida.**
   Reutiliza a CLI, o histórico e os adapters atuais. O usuário decide o que
   envia ao provedor e não paga por um catálogo completo em cada request.
2. **Injetar o índice de todas as skills e carregar por ferramenta.** Aproxima
   o comportamento completo da spec, mas exige contrato de descoberta,
   cache por conversa e uma via de leitura fora do workspace. Fica para
   outro recorte, após o consumo explícito estar verificado.

## Comportamento da CLI

Exemplo, usando uma skill fictícia instalada:

```bash
kairos run --session trabalho --skill revisar-docs \
  "Revise o texto abaixo: ..."

kairos run --session trabalho --tools --workspace /projeto \
  --skill revisar-docs --skill padronizar-texto \
  --allow-tool edit_file --json "Revise README.md"
```

- `run` e `chat` aceitam a opção quando há uma mensagem para um único turno.
  O nome é o diretório imediato da skill em `<home>/skills/NOME/SKILL.md`.
  É kebab-case, até 64 caracteres, e deve coincidir com `name` do frontmatter.
  Não são aceitos caminhos, categorias, globs ou aliases.
- A opção não exige `--tools`: carregar contexto é independente de executar
  ferramentas. `--workspace` e `--allow-tool` mantêm seus requisitos atuais.
- A ordem das opções é preservada. Repetir um nome carrega-o uma única vez,
  na primeira posição em que apareceu. A lista é copiada antes de compor o
  serviço; alterações na lista do chamador não mudam o turno.
- Uso sem mensagem ou em uma sessão Agent Runtime retorna erro de uso,
  código 2, antes da composição do serviço e de qualquer chamada de provedor.
  O roteador também recusa snapshots de skills em um envelope de runtime,
  para não depender apenas da validação da CLI.
- Nome ausente, arquivo inválido ou leitura recusada retornam código 2 com
  mensagem acionável. Se qualquer skill falhar, o conjunto inteiro é
  recusado: nenhum turno é enviado ou persistido parcialmente.
- JSON continua emitindo os eventos NDJSON existentes. Erros e um aviso
  curto com nomes carregados vão a stderr, sem imprimir corpos ou caminhos
  internos. Sem `--skill`, comportamento e payload permanecem iguais.

Somente o diretório instalado é fonte de leitura. Não há fallback para
`skills/`, `optional-skills/`, quarentena ou outro perfil. O diretório plano
é o contrato hoje usado por `kairos skills list` e `sync_bundled_skills`.
Este recorte não corrige nem amplia a descoberta de bundled por categorias.

## Leitura e snapshot

Um módulo pequeno em `kairos_skills/runtime.py` resolve a seleção e devolve
uma tupla de snapshots imutáveis. Cada snapshot contém nome, descrição,
versão declarada, texto completo de `SKILL.md` e SHA-256 dos bytes lidos.
O hash identifica o conteúdo entregue; não substitui proveniência ou o
hash de origem do manifesto de sincronização.

A leitura usa UTF-8 estrito e os parsers existentes. Antes de coerções,
verifica tipos dos campos conhecidos e a estrutura de `metadata`: um
inteiro em `name`, por exemplo, não vira um nome válido por conversão para
texto. Depois aplica `validate_frontmatter(new_skill=False)`, preservando
a compatibilidade de leitura com versões antigas. Corpo vazio é recusado.
Erros não reproduzem YAML, corpo, valores privados ou traceback em stderr.

Limites explícitos da política: até oito nomes únicos por turno, até 64 KiB
por arquivo e até 128 KiB de arquivos selecionados no total. A leitura é
limitada e recusa o excesso; não trunca conteúdo silenciosamente. Limites
são medidos nos bytes UTF-8, antes da serialização para o request.

O home explicitamente resolvido pela CLI delimita a fonte. Abaixo dele,
`skills`, o diretório da skill e `SKILL.md` são abertos por descritores,
sem seguir links (`O_NOFOLLOW`). O arquivo usa abertura não bloqueante
(`O_NONBLOCK`), para que um FIFO seja recusado sem aguardar um escritor.
`fstat` exige arquivo regular com uma
única ligação; hardlinks, symlinks e arquivos especiais são recusados.
O arquivo aberto é a fonte do snapshot mesmo se seu nome for substituído
durante a leitura. Mudanças observadas de tamanho/mtime no descritor ao
longo da leitura resultam em recusa; não há promessa de transação entre
arquivos editados simultaneamente por um escritor externo.

Nenhum script é importado ou executado. `references/`, `scripts/` e
`templates/` não são carregados, copiados ou montados na sandbox. O usuário
deve colocar insumos adicionais no workspace pelos mecanismos existentes.
Não se cria exceção no leitor de arquivos para o diretório de skills.

## Fluxo e contratos entre componentes

1. A CLI valida a combinação de opções, resolve a seleção e captura os
   snapshots antes de compor o serviço. Sem seleção, não varre nem lê o
   catálogo.
2. `InteractionEnvelope` recebe `skills`, uma tupla de snapshots, vazia por
   padrão. A fronteira copia e valida tipos, limites e nomes únicos; os
   dados não entram em `parameters` do adapter. Não são aceitos objetos
   mutáveis como snapshots. Nome, descrição, versão, texto e hash são strings;
   o hash deve corresponder ao texto completo codificado em UTF-8, com os
   mesmos limites aplicados pelo loader. O serviço usa os dados recebidos e
   não reabre arquivos.
3. O serviço monta uma representação determinística do contexto: um
   preâmbulo fixo identifica as skills selecionadas como procedimentos de
   referência, seguido de JSON contendo nome, descrição, versão, hash e
   texto, e então o pedido do usuário. A serialização escapa os valores
   fornecidos pelas skills; não interpreta marcadores do corpo.
4. `_persist_user` conserva `content=envelope.content`. `api_content` guarda
   a representação completa usada pelo modelo. `display_metadata` guarda
   uma lista `skills` com nome, versão e hash, além da seleção de provedor
   já existente; não duplica o corpo no metadata. Sem skills, o caminho
   antigo permanece byte a byte.
5. `_history` e os adapters recebem essa mensagem no papel **user**. Uma
   skill não cria uma mensagem system nem altera a hierarquia do serviço.
   Rodadas de ferramentas e tentativas reutilizam o conteúdo persistido,
   sem resolver novamente a seleção ou reler arquivos.

O loader tem a responsabilidade de leitura; o snapshot, de congelamento;
o serviço, de serialização/persistência; a CLI, de seleção e feedback.
Nenhuma dependência nova ou migração de banco é necessária.

## Continuidade e isolamento

Editar ou apagar o arquivo após a captura não modifica o turno em andamento
nem as mensagens antigas após reiniciar o serviço. Uma nova seleção em
outro comando carrega os bytes atuais. Falhas de autenticação e retries
mantêm a credencial e o contexto fixados pelas regras existentes.

Sem `--skill` no próximo comando, não há nova ativação ou releitura. O
conteúdo anteriormente enviado continua no histórico ativo, como qualquer
outra mensagem; omitir a opção não apaga retroativamente esse histórico.
Compactação e rewind preservam sua semântica atual. Para uma conversa sem
o contexto anterior, usa-se outra sessão.

Não há cache global, alteração de configuração, sync, escrita no diretório
de skills, migração de pool de credenciais ou estado de permissão por skill.
Dois homes/sessões/provedores não compartilham snapshots por estado global.

## Permissões e observabilidade

O conteúdo pode recomendar comandos, mas os schemas e os executores atuais
continuam impondo as capacidades disponíveis. Contexto não autoriza
`write_file`, `bash`, MCP, Git ou agenda. Escrever exige a aprovação humana
ou `--allow-tool` apropriado; caminhos externos continuam recusados, e
bash continua dependendo de Docker.

O texto da skill é enviado ao provedor selecionado e permanece no histórico
local daquele turno. Isso será informado no guia da CLI. Logs operacionais
e avisos não registram esse texto; a rastreabilidade usa nome, versão e hash.
Não se promete detecção de segredos que o usuário tenha colocado no arquivo.
Testes e smoke da entrega usarão somente conteúdo e credenciais fictícios.

## Verificação exigida na implementação

- CLI real com serviço e adapter controlado: selecionar uma e várias skills,
  confirmar o texto no `AdapterRequest` e a ordem, sem depender da resposta
  final do modelo. Exercitar `run`, `chat`, texto, JSON e pipe.
- Banco real: confirmar pedido exibido preservado, payload enviado completo
  e metadata com nomes/hashes; reiniciar o serviço, editar/apagar os arquivos
  e confirmar que o histórico enviado continua com a versão antiga.
- Turno com duas rodadas e retry: alterar arquivos e a lista do chamador
  depois da captura; confirmar que todos os requests usam os snapshots
  originais. Novo comando com seleção deve usar a nova versão.
- Erros anteriores ao provider: ausente, nomes hostis, nome divergente,
  YAML/tipos inválidos, UTF-8 inválido, corpo vazio e limites de bytes/nomes.
  Nenhuma mensagem de usuário ou chamada parcial no caso de uma segunda
  skill inválida.
- Arquivos reais temporários: escapes, symlinks em cada componente,
  hardlinks, arquivo especial e substituição de caminho após abertura.
  Confirmar que nenhum conteúdo externo é enviado ao adapter ou exposto
  em erros; preservar os arquivos de origem.
- Isolamento: homes com o mesmo nome de skill e conteúdos diferentes,
  sessões distintas e chamadas concorrentes. Sem seleção, nem catálogo é
  lido nem contexto novo é injetado.
- Skill que pede escrita externa, shell do host ou ferramenta omitida:
  produzir chamadas controladas pelo adapter e observar recusas reais;
  confirmar que skill não cria uma aprovação nem muda schemas.
- Rejeição antecipada em CLI interativa/Agent Runtime e no roteador de
  runtime. Preservar opções existentes, provedores locais, migração e
  rollback de credenciais, sync e quarentena.

Os testes verificam comportamento e relações entre dados; não leem código
Python nem congelam contagens de catálogo. TDD demonstra as falhas antes
da implementação. Gate de entrega: `scripts/ci.sh` completo e checks remotos
verdes, conforme AGENTS.md. Não habilitar `runtime_live` sem credenciais.

## Entrega e revisão

Após aprovação desta especificação, será escrito um plano de implementação
com tarefas e testes. A revisão desse plano precede a implementação, conforme
o fluxo arquitetural da skill brainstorming. Esta especificação não altera
produção nem constitui uma alegação de implementação concluída.

Na implementação, documentar a divergência em `docs/decisoes.md`, atualizar
o guia e o registro do roadmap. O índice automático, o consumo de referências,
autoria (`/learn`), telemetria, curador e workflows duráveis continuam em
recortes próprios. Merge/deploy seguem a autorização existente, condicionados
aos gates de validação do projeto.
