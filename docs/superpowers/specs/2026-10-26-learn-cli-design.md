# `/learn` no Chat da CLI — criação por modelo com confirmação humana

Status: proposta de desenho para aprovação. A abordagem e os seis requisitos
de recorte foram fornecidos pela usuária em 26/10/2026; as decisões adicionais
abaixo são propostas do implementador. Nenhum código de produto foi alterado.

Base examinada: `main` em `f106ba8a8934f21f19abe9484b5ba3cb710fb06a`, após
os PRs #118 e #120, com CI e deploy #323 aprovados. Trabalho no worktree
existente `.worktrees/backup_sync`, branch `docs/learn-cli-design`; checkout
principal preservado. A triagem de segurança do Sonar fica preservada para
outro lote, sem reclassificação de alertas nesta entrega.

## Objetivo e abordagem escolhida

O usuário escreve `/learn <pedido>` no Chat da CLI. O modelo usa a conversa
ativa e esse pedido como fonte, produz uma skill e chama `skill_create`.
O usuário vê nome, descrição e o `SKILL.md` inteiro antes de decidir. Somente
uma confirmação humana vinculada a essa proposta permite publicar pelo
serviço de autoria existente. O resultado é uma instalação real, registrada
no ledger e reversível por `kairos skills rollback <operation_id>`.

O pedido continua sendo um turno normal do `InteractionService`, com o mesmo
modelo, provedor, credencial fixada, histórico, orçamento, retries e hooks.
Não há modelo auxiliar, motor de destilação nem execução paralela de agente.

### Alternativas examinadas

1. **Criação na conversa com confirmação obrigatória — escolhida pela usuária.**
   O agente solicita a criação e o backend comprova o efeito. Exige uma
   capacidade temporária de escrita e uma aprovação que permita revisar
   conteúdo. Reutiliza a publicação, política, journal e ledger atuais.
2. **Somente rascunho e posterior `skills add --file` — não escolhida.**
   Reduz a superfície de escrita do turno, mas exige instalação manual e não
   atende ao fluxo de `/learn` definido em RF-01. Não será apresentada como
   implementação de `/learn`.

## Rastreabilidade

| Referência | Contrato | Recorte |
| --- | --- | --- |
| `../hermes-agent/_reversa_sdd/skills/requirements.md`, RF-01 | O agente autora e solicita criação | Prompt compartilhável e `skill_create` nativo, com efeito pelo serviço de autoria |
| Mesma unit, RF-02 | Conversa, texto, código e URL como fontes | Somente conversa ativa e pedido neste MVP; coleta de diretório/URL permanece pendente |
| Mesma unit, RF-04/RF-05/RF-06/RF-08 | Origem confiável, validação, barreiras e reversibilidade | `USER_FOREGROUND` e `Provenance.USER`, frontmatter estrito, política e ledger existentes |
| Mesma unit, regras de privacidade | Autor não vem do ambiente | Sem leitura de login, Git ou identidade ambiental para preencher autoria |
| `skills/tasks.md`, T-20–T-22 | Um prompt, turno normal e padrões de autoria | Construtor puro; nenhuma engine ou ferramenta de destilação |
| `docs/superpowers/specs/2026-10-08-autoria-skills-design.md` | Nomes novos, bytes validados e publicação recuperável | Extensão segura da entrada de conteúdo, sem duplicar a publicação |
| `docs/superpowers/specs/2026-10-25-indice-automatico-cli-design.md` | Snapshot positivo fixo por conversa | Criação não invalida nem amplia implicitamente o snapshot atual |
| `kairos_cli/tool_approval.py`, `kairos_integration/chat_tools.py` | `--allow-tool` pode autorizar mutadores comuns; argumentos têm 16 KiB | Criação fica fora desse bypass e mantém o limite global |

`skill_create` é o nome restrito escolhido neste recorte para a ação de criação
que o legado expõe em `skill_manage(action="create")`. Não implementa edição,
remoção, anexos nem as demais ações de gerenciamento.

## Escopo e limites do MVP

- Chat por modelo na CLI, interativo e mensagem única com terminal disponível.
- Sintaxe `/learn <pedido>`; pedido vazio produz ajuda e erro de uso antes do
  provedor. `/learning` e conteúdo do modelo não ativam a capacidade.
- Uma skill nova por pedido. Somente `SKILL.md`, sem `references/`, `scripts/`
  ou `templates/`. Nome existente continua recusado, sem `--force` ou substituição.
