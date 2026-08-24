# Decisões de reconstrução — Kairos

Registro das divergências deliberadas em relação ao legado. Cada entrada
nomeia a tarefa que a produziu e a evidência que a justifica.

---

## Tarefa 01 — Schema do Banco de Dados

### D-01.1 — `gateway_routing` ganha `session_id` com FK

**Divergência.** No legado, o `session_id` ficava enterrado dentro de
`entry_json` e não havia chave estrangeira; `session_key` órfão era possível.

**Por quê.** A relação é **N:1** — `session_key` é identidade de
*roteamento*, `sessions.id` é identidade *durável*. Foi essa relação, e o
fato de o banco não a enxergar, que produziu o bug #64934: os guards eram
chaveados pela coluna da esquerda enquanto o transcript pertence à do meio,
e `switch_session()` torna a relação N:1 — nenhum guard por chave de
roteamento vê a colisão.

**A regra que fica verificável.** Sempre que uma relação N:1 separa a chave
de coordenação da chave do dado, **o lock tem que estar do lado do dado**.
`session_turn_leases.conversation_id` referencia `sessions.id`, e há teste
provando isso (`test_lease_de_turno_e_chaveado_pela_identidade_duravel`).

Origem: `_reversa_sdd/erd-complete.md` §2 e §6; decidido em 2026-08-23.

### D-01.2 — `SCHEMA_VERSION` do Kairos começa em 1

**Divergência.** O plano diz "schema v26". O legado chegou ao 26 por 26
migrações sucessivas; o Kairos nasce com a **forma final** delas.

**Por quê.** Herdar o número 26 sem herdar as 26 migrações seria mentir
sobre o histórico do banco e quebraria qualquer migrador futuro, que
esperaria encontrar 25 degraus atrás. `LEGACY_SHAPE_VERSION = 26` registra a
correspondência para rastreabilidade.

### D-01.3 — Índice CJK é opcional e falha-aberto

`initialize_schema(cjk=True)` só deve ser chamado quando a extensão nativa
`fts5_cjk` (Tarefa 04) estiver carregada. Sem ela, a busca degrada por
FTS5 base → trigram → `LIKE`. Um índice ausente reduz a qualidade da busca;
**nunca** derruba o agente.

### D-01.4 — `PRAGMA foreign_keys=ON` é herança, não mudança

O ERD marcava com 🔴 que o PRAGMA "não foi confirmado" no `state.db`
principal. **Verificado em 2026-08-23: está ligado** — `hermes_state.py:3430`
e `:4182`, e a migração de schema o desliga numa janela controlada e o religa
depois (`hermes_state_schema.py:737-811`). O 🔴 era lacuna de documentação,
não defeito. O Kairos herda o comportamento; sem ele, as FKs declaradas
seriam decorativas, porque o SQLite mantém o pragma OFF por padrão e por
conexão.

### D-01.5 — Timeout de conexão curto, por desenho

`connect(timeout=1.0)`. O handler de ocupado embutido do SQLite usa
escalonamento determinístico que produz efeito comboio sob concorrência
alta. A paciência real é da escada de retry com jitter da camada de
aplicação (unit `hermes-state`, T-13), não deste timeout.

### D-01.6 — Suíte em `unittest`, executável por `pytest`

A suíte é escrita em `unittest` da biblioteca padrão, e `pytest` a executa
sem alteração — ambos verificados, 18/18 nos dois. A escolha não é sobre
preferência de framework: um conjunto de testes de schema não deve depender
de terceiros **para existir**. `pytest` entra pelo relatório, não pela
capacidade de rodar.

### D-01.7 — Toolchain fixada em Python 3.11 via `uv`

O host tem Python **3.14.4**; o legado fixa **3.11**. Reconstruir contra 3.14
enquanto as specs descrevem comportamento observado em 3.11 introduziria uma
variável que nenhuma spec cobre. O projeto passa a provisionar o 3.11 pelo
`uv` (`.python-version`, `uv.lock`), independente do interpretador do sistema.

Nota de ambiente: a máquina não tinha `pip`, `venv`, `uv` nem `pytest`, e
`sudo` exige autenticação interativa (logo, `apt` está fora). O `uv` resolve
tudo sem privilégio, instalando em `~/.local/bin` — por isso é ele a
dependência de entrada do projeto, e não o gerenciador de pacotes do sistema.

