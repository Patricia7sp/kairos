# Catálogo compacto e leitura de skills sob demanda

Status: especificação e plano aprovados pela usuária em 2026-10-08; implementação em andamento conforme `../plans/2026-10-08-catalogo-skills.md`.

## Objetivo e entendimento

A usuária autorizou avançar após os PRs #94 e #95. O próximo resultado é que
o modelo encontre uma skill instalada por nome e descrição e consulte seu
procedimento ou uma referência quando necessário, sem enviar todos os corpos
em toda requisição. Workspace, aprovação, credenciais fixadas e isolamento
entre homes/sessões continuam obrigatórios.

Base examinada: `main` em `8f8db6c1eff47d192837d3db357d8fcc2436c07b`.
Checkout original preservado. Proposta na branch `feat/catalogo-skills`, no
worktree `.worktrees/skills_runtime`.

Política aprovada: habilitação explícita por turno e snapshot fixado por sessão.
Não houve implementação ou alteração de produção neste recorte.

## Rastreabilidade

| Referência | Contrato | Aplicação |
|---|---|---|
| `_reversa_sdd/skills/requirements.md`, unit skills, RF-09 | Índice compacto, cache em processo e snapshot em disco, estável na conversa | Índice imutável por sessão, persistido em SQLite e reutilizado no turno |
| Mesma referência, RF-17 | Knowledge-base e references sob demanda | Leitura paginada de SKILL.md e referências textuais declaradas no snapshot |
| Mesma referência, RF-05 e formato | Nome e descrição validados | Reutilizar o contrato de leitura dos PRs #94/#95 |
| `_reversa_sdd/skills/design.md`, dependências/estado interno | Fonte runtime instalada, índice separado do corpo | Apenas `<home>/skills`; cache separado do bundle e do manifesto de origem |
| `docs/decisoes.md`, D-CLI.SKILL, D-CLI.WS, D-CLI.AUTO, D-09.SYNC | Contexto sem concessão de permissão; contenção e autoria | Leitor próprio de skills, sem ampliar read_file, escrever ou executar assets |
| `_reversa_sdd/hermes-state/requirements.md`, migração/reparo | Dados canônicos preservados e migração idempotente | Tabelas aditivas e tratamento explícito dos snapshots no reparo/exportação |

O snapshot em disco será SQLite, em vez de `skills/index-cache/`. Isso permite
associar o catálogo à sessão e salvar assets verificados pela mesma fronteira
de persistência. A divergência deve ser registrada em `docs/decisoes.md`.

## Alternativas

1. **Opt-in com catálogo persistido e leitor restrito — proposta.** Controla
   custo e divulgação, mantém versões verificáveis e permite referências fora
   do workspace sem dar acesso genérico a arquivos.
2. **Catálogo ativo por padrão em todo Chat.** Reduz opções, mas envia nomes e
   descrições em conversas que não precisam de skills e muda o custo padrão.
3. **Copiar skills para o workspace e usar read_file.** Evita um schema novo,
   mas duplica arquivos, dificulta fixar versões e conflita com sincronização
   e autoria. Não atende bem à estabilidade de RF-09.

Uma única ferramenta de leitura, gated pelo catálogo do turno, é justificada
pela ausência de uma capacidade existente que leia skills fora do workspace.
Não se adicionam ferramentas separadas de listagem, busca ou execução.

## Fluxo da CLI

```bash
kairos run --session trabalho --skills-catalog "Revise a documentação"
kairos run --session trabalho --skills-catalog "Continue a revisão"
kairos run --session trabalho --skills-catalog --tools --workspace /projeto \
  --allow-tool edit_file "Aplique a revisão no README.md"
```

- `--skills-catalog` vale para um turno único de `run`/`chat` por modelo.
  Deve ser repetido nos turnos que disponibilizam catálogo e leitura; não
  cria uma autorização persistente para ferramentas.
- Na primeira ativação daquela sessão, capturar e persistir o catálogo.
  Ativações seguintes reutilizam seus mesmos bytes, inclusive após reinício.
  Skills instaladas/editadas posteriormente aparecem em uma nova sessão.
- Sem a opção, não descobrir arquivos, criar catálogo, acrescentar índice
  nem oferecer skill_view. Resultados de leituras anteriores continuam no
  histórico ativo, como os demais resultados de ferramentas.
- A opção funciona sem `--tools`: nesse caso, só disponibiliza skill_view.
  Ferramentas gerais continuam dependendo das opções existentes.
- `--skill NOME` permanece válido e pode coexistir com o catálogo. Sua
  seleção explícita continua capturando a versão atual para a mensagem user;
  não atualiza nem substitui o catálogo fixado. Esse contraste de versões
  será informado no guia e identificado pelos hashes.
- Sem mensagem ou no Agent Runtime, recusar antes da chamada ao provedor.
  Web/gateways e o Chat interativo não ativam esta capacidade neste recorte.
  O roteador também rejeita a opção em envelopes de runtime.