- Fontes: mensagens ativas que o Chat já enviaria ao modelo, incluindo sua
  compactação/rewind normais, e o pedido atual. Não ler transcrições arquivadas,
  outro perfil, arquivos ou URLs para ampliar a fonte.
- Durante `/learn`, o conjunto de ferramentas enviado ao modelo contém somente
  `skill_create`. As opções normais de ferramentas da sessão voltam a valer no
  próximo turno comum. Nenhuma ferramenta de arquivo, web, shell ou MCP é
  implicitamente liberada para coletar fontes neste MVP.
- O índice já fixado continua seguindo seu contrato. `/learn` não chama
  `skill_view` nem injeta novos corpos de skills como fontes adicionais.
- Uma seleção explícita `--skill` junto de uma mensagem `/learn` é recusada
  neste recorte, antes de carregar o arquivo; o usuário pode aprender de
  conteúdo de skill já presente na conversa ativa.
- Agent Runtime/Codex, Web, gateways, cron e subagentes não recebem a capacidade.
- `--json`, pipe, EOF e ausência de terminal não autorizam instalação. A CLI
  recusa `/learn` nesses modos antes de chamar o provedor, com orientação para
  usar o Chat em terminal. Não haverá `--yes` nem opção de autoaprovação.
- Modelo sem suporte confirmado a ferramentas é recusado antes da geração.
  Nenhuma troca automática de modelo, credencial ou política de privacidade.

Diretórios/URLs, RF-02 completo, edição por modelo, sedimento autônomo,
telemetria, curador, workflows duráveis, hub e outras superfícies são entregas
separadas. Não declarar a unit `skills` concluída por este MVP.

## Prompt e histórico

`build_learn_prompt(user_request)` será uma função determinística em
`kairos_skills`, reutilizável por superfícies futuras. Ela orienta o modelo a:

- Basear comandos, caminhos e APIs somente nas fontes disponíveis; pedir
  esclarecimento quando faltar evidência e não inventar o procedimento.
- Produzir conteúdo conciso, com frontmatter válido, nome kebab-case até 64
  caracteres, descrição até 60 terminada em ponto e versão semver.
- Usar o enquadramento das ferramentas do agente e as oito seções de autoria
  da spec, evitando uma skill que apenas encaminhe para outras.
- Não inferir autor pelo ambiente. O padrão proposto é autor genérico
  `Kairos` ou campo ausente; não inventar uma licença para a fonte.
- Chamar `skill_create` e usar o recibo retornado para informar a criação.
  Dizer que salvou um arquivo não é evidência de instalação.

O comando é reconhecido na entrada confiável da CLI, antes de aumentos de
contexto como experiências. O envelope recebe uma intenção imutável de
aprendizado, separada do texto. O serviço não infere essa autorização de
texto de assistente, histórico, frontmatter ou argumentos de ferramenta.

O texto visível preserva o pedido do usuário; a expansão efetivamente enviada
vai em `api_content`, compondo com o contexto normal de forma determinística.
Retries reutilizam o pedido persistido. O contexto enviado não ganha papel
system por conter material de uma skill ou fonte externa.

## Capacidade do turno e ferramenta

`skill_create` aceita somente:

```json
{"name": "nome-kebab-case", "skill_md": "texto completo de SKILL.md"}
```

O nome declarado precisa coincidir com o frontmatter. Campos extras, chaves
JSON duplicadas, tipos incorretos e UTF-8 inválido são recusados. Ator,
proveniência, home, caminho de destino e IDs de autorização não são argumentos
do modelo.

A capacidade segue o padrão revogável de `skill_view`: contexto local ao
turno, titular compartilhado com flag de encerramento, callback nativo e
revogação no `finally`. Cópias antigas do contexto não conservam autorização.
O handler nativo e a ausência de override são conferidos na exposição e no
despacho. Indisponibilidade ou override produz recusa; não há fallback para
outro escritor.

O schema só aparece quando a intenção explícita de `/learn` está ativa sob
posse do turno. `CHAT_TOOLS`, o conjunto normal de `--tools` e os schemas dos
demais turnos não passam a conter criação. A chamada direta ao registry fora
da capacidade também recusa, antes de qualquer publicação.

Uma invocação pode confirmar no máximo uma criação. Validação inválida pode
ser corrigida pelo modelo dentro dos orçamentos existentes. Recusa humana,
cancelamento ou timeout encerram a capacidade; uma revisão pelo usuário deve
começar com novo `/learn <pedido de revisão>`, aproveitando o histórico.
Depois de entrar na publicação, erro de I/O/estado pendente não dispara uma
segunda publicação automática no mesmo turno.