---

## Tarefa 02 — Entidades de Domínio

### D-02.1 — Regras de runtime e regras de processo ficam separadas

Dos 7 grupos de `domain.md` §2, nem tudo é comportamento de software.
*"Reject PRs that tell users to set X in your .env"* e *"menus interativos de
CLI devem usar curses"* são instruções para quem revisa código.

`kairos_domain/rules.py` registra **todas** as ~45, classificadas em
`RUNTIME` e `PROCESS`. As de runtime apontam para a função que as impõe; as
de processo apontam para `docs/rubrica-de-contribuicao.md`. Um teste garante
que os dois conjuntos são não-vazios e que somam o total.

O motivo de não codificar as de processo: nenhuma execução as exercitaria, e
o teste correspondente só provaria que uma constante existe. Registrá-las
mantém a contagem dos 7 grupos auditável sem produzir código falso.

### D-02.2 — `Platform` tem 24 membros, não 23 — e resolve plugins dinamicamente

A spec (`data-dictionary` §2.1) diz *"23 valores"* e então **lista 24**. A
contagem está errada; a lista está certa. Verificado em
`gateway/config.py:317-341`.

Mais relevante: a spec **omitiu** o mecanismo. O enum define `_missing_()`,
que cria membros dinâmicos sob demanda — `Platform("irc")` funciona sem
alterar o núcleo, e o membro fica cacheado para que a comparação por
identidade permaneça estável. É a Lei 2 aplicada ao enum: plataforma de
plugin não exige mudança no core. Reproduzido, com `is_builtin` distinguindo
os dois casos.

### D-02.3 — O registro de invariantes nomeia quem ainda não impõe

`kairos_domain/invariants.py` lista os 15 com o local de imposição:
`DOMAIN` (aqui, com teste), `SCHEMA` (Tarefa 01) ou `DEFERRED` (unit futura).
Hoje são **9 impostos e 6 diferidos** (1, 6, 11, 12, 13, 14), e há um teste
que fixa esse conjunto — se ele crescer, houve regressão.

A alternativa seria não registrar os diferidos, e aí um invariante sem dono
desapareceria em silêncio.

### D-02.4 — `Message` recusa o estado impossível na construção

`active=1` **e** `compacted=1` não é um estado válido — significaria estar no
contexto do modelo e arquivada por compressão ao mesmo tempo. O construtor
levanta, em vez de deixar `visibility` escolher arbitrariamente. As três
visibilidades ficam sendo de fato três.

### D-02.5 — Alternância de papéis isenta `tool`

`check_role_alternation` não conta mensagens de papel `tool`: uma chamada do
assistente pode produzir vários resultados em sequência, e isso é a forma
normal do protocolo. Aplicar a regra literalmente reprovaria histórico
válido.

---

## Tarefa 03 — Máquinas de Estado

### D-03.1 — A máquina de aprovação de comando ganha o estado `never`

`state-machines.md` §10 traz a nota: *"COLAPSO SEMÂNTICO: o ACP oferece deny
E deny_always, mas o Hermes só tem 'deny' — NÃO existe negação permanente."*

**A nota está errada, e é a mesma afirmação já corrigida em G-22.**
`approvals.deny` existe (`tools/approval.py:623-651`), é editável pelo usuário
em `config.yaml`, e fica **acima** do bypass de yolo na cadeia de 7 camadas
(`hermes_cli/approvals_test.py:1-30`). A spec repete o erro em dois lugares:
aqui e em `gaps.md` G-22.

Coerente com a decisão de `questions.md#pergunta-12`, a máquina do Kairos tem
`never` como estado próprio, distinto de `deny`.

### D-03.2 — A máquina de aprovação de edição reflete o piso de workspace

`state-machines.md` §11 desenha `policy=session → auto_aprovado` sem
qualificação de caminho. Pela decisão de `questions.md#pergunta-11` (G-16), o
workspace vira **piso**: a transição para `auto_aprovado` exige estar dentro
dele, e caminho externo vai para `pergunta_sempre` em qualquer política.

### D-03.3 — O despacho da aprovação de edição é um estado, não duas setas