- Modelo sem suporte a ferramentas deve receber erro acionável antes de
  enviar o turno; não anunciar skill_view que ele não consegue chamar.
- Aviso em stderr informa catálogo ativo, número de entradas e quantidade
  omitida por invalidez, sem corpos, descrições ou paths absolutos.
  stdout JSON conserva os eventos NDJSON existentes.

## Captura e índice

Fonte única: diretórios imediatos em `<home>/skills`, no formato instalado
pelo sync. Não sincronizar automaticamente nem consultar bundle, optional,
quarentena, outros homes ou rede.

Validar SKILL.md com o leitor estrito existente: nome kebab-case até 64,
descrição até 60, compatibilidade de versões antigas, UTF-8, arquivo regular
sem links e limite de 64 KiB. Entradas ausentes/invalidáveis não são
anunciadas; um aviso agregado torna a omissão visível. Erros não reproduzem
conteúdo. Falha na raiz ou falha global de captura recusa a ativação.

O índice contém somente nome e descrição, em ordem lexical e JSON
determinístico. Versão, SHA-256 do SKILL.md e inventário de referências vivem
no manifesto interno, não oneram o índice enviado. O digest do catálogo
cobre o manifesto completo, não apenas nomes/descrições.

Política proposta: até 256 skills válidas e índice serializado até 32 KiB.
Se o conjunto válido exceder um limite, recusar com orientação para reduzir
o conjunto instalado; não truncar ou escolher um subconjunto silencioso.
Catálogo sem nenhuma entrada válida retorna erro de uso sem chamar provider.

Inventariar apenas referências `.md` e `.txt` abaixo de `references/`, até
128 arquivos por skill, profundidade de diretórios até oito, 256 KiB por
referência e 64 MiB de assets textuais no catálogo inteiro. Identificadores
relativos têm até 512 caracteres e o manifesto serializado tem até 32 MiB.
Esses limites também são verificados ao carregar snapshots do banco. Referências
inválidas são omitidas e contadas no aviso; limites globais recusam a captura.
Não ler scripts/templates nem inventariar seus conteúdos. Nenhum arquivo é
executado. Os limites são política de recursos, não contagens do bundle.

Ler por descritores ancorados na raiz autorizada, sem seguir links em nenhum
componente; recusar hardlinks e arquivos especiais, usar abertura não
bloqueante e leitura limitada. Mudanças observadas durante a leitura recusam
o asset. A captura calcula hashes locais; envia ao provedor apenas o índice.
Não há promessa de transação contra um editor externo alterando vários assets.

## skill_view e capacidade de leitura

Schema proposto: `skill_view(name, reference?, offset=0, limit=4000)`.
`name` precisa pertencer ao snapshot. Sem reference, consultar SKILL.md;
com reference, usar o identificador relativo presente no inventário de
references daquele nome. Identificadores usam barras POSIX; componentes vazios,
`.` ou `..`, barras invertidas e caminhos absolutos são recusados. Caminhos
arbitrários, escapes, aliases
de ferramentas e nomes não anunciados são recusados antes de I/O.

O resultado contém nome, referência relativa, SHA-256, texto da página,
offset, total de caracteres e next_offset ou null. Offsets/limites são
inteiros em caracteres Unicode; limite entre um e 4000. Uma página não
divide um caractere UTF-8. Offset além do fim retorna erro; no fim retorna
página vazia terminal. Paginação explícita evita cortar conteúdo e mantém
o resultado dentro do orçamento atual de 32 KiB, inclusive com escapes JSON.

O primeiro acesso entrega somente bytes cujo hash coincide com o manifesto.
Persistir o texto completo verificado no cache, antes de emitir a página.
Depois, páginas/retries usam esse conteúdo imutável. Se o arquivo mudar ou
sumir antes do primeiro acesso e não houver conteúdo verificado no cache,
retornar erro solicitando nova sessão; nunca carregar a versão nova sob o
nome antigo. Cache de outro hash não é fallback.

Orçamento adicional: até 128 KiB de texto de páginas entregue por turno.
O limite existente de chamadas/rodadas continua aplicado. Excesso retorna
erro nomeado sem leitura adicional. Não repetir carga ou truncar sem informar.

Registrar a ferramenta em toolset com requisito de capacidade ativa do
turno. O handler também exige essa capacidade, pois disponibilidade de schema
não é uma barreira suficiente para dispatch direto. A capacidade tem home,
sessão e digest fixados e é encerrada com o turno; sem variável global
mutável que permita compartilhar seleção entre requisições concorrentes.
Propagar o contexto apenas às operações próprias daquele turno.

Sob workspace, liberar apenas esse handler nativo de leitura comprovada.
Overrides de plugins para skill_view são recusados mesmo sem workspace.
Não ampliar read_file, search_files, bash, MCP ou qualquer mutador. Leitura
de skill não exige aprovação de escrita; executar seus comandos continua
dependendo das capacidades e aprovações existentes.

## Contexto e persistência