## Proposta congelada e confirmação forte

Antes da aprovação, o backend valida os argumentos e o conteúdo em memória e
gera uma proposta imutável: ID opaco, conversa, turno/chamada, nome, descrição,
texto exato, SHA-256 e tamanho. Não prepara arquivos nem escreve no ledger de
autoria nessa fase.

A CLI obtém a proposta validada do serviço e mostra nome, descrição, hash e
todo o `SKILL.md`. Usa bloco delimitado, prefixo por linha e escape de controles
de terminal e caracteres de formatação invisível. Não trunca conteúdo nem
interpreta escapes ANSI vindos da skill.

A confirmação proposta é digitar **`criar <nome>`**, com o nome exato mostrado.
`sim`, uma resposta vazia, outro nome, resposta excessiva, timeout e EOF não
autorizam. O leitor dessa confirmação suporta o prefixo mais os 64 caracteres
do nome, sem aumentar o limite padrão do leitor de aprovações comuns.

O serviço só aceita a confirmação se ID, conversa, turno/chamada e hash ainda
corresponderem à proposta pendente. A publicação recebe exatamente os bytes
aprovados. Alteração de conteúdo, proposta expirada, replay ou aprovação de
outra sessão não autorizam escrita.

### Não há bypass de automação ou aprovação genérica

- `skill_create` não entra em `MUTATING_TOOLS` nem em `AUTOMATION_TOOLS`.
- Sua necessidade de aprovação é explícita e sempre verdadeira. O caminho
  específico da CLI é selecionado antes de consultar `authorized_tools`.
- `--allow-tool skill_create` é recusado. Autorizações de outros nomes não
  afetam essa operação, nem se um chamador interno fornecer esse nome no set.
- A API genérica `decide_tool_approval(allow/deny)` não resolve propostas de
  criação. Isso impede que o endpoint Web de aprovação genérica libere uma
  proposta CLI sem o fluxo específico.
- O serviço/router oferece consulta da proposta e decisão específica,
  vinculada aos seus IDs e hash. O transporte CLI é o consumidor; não se cria
  endpoint HTTP de confirmação neste MVP.

“Humana” significa confirmação ativa em terminal neste fluxo, sem flags ou
API genérica de autoaprovação. Não é uma atestação de humanidade contra um
processo que controla o mesmo UID ou simula um terminal; esse isolamento
continua fora dos recortes acordados.

## Publicação compartilhada e prova do efeito

Acrescentar `SkillMutationService.add_text(text, *, actor)` como entrada segura.
Ela valida em memória e utiliza a mesma implementação de publicação de
`add(source, *, actor)`. A leitura segura de arquivo de `add` e seus contratos
continuam preservados. Não chamar handlers da CLI, não executar `skills add`
por shell e não usar `write_file` no workspace como ponte.

A extração interna mantém ator, política organizacional, nome disponível,
lock, preparação durável, publicação sem substituição, verificação de
identidade, recuperação e conclusão no ledger. A composição usa a abertura
segura de banco e os repositórios de skills existentes; não cria outro banco,
ledger ou diretório de rascunhos.

O callback só fornece `Actor.USER_FOREGROUND` depois da confirmação vinculada
ao pedido explícito. A origem persistida é `Provenance.USER`: o usuário pediu
a autoria, e a skill não passa a ser sedimento do curador por ter sido escrita
por modelo. Nenhum campo do modelo pode mudar essa classificação.

A capacidade e a posse são conferidas antes de iniciar a mutação e no guard
de publicação. A integração pode acrescentar um guard confiável opcional ao
serviço compartilhado para essa verificação; nunca um argumento do modelo.
Cancelamento anterior impede iniciar a publicação. Uma operação já iniciada
segue o journal existente; cancelamento não promete rollback de uma criação
já concluída nem desfaz efeitos físicos retroativamente.

Só um registro efetivamente `COMMITTED`, com nome/hash correspondentes à
proposta, produz recibo de sucesso. O resultado contém `operation_id`, nome,
estado, origem e hash; não contém caminhos privados nem o corpo da skill.
Estado pendente/erro informa a categoria e o `operation_id`, quando existente,
e não se transforma em sucesso porque o modelo diz que salvou.

Recusar uma proposta não escreve arquivos de skill nem entradas no ledger de
autoria. A conversa, os argumentos da chamada e o consumo do provedor seguem
a persistência e a contabilidade normais do Chat. Não se promete zero escrita
de histórico para um turno normal.

## Limite escolhido: 16 KiB