A spec desenha **dois** pontos de entrada (`[*] --> bypass` e
`[*] --> avaliando`). Isso deixou `bypass` inalcançável, e o validador da
primitiva pegou — foi o primeiro erro que ele encontrou.

Na verdade é um ponto de entrada só, seguido de uma decisão: *o ContextVar
está ligado?* Modelado como estado `despacho`, a decisão fica visível em vez
de escondida numa seta. Para CLI, gateway e cron a guarda é **inexistente**,
não "permissiva" — distinção que importa ao auditar quem é protegido por ela.

### D-03.4 — 13 entidades, 14 objetos de máquina

A tabela-resumo (§15) lista 13 entidades com estado. São 14 objetos porque a
delegação assíncrona tem **dois eixos ortogonais** (`state` e
`delivery_state`) que evoluem independentemente — uma delegação pode estar
`completed` na execução e `pending` na entrega, que é exatamente o gap que o
ledger de obrigação existe para cobrir.

A linhagem de sessão (§2) não vira máquina: é classificação de parentesco,
não ciclo de vida, e vive em `Lineage` desde a Tarefa 02.

### D-03.5 — Transição não declarada é recusada por construção

A primitiva (`statemachine.py`) valida na construção — estado inalcançável,
beco sem saída não declarado terminal, transição citando estado inexistente,
saída a partir de terminal — e `transition()` é o ponto único por onde
qualquer mudança passa. Uma cadeia de `if` deixaria a transição não prevista
cair num `else` silencioso; aqui ela levanta, e a mensagem lista os alvos
válidos.

Isso é o que dá ao invariante 7 (terminais do ledger são imutáveis) uma
segunda imposição, independente da de `scheduling.assert_terminal_immutable`.

---

## Tarefa 04 — native (`fts5_cjk`)

### D-04.1 — A lacuna 🟡 da spec foi fechada lendo a fonte

`native/requirements.md` marcava 🟡: *"O conteúdo de `fts5_cjk.c` (9,6 KB) não
foi lido linha a linha — as faixas exatas de codepoint tratadas como CJK não
foram extraídas."*

São **12 blocos**, agora documentados no próprio C:

| Bloco | Faixa |
|---|---|
| Sílabas Hangul | `AC00..D7A3` |
| Hangul Jamo | `1100..11FF` |
| Hangul Jamo compat | `3130..318F` |
| Hangul Jamo ext-A | `A960..A97F` |
| Hangul Jamo ext-B | `D7B0..D7FF` |
| Ideogramas CJK unificados | `4E00..9FFF` |
| CJK ext A | `3400..4DBF` |
| Ideogramas CJK compat | `F900..FAFF` |
| CJK ext B..F, compat sup | `20000..2FA1F` |
| Hiragana | `3040..309F` |
| Katakana | `30A0..30FF` |
| Katakana fonético ext | `31F0..31FF` |

Hangul aparece **cinco vezes** porque o coreano moderno usa sílabas
pré-compostas enquanto texto decomposto, teclados e dados legados usam Jamo —
tratar só as sílabas deixaria de fora exatamente o material mais irregular.

### D-04.2 — A marca d'água é uma FRONTEIRA, não um progresso

O bug mais instrutivo da tarefa, encontrado por um teste que falhou com
`constraint failed`. A divisão de trabalho é:

```
id  >  high_water   →  responsabilidade do GATILHO (ao vivo)
id  <= high_water   →  responsabilidade do BACKFILL
```

O gatilho de INSERT do schema consulta `fts_cjk_rebuild_high_water` e só
indexa **acima** dela. A marca não é "até onde o backfill chegou" — é a
fronteira fixa entre os dois, definida no instante em que o índice nasce
sobre um banco populado.

Eu havia implementado a marca como cursor de progresso, o que fazia o backfill
reindexar exatamente as linhas que o gatilho já inserira. O avanço passou a
ser rastreado por um **cursor separado** (`fts_cjk_rebuild_cursor`).

Num banco vazio a fronteira fica em `-1`, o gatilho cobre tudo e não há
backfill. Num banco populado, `ensure_cjk_index` fixa a fronteira no maior id
existente, e daí em diante os dois caminham sem se cruzar. Há teste para
exatamente esse cenário.

### D-04.3 — O build vendorizado foi exercitado de verdade