Adicionar um bloco system fixo com o índice serializado e instrução estática
de consulta. Nomes e descrições são dados de referência, escapados em JSON,
não instruções de autoridade. Corpos e referências entram exclusivamente
como resultados tool; não promovê-los a mensagens system.

Salvar o bloco exato junto do snapshot. Todos os AdapterRequests daquele
turno habilitado, suas rodadas e retries recebem os mesmos bytes uma única
vez, sem duplicar o bloco a cada reconstrução do histórico. Sem a opção,
não acrescentar esse bloco. Mensagens user e api_content existentes continuam
com a semântica do PR #94; resultados tool preservam os bytes entregues.

Adicionar tabelas canônicas para snapshot por sessão e assets verificados,
com migração aditiva e repositório dedicado. Não colocar catálogo em
model_config nem misturá-lo com parâmetros, seleção de modelo ou credenciais.
Snapshot e assets devem validar estrutura, limites e hashes ao reabrir o
banco; corrupção recusa a capacidade, sem reindexação silenciosa.

Criar o snapshot somente dentro da posse do turno e em transação. Dois
processos na mesma sessão devem observar um único vencedor; não substituir
um snapshot existente. O cache em memória vive no turno/serviço, e SQLite
é o snapshot durável em disco. Operações assíncronas passam pelo worker
existente; cancelamento fecha descritores e aguarda operações próprias sem
deixar trabalho órfão. SQL síncrono continua disponível aos testes e serviços.

Compactar/rebobinar mensagens não altera o catálogo separado. Forks ou novas
sessões não herdam implicitamente a capacidade; a nova ativação captura seu
próprio catálogo. Exportação e reparo precisam preservar/identificar esses
dados canônicos; migração idempotente não modifica mensagens, seleção ou
credenciais anteriores. Verificar abertura com a versão anterior sem a
opção e restauração do backup como parte do teste de rollback de migração.

O índice e as páginas carregadas são enviados ao provedor e persistidos
localmente. O cache local também retém os assets efetivamente consultados.
O guia precisa explicar essa divulgação e retenção. Logs/avisos não registram
corpos ou valores de chaves; nomes, identificadores relativos e hashes bastam
para auditoria. Não prometer detecção de segredos dentro dos arquivos.

## Componentes e limites do recorte

- `kairos_skills`: descoberta, manifesto imutável, leitura contida e paginação.
- `kairos_state`: schema aditivo, snapshots/assets e persistência transacional.
- `kairos_integration`: capacidade do turno, composição do índice, schemas,
  tool loop e worker assíncrono.
- `kairos_cli`: opção, combinações inválidas e aviso sem conteúdo.

Ficam fora: autoria/learn, telemetria de uso, curadoria, instalação de
terceiros, execução de scripts de skills, configuração global automática,
workflows duráveis, Web/gateways e skills nativas do Agent Runtime.

## Verificação exigida

1. CLI e adapter fictício: índice chega no request, corpos ausentes antes
   da chamada; skill_view recebe um corpo/ref real e o request seguinte
   contém seus bytes. Sem flag, nenhum schema/índice novo ou varredura.
2. Reabrir serviço e banco, editar/apagar/adicionar skills: índice byte-estável;
   cache mantém conteúdo original, asset não carregado divergente recusa;
   nova sessão observa as novas versões.
3. Paginação: acentos, emoji, escapes JSON, fim do arquivo, offsets inválidos,
   limite por página e orçamento agregado; reconstrução sem perda/repetição.
4. Arquivos reais: symlinks em cada componente, hardlinks, FIFO, escapes,
   trocas de caminho após abertura, UTF-8/YAML inválidos e complexidade.
   Nenhum conteúdo externo chega ao adapter ou a erros/logs.
5. Homes/sessões concorrentes e processos disputando snapshot; sem vazamento
   de capability/cache, erro parcial ou troca de catálogo no retry.
6. Skill pede mutação/shell/MCP e override de skill_view: observar recusa real
   e manutenção de workspace, --allow-tool e aprovação. Sem flag, dispatch
   direto também recusa. Modelo sem tools e runtime recusam o recurso.
7. Banco antigo, migração duas vezes, cancelamento, reparo, exportação, backup
   e retorno à versão anterior; preservar dados e seleção/credenciais.
8. Manter regressões de sync, --skill, provedores locais, credencial ativa,
   compactação/rewind e tool loop. Testes comportamentais com dados fictícios;
   nenhum teste lê código Python ou congela contagens do catálogo.

TDD e revisão independente precedem entrega. Gate: scripts/ci.sh completo
e todos os checks remotos verdes; depois squash, inspeção do diff e deploy
autorizado com smoke fictício, commit/hashes/saúde reais. Não executar
runtime_live ou usar chaves reais sem os pré-requisitos existentes.

## Próxima etapa

Aprovar ou corrigir esta especificação. Depois escrever o plano de
implementação, revisar suas tarefas e escolher o método de execução antes
de escrever código do produto, conforme o fluxo arquitetural de brainstorming.