O MVP mantém `MAX_ARGUMENT_BYTES = 16_384` para o **JSON completo** da chamada,
incluindo nome, estrutura e escapes. O `skill_md` precisa caber nesse envelope;
não se promete um corpo de 16 KiB mais overhead. Excesso é recusa explícita,
sem truncamento ou criação parcial.

Os 64 KiB do serviço de autoria continuam valendo para a entrada geral/manual.
Este recurso não amplia o orçamento global nem cria exceção por ferramenta.
Fontes grandes são resumidas para uma skill concisa; knowledge-base e anexos
ficam para outro recorte. Também permanecem os limites de ferramentas, rounds,
resultado e timeout de aprovação existentes.

## Resultado visível, retomada e catálogo

A CLI mostra o recibo nativo e o comando de rollback quando a criação é
comprovada. Não usa a prosa do assistente para decidir sucesso.

Proposta de códigos para `/learn`: 0 exige criação comprovada e turno sem
falha; 2 para sintaxe/combinação incompatível; 77 para recusa humana ou modo sem
confirmação interativa; 1 para geração sem criação, validação não corrigida,
falha do provedor ou publicação incompleta. Se a criação se concluiu antes de
falha posterior do provedor, o recibo continua visível e a falha é propagada.

O usuário pode inspecionar a criação com `skills history` e revertê-la com
`skills rollback`. Não se adiciona recuperação automática de um aprendizado
incompleto ao reabrir a sessão; uma nova tentativa requer novo pedido e nova
confirmação, preservando a recuperação já existente do journal.

A skill publicada é consumível pelos caminhos de leitura existentes. O
snapshot positivo da conversa atual permanece congelado; para a nova entrada
aparecer no índice automático, usar uma nova sessão. Não alterar ou apagar o
histórico antigo para forçar atualização.

## Verificação exigida

- CLI real, serviço compartilhado e adapter controlado: pedido `/learn`,
  conversa anterior em SQLite, proposta pelo modelo, confirmação, arquivo e
  registro `COMMITTED` com bytes/hash e recibo correspondentes.
- Mostrar nome, descrição e conteúdo inteiro; cobrir controles ANSI/bidi,
  nomes de 64 caracteres, nome/hash alterados e resposta genérica `sim`.
- Recusa, EOF, timeout e cancelamento antes da confirmação: zero skill/ledger
  novo; o histórico normal não é confundido com instalação.
- Confirmar que `--allow-tool`, `--json`, pipe, aprovação genérica, outra
  conversa, replay, registry direto e cópia de contexto revogado não escrevem.
- Dois turnos/homes concorrentes: capacidade e propostas isoladas; nomes
  existentes preservados e disputa pelo mesmo nome resolvida pelo backend.
- Argumentos na fronteira de 16 KiB, escapes, chaves duplicadas, UTF-8/tipos
  inválidos, nome divergente e frontmatter inválido: recusa sem publicação.
- `add(source)` e `add_text` compartilham os mesmos guardas, política, estados
  e recuperação; manter testes reais de symlink, hardlink, swap e falha após
  preparação/publicação. Não testar forma do código-fonte.
- Modelo sem ferramentas confirmadas, Agent Runtime e `/learning`: nenhuma
  criação ou mudança de configuração/modelo. Falas de “salvei” sem recibo
  nunca contam como sucesso.
- Durante `/learn`, nenhuma coleta nova por arquivo, web, shell, MCP ou leitura
  de skill; nos turnos comuns, schemas, seleção, aprovações e payloads seguem
  seus contratos anteriores. Preservar snapshot desde a primeira ativação.
- Suíte local integral e CI completos antes de merge; imagem instalada e smoke
  com provider controlado. Provedores externos reais e política organizacional
  instalada em produção continuam exigindo aceites próprios.

## Decisões do implementador para revisão

1. Manter 16 KiB para o JSON total, sem exceção global de orçamento.
2. Confirmação por `criar <nome>`, ligada ao conteúdo congelado e com caminho
   distinto da aprovação genérica.
3. Preferir `add_text` com pipeline interno compartilhado a materializar fonte
   temporária ou despachar a CLI.
4. Primeiro recorte só na CLI, com `skill_create` como única ferramenta do
   turno de aprendizado, mesmo em uma conversa iniciada com `--tools`.
5. Exigir novo `/learn` para revisar após recusa humana e não prometer nova
   skill no índice de uma conversa com snapshot já fixado.

Após a revisão do desenho e sua aprovação, o próximo passo será escrever o
plano de implementação. Esta proposta não autoriza produção, merge ou deploy.