Este host **não tem** `/usr/include/sqlite3ext.h`, então o caminho de RF-03
não é hipotético: a compilação usou `vendor/` e passou com `-Wall -Wextra`
sem warnings. Há um teste que afirma a ausência do header do sistema antes de
concluir que o build vendorizado funcionou — se alguém instalar
`libsqlite3-dev` na máquina, o teste avisa que o requisito deixou de ser
exercitado ali, em vez de passar por engano.

### D-04.4 — Testes que dependem da extensão são pulados, não falhados

`@requires_ext` pula quando o `.so` não compila. Isso não é conveniência: é
o comportamento sob teste. A extensão é opcional, e uma suíte que falhasse
sem ela afirmaria o contrário do RF-12.

Os testes de degradação (`DegradationTests`) rodam **sempre**, inclusive o de
`.so` inválido — carregar lixo não pode levantar.

### D-04.5 — Override de caminho quebrado não cai no padrão

`KAIROS_FTS5_CJK_SO` apontando para caminho inexistente devolve `None`, em vez
de silenciosamente usar `~/.kairos/lib`. Um override quebrado é erro de
configuração e deve aparecer; contorná-lo faria o operador acreditar que está
usando um `.so` que não está.

---

## Tarefa 05 — hermes-state

### D-05.1 — Correção da Tarefa 01: WAL é condicional

A Tarefa 01 aplicava `PRAGMA journal_mode=WAL` incondicionalmente. A spec
(§1) é explícita: **WAL é condicional, não obrigatório**. Ele exige memória
compartilhada e falha em NFS, em alguns FUSE e em montagens de rede — casos
reais, não exóticos: `state.db` num home montado por rede é comum em ambiente
corporativo.

`apply_wal_with_fallback` cai para `DELETE` (o default pré-WAL), que degrada a
concorrência mas mantém a durabilidade. E não insiste: flipar `journal_mode`
com outras conexões abertas é caminho conhecido de corrupção.

### D-05.2 — Bug de trigger encontrado só agora: FTS standalone vs external-content

Os triggers de `messages_fts_trigram` e `messages_fts_cjk` usavam o comando
`INSERT INTO t(t, ...) VALUES('delete', ...)`. Esse comando **só existe para
tabelas external-content ou contentless**. As duas são **standalone**, e numa
standalone ele devolve `SQL logic error`.

Só `messages_fts` é external-content (`content='messages'`) e podia usá-lo.

O bug atravessou as Tarefas 01 e 04 porque nenhuma das duas suítes fazia
`UPDATE` numa linha de `messages` — e o trigger de update é o único caminho
que exercita a remoção. Apareceu na primeira compactação, que é justamente um
`UPDATE` em massa.

### D-05.3 — A instrumentação de contenção entrou junto com a escada (T-13)

Conforme `questions.md#pergunta-9`. Três orçamentos, e a ordem entre eles
codifica o custo da falha:

| Orçamento | Paciência | O que custa falhar |
|---|---:|---|
| `activity` | 0,5 s | Nada — a próxima janela repete |
| `routine` | 20 s | Atraso de UI/background |
| `transcript` | 60 s | **O turno do usuário** |

`gaveup_total` do orçamento `transcript` é o número que mais importa:
diferente de zero significa turno destruído por banco ocupado.

Duas escolhas de medição que valem registro. **Esperas bem-sucedidas também
são registradas** — medir só as falhas esconderia a degradação progressiva
que as antecede. E as amostras de percentil usam janela deslizante, para que
um processo de longa vida não tenha o p99 dominado pela primeira hora de
uptime.

### D-05.4 — A paciência é por tempo, com jitter, e o `busy_timeout` é curto

`BUSY_TIMEOUT_MS = 1000`, deliberadamente baixo. O handler de ocupado embutido
do SQLite usa escalonamento determinístico, o que sob concorrência alta faz
vários escritores acordarem juntos — efeito comboio. A paciência real fica na
camada de aplicação, com jitter aleatório que os escalona naturalmente.

E é por **tempo**, não por tentativa: um orçamento contado em tentativas perde
a corrida contra um checkpoint TRUNCATE ou um VACUUM de processo irmão, e a
falha aparece como turno destruído mesmo com o banco saudável e apenas
ocupado.

### D-05.5 — A linhagem de compactação e seus três filtros negativos

A spec chama de *"a regra de negócio mais valiosa da unit"*, e registra que
**estava ausente** da própria versão anterior dela. A CTE recursiva só sobe
enquanto quatro condições valem: pai encerrado por `compression`, e o filho
**não** sendo branch, **não** sendo delegação e **não** tendo `source='tool'`.

Sem os três filtros negativos, um branch arrastaria a conversa de onde
ramificou e uma delegação arrastaria a do pai que a despachou — em ambos os
casos o usuário veria, no próprio transcript, falas que nunca fez. Há um teste
por filtro.

Acrescentei um teto de profundidade (`MAX_LINEAGE_DEPTH = 1000`): uma linhagem
legítima tem dezenas de degraus, e milhares indicam ciclo por corrupção — a
CTE giraria até estourar memória.

### D-05.6 — A posse do lock é verificada dentro da transação de commit

Entre adquirir o lock de compactação e chamar `archive_and_compact` passou uma
**chamada de LLM inteira**. O lease pode ter expirado e sido tomado nesse
intervalo. Verificar fora da transação deixaria uma janela de corrida do
tamanho da sumarização.

### D-05.7 — Contabilidade coalescida, com devolução à fila em falha

Streaming produz dezenas de deltas por turno. Um `UPDATE` por delta faria da
contabilidade — que é acessória — a principal fonte de contenção no
`state.db`, exatamente quando o turno precisa gravar o transcript.

Se o flush falha, o lote **volta para a fila**: contabilidade perdida é
irrecuperável, e uma falha transitória não deve custar os números do turno.
`drain_at_exit` engole exceção de propósito — no encerramento não há para
quem propagar.

### D-05.8 — A busca tem quatro degraus, independentes entre si

FTS5 base → trigram → bigrama CJK → `LIKE`. Cada um é opcional
**separadamente**: a ausência do trigram não afeta o CJK, e a ausência dos
dois não afeta o índice base. `LIKE` está sempre no fim e nunca é removido —
é o único caminho que existe quando o SQLite foi compilado sem FTS5.

Um índice presente mas inutilizável (corrompido, tokenizador sumido entre a
sondagem e a consulta) cede à rota seguinte em vez de propagar: busca pior é
melhor que busca quebrada.

O `%` e o `_` digitados pelo usuário são escapados no caminho `LIKE` — sem
isso, buscar `%` casaria tudo.

---

## Tarefa 06 — i18n

### D-06.1 — G-30 era falso: os gates de paridade existem, e são dois

A lacuna afirmava que o *"keep keys in sync"* era convenção de cabeçalho sem
teste. **É falso.** `tests/agent/test_i18n.py` traz dois gates parametrizados
sobre todos os idiomas, e o CI roda a suíte inteira em fatias:

1. **Chaves**, nos **dois sentidos** — reprova a que falta e a que sobra. Só
   o primeiro sentido deixaria passar chave órfã de uma remoção incompleta no
   inglês: nunca aparece na tela, ninguém percebe.
2. **Placeholders** — nenhuma spec mencionava este. Verifica que os tokens
   `{...}` do valor traduzido batem com os do inglês, porque *"a mistranslated
   placeholder would either raise KeyError at runtime or silently drop the
   interpolated value"*. Pega o caso em que a chave existe, o texto está
   traduzido, e mesmo assim o valor some.

A tarefa passou de "adicionar um gate" para "herdar dois". Registrado como
**E-06** na errata; é a **terceira** ocorrência de ausência tratada como fato,
depois do PRAGMA (E-01) e do `approvals.deny` (E-04). Desta vez o gate foi
procurado nos workflows e nos catálogos — não na suíte de testes.

### D-06.2 — `covers` e `intends` são perguntas diferentes

A decisão de G-32 declara cobertura por superfície. Ao implementar o aviso ao
usuário (T-11), apareceu uma ambiguidade que o teste expôs: o aviso olha o que
a superfície **entrega** ou o que **pretende**?

Só "entrega" é honesto. O desktop *pretende* cobrir japonês, mas o catálogo
ainda não existe (Tarefa 19) — dizer que cobre, com respaldo de um arquivo de
declaração, seria mentir com aparência de rigor. `covers()` responde runtime,
`intends()` responde planejamento, e o aviso usa a primeira.

### D-06.3 — Entrega e alvo são campos distintos, para a dívida não sumir

`SurfaceCoverage` tem `locales` (o que existe) e `target_locales` (o que o
legado tinha). Sem os dois, ou o build fica vermelho por conteúdo que ninguém
escreveu, ou a dívida desaparece de vista.

O Kairos entrega **3** catálogos (`en`, `pt`, `es`) contra um alvo herdado de
**17**. Os 14 restantes são trabalho de **tradução humana**, não de
reconstrução de mecanismo — e fabricá-los seria inventar conteúdo que não
posso verificar. Um teste fixa os números para que a diferença permaneça
visível.

### D-06.4 — PyYAML é a primeira dependência do Kairos

O RF-12 diz "não introduzir dependência nova — usa o PyYAML já presente". No
Kairos `dependencies` estava **vazio**, então PyYAML *é* nova.

Adicionada mesmo assim: os catálogos são YAML, e o `config.yaml` — que a
Tarefa 15 vai precisar — também. A intenção do requisito é "não adicione uma
dependência **só** para i18n", e ela é respeitada: a mesma biblioteca serve às
duas coisas.

### D-06.5 — Sufixo regional cai para a base, não para o inglês

`pt-BR` resolve para `pt` quando o catálogo base existe. O legado só compara
com a lista exata. Cair no inglês por causa de um sufixo regional é pior que
mostrar português europeu para um usuário brasileiro.

### D-06.6 — Todo caminho de leitura degrada

YAML inválido, arquivo ausente, catálogo que não é mapa, `config.yaml`
quebrado, parâmetro faltante na substituição — nenhum levanta. É a invariante
da unit levada a sério: uma chave pontilhada aparecendo na tela é feia e
diagnóstica, e infinitamente melhor que um `KeyError` no meio de um prompt de
aprovação, que é exatamente quando o usuário mais precisa da interface.

---

## Tarefa 07 — container

### D-07.1 — O shim de exec é uma correção de bug, não uma conveniência

O bug que ele resolve tinha **sintoma contraditório**, e é por isso que era
difícil: os processos supervisionados rodam como UID 10000, mas
`docker exec <c> kairos login` roda como **root** e grava `auth.json` como
`root:root` modo `0600`. A partir daí o gateway responde "authentication
failed" em toda mensagem — **enquanto `docker exec <c> kairos chat -q ping`
continua funcionando**, porque root lê o próprio arquivo. A mesma ferramenta
funciona por um caminho e falha por outro.

O shim fica primeiro no `PATH` e derruba privilégio quando invocado como root.
Custo: um fork extra **só** nesse caminho.

### D-07.2 — A imunidade a `PATH` vem do caminho absoluto, não de sentinela

O shim executa `/opt/kairos/.venv/bin/kairos` por caminho absoluto, de modo
que o segundo salto não pode reentrar nele **independentemente do estado do
`PATH`**. A alternativa comum — uma variável sentinela — falha quando o
ambiente é limpo entre os saltos, que é exatamente o que `/init` faz.

Há um teste que põe um `kairos` hostil no `PATH` e confirma que não há
recursão.

### D-07.3 — Degradar é seguir rodando, e anunciar

O s6-overlay exige ser PID 1. Em Fly Machines, `docker run --init` e algumas
configurações de Nomad/K8s ele nunca será. O dispatcher escolhe o caminho e
**avisa em stderr** que os serviços supervisionados ficam indisponíveis
naquele runtime — mas executa o comando pedido.

Falhar em silêncio seria pior; seguir em silêncio também. O operador precisa
saber por que o dashboard não sobe.

### D-07.4 — A ordem `01 → 015 → 02` é o mecanismo, não erro de digitação

A ordem entre scripts de `cont-init.d` é **lexicográfica**, e é a única
garantia de sequência. `015-supervise-perms` precisa do UID já remapeado pelo
`01`; `02-reconcile-profiles` precisa do `$KAIROS_HOME` já semeado. O `015`
entre `01` e `02` é intencional.

Há um teste que fixa a ordenação — se alguém "corrigir" o `015` para `03`, ele
reprova.

### D-07.5 — O bootstrap não derruba privilégio e não executa o CMD

Duas coisas que o stage2 deliberadamente **não** faz, e ambas por razão:

- **Não derruba privilégio**, porque precisa continuar root para
  `usermod`/`groupmod`/`chown`. A queda acontece por serviço, no `run` de cada
  um.
- **Não executa o CMD**, porque scripts de `cont-init.d` rodam sem argumentos
  — os args do usuário nem chegam ali. É por isso que bootstrap e execução são
  separados, e é o contrato que o shim legado avisa ter mudado.

### D-07.6 — Grep em texto de script casa com o comentário, não com o código

Um teste meu afirmava "o stage2 não usa `s6-setuidgid`" e falhou — porque o
**comentário** que explica por que ele não usa contém a palavra. O teste
afirmava o contrário do que pretendia.

Introduzido `code_only()`, que remove comentários antes de qualquer asserção
de comportamento sobre shell. É uma armadilha genérica: quanto melhor o
comentário explica o que o código *não* faz, mais provável que o grep quebre.

### D-07.7 — O build real achou três bugs que o `--check` não podia achar

`docker build --check` valida a **estrutura** do Dockerfile. Não valida nada
que dependa do sistema de arquivos, de listas mantidas em dois lugares ou do
`PATH` de runtime. Os três bugs mais graves da tarefa só apareceram
construindo a imagem de verdade:

**1. Deriva entre `pyproject.toml` e o Dockerfile.** O `COPY` lista os pacotes
um a um; `kairos_container`, adicionado nesta mesma tarefa, ficou de fora. São
duas listas mantidas à mão, e a deriva é invisível até o `pip install -e .`
falhar. Virou teste: todo pacote declarado precisa ter um `COPY`.

**2. `install` não cria o diretório de destino.** `/opt/kairos/bin`,
`/etc/cont-init.d` e `/etc/s6-overlay/s6-rc.d` não existiam. Erro trivial,
invisível sem executar.

**3. O shim dependia do `PATH` para achar o `s6-setuidgid` — e falhava
exatamente no cenário para o qual existe.** Num `docker exec` cru o `PATH` não
inclui `/command`: quem o semeia é o `/init`, que não roda nesse caminho.
Resultado: o shim caía no fallback e executava **como root**, reintroduzindo o
bug de UID que ele existe para prevenir.

O teste unitário passava porque eu injetava um `s6-setuidgid` stub no `PATH` do
harness. O stub escondia a dependência que era o bug.

### D-07.8 — Não conseguir derrubar privilégio virou recusa, não aviso

Consequência direta do bug 3. A versão anterior avisava em `stderr` e seguia
como root. Isso é **o mesmo bug com passos extras**: ninguém lê o stderr de um
`docker exec ... kairos login`, e o sintoma aparece uma hora depois como falha
de autenticação contraditória.

Agora o shim tenta `/command/s6-setuidgid`, depois
`/package/admin/s6/command/s6-setuidgid`, depois o `PATH`, depois `setpriv` —
e, não conseguindo nenhum, **recusa com exit 77** apontando o opt-out. Falhar
alto agora é melhor que corromper agora e confundir depois. Quem realmente
quer root já tem `KAIROS_DOCKER_EXEC_AS_ROOT=1`.

### D-07.9 — O dashboard pode cair sem derrubar o container

`dashboard/finish` sai com `0`; `main-kairos` **não tem** `finish`. O gateway
define a vida do container, o dashboard é acessório. Foi para poder expressar
isso que o s6 substituiu o tini: um reaper de zumbis não distingue serviço
essencial de acessório.

Confirmado na imagem real: com os dois serviços falhando, o log mostra
`kairos: dashboard encerrou` e o container continua — quem o encerra é o
`main-kairos`.

---

## Ainda em aberto

### `messages.id` continua não sendo estável

`AUTOINCREMENT`, re-sequenciado pela compactação; os consumidores
re-resolvem por conteúdo. Foi **oferecido e não selecionado** na Tarefa 01:
corrigir exige id imutável (ex.: `message_uid` ULID + `seq` de ordenação) e
toca compressão, FTS, watermark, rewind e todos os consumidores — alcance
muito além do schema. Fica registrado como dívida herdada consciente, a ser
revisitada na Tarefa 05 (`hermes-state`) se ainda fizer sentido.
