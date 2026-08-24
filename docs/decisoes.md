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

## Fechamento de pendências (antes da Tarefa 08)

### D-X.1 — O registro de invariantes pegou dois apontando para tarefa concluída

Os invariantes **6** e **11** estavam marcados `DEFERRED` para a Tarefa 05 —
que fechou. Dívida invisível: o registro parecia em ordem enquanto ninguém os
havia imposto. É exatamente o que ele existe para pegar, e o mecanismo só vale
se for consultado.

Acrescentado `test_nenhum_invariante_aponta_para_tarefa_ja_concluida`, para a
próxima ocorrência não depender de alguém lembrar de olhar.

### D-X.2 — Invariante 6: a verificação é a imposição

*"Linhas canônicas nunca são modificadas pelo reparo."* `repair_derived_objects`
recria índices FTS, views e gatilhos, e compara a **impressão digital** das
tabelas canônicas antes e depois — levantando se mudou.

Um comentário dizendo "não toque em `messages`" não é imposição. A distinção
importa mais no reparo do que em qualquer outro caminho: um índice corrompido
é reconstruível, o transcript não, e confundir os dois num momento de pânico é
como se perde o dado do usuário.

A impressão digital é `(contagem, max(rowid))` por tabela. Não detecta edição
de conteúdo em linha existente — deliberadamente: o reparo não tem caminho que
faça isso, e um hash de conteúdo custaria varredura completa a cada
verificação.

### D-X.3 — Invariante 11 vive no harness, e ganhou um nível de imposição

É o único cuja violação vem do **teste**, não do código de produção — então a
imposição não pode morar no código de produção. `tests/conftest.py` tem duas
fixtures `autouse`:

1. **Escopo de sessão**: aponta `KAIROS_HOME` para um diretório descartável.
   Cobre o caminho normal.
2. **Por teste**: compara mtime e tamanho do `~/.kairos/state.db` real antes e
   depois. Cobre o resto — caminho absoluto escrito à mão, `Path.home()`
   direto, default que escapou.

`autouse` é essencial: a proteção não pode depender de cada teste lembrar de
pedi-la, porque é o esquecimento que ela cobre.

Introduzido o nível `Enforcement.HARNESS` para distinguir este caso — chamá-lo
de `DOMAIN` seria mentir sobre onde ele é imposto.

---

## CI

### D-CI.1 — Um workflow, seis jobs — não 28 arquivos

O legado tem 28 workflows porque tem 28 superfícies. Aqui a divisão é por
**ferramenta**, e um job existe quando falha por um motivo distinto dos
outros. Um job a mais que não distingue nada só adiciona espera.

O `image` é separado do `tests` de propósito: uma falha de build não pode se
confundir com falha de lógica no relatório.

### D-CI.2 — Actions fixadas por SHA

Tag é mutável. Confiar nela significa executar código arbitrário do mantenedor
da action a cada push. O legado já faz assim; herdado.

### D-CI.3 — `scripts/ci.sh` existe para o CI não ser ficção

O repositório ainda não tem remoto — o workflow não roda em lugar nenhum.
Sem um espelho local, ter o arquivo seria fingir cobertura.

Vale mesmo depois do remoto existir: descobrir uma quebra no push é um ciclo
de minutos, aqui é de segundos. E o script **pula com aviso** em vez de
silenciosamente quando falta ferramenta — pular calado seria a mesma ficção em
menor escala.

O script é linted por ele mesmo, e não passou na primeira tentativa.

### D-CI.4 — Conjunto de regras deliberado, e cada exceção argumentada

`[tool.ruff.lint.select]` lista famílias escolhidas, não o default inteiro.
Cada `ignore` traz o motivo em uma linha; um ignore sem justificativa é dívida
disfarçada de configuração.

A distinção que guiou o triagem dos 60 achados iniciais:

- **Cosmético** (`I`, `RUF022`, `UP`) → auto-corrigido.
- **Real** (`F401`, `B017`, `RUF012`, `PLW2901`) → corrigido de fato. O `B017`
  era um `assertRaises(Exception)` que passaria até com `TypeError` de
  assinatura errada — o teste tinha deixado de significar o que dizia.
- **Deliberado** (`BLE001`, `S110`, `S311`, `S608`) → `noqa` **por sítio, com
  justificativa**, nunca regra desligada em bloco. Regra de segurança
  silenciada globalmente deixa de proteger o caso que ninguém previu.

### D-CI.5 — Formatador adotado agora, que é o momento mais barato

`ruff format` reformataria 27 arquivos. Inspecionei o diff num arquivo antes
de decidir: mudanças de quebra de assinatura, sem tocar comentários nem
tabelas. Adotado e passa a ser exigido — o custo só cresce com o tempo.

### D-CI.6 — `str, Enum` → `StrEnum`

`UP042`. Além de modernizar para o alvo 3.11, resolve a razão de existir do
helper `_label()` de `statemachine.py`: com `StrEnum`, `str(membro)` já devolve
o valor em vez de `Classe.MEMBRO`. Os 310 testes passaram sem alteração,
incluindo o `Platform._missing_` que cria membros dinâmicos.

### D-CI.7 — O hadolint achou um bug de tamanho de imagem

`DL3046`: `useradd` com UID alto sem `-l` cria entradas em `lastlog`/`faillog`
indexadas por UID, gerando arquivos esparsos de centenas de MB. Corrigido, não
silenciado — e o teste que fixava a string exata do `useradd` quebrou junto,
o que o expôs como específico demais: agora afirma a **propriedade**, não a
ordem das flags.

---

## Tarefa 08 — tools

### D-08.1 — A ordem das camadas de aprovação é a política (T-27)

`resolve()` é o **ponto de entrada único**, e isso é o desenho, não
conveniência. Expor as camadas individualmente permitiria a um chamador
consultá-las fora de ordem — e um `if allowlist: allow` escrito antes do
`user_deny` em qualquer lugar do código anularia a camada 4.

A propriedade que não pode se perder, com teste que a fixa:

```
4. approvals.deny    ← ACIMA do bypass
5. yolo / mode=off   ← ABAIXO da negação do usuário
6. command_allowlist
```

**`--yolo` amplia o que é permitido e nunca alcança o que foi proibido.**
`Layer` é `IntEnum` justamente para que a ordem seja comparável em teste, e
`Decision.bypassable` expõe a propriedade sem que o chamador precise conhecer
os números.

### D-08.2 — As duas camadas veem exatamente as mesmas variantes

A desofuscação (`detection_variants`) alimenta tanto o `approvals.deny` quanto
a detecção de padrão perigoso. Se divergissem, a ofuscação viraria caminho de
contorno de uma e não da outra — a pior combinação possível, porque criaria a
impressão de proteção onde ela é seletiva.

`r\m -rf /`, `g\i\t st""atus` e `rm${IFS}-rf${IFS}x` recebem o mesmo veredito
das formas simples.

### D-08.3 — A mensagem de recusa fala com o modelo, não com o humano

*"NÃO tente de novo nem reformule o comando; o usuário o proibiu
explicitamente."* Uma recusa genérica inicia uma busca por sinônimo: o modelo
tenta `unlink` depois de `rm`, `python -c "os.remove"` depois disso. A recusa
precisa **encerrar a tentativa**.

### D-08.4 — Allowlist e blocklist, cada uma onde faz sentido

Sandbox por **allowlist** (7): ferramenta nova nasce invisível até alguém
decidir o contrário. Uma blocklist esqueceria o que ainda não existe.

Delegação por **blocklist** (5): o subagente é o mesmo agente com menos
autoridade, então herdar tudo menos exceções nomeadas é o default correto. E
cada exceção carrega o **motivo** — é o que impede a lista de virar folclore,
e `delegate_block_reason` devolve o motivo em vez de um booleano porque sem
ele o modelo tenta por outro caminho achando que foi acidente.

Ambas `frozenset`: política mutável em runtime não é política, é sugestão.

### D-08.5 — Requisito não atendido OMITE a ferramenta

Um schema que o modelo pode chamar e que sempre erra é pior que ferramenta
ausente: ele tenta, falha, e tenta de novo achando que errou os argumentos.
E um requisito que **levanta** conta como não satisfeito — propagar
transformaria a montagem do prompt inteiro em falha por causa de uma sonda de
ambiente.

### D-08.6 — Correção de spec: o critério de paralelizabilidade existe

A unit marcava 🔴: *"o critério que decide quais ferramentas de um lote são
paralelizáveis não foi localizado"*. Ele existe, em
`agent/tool_dispatch_helpers.py:44-73` — a busca anterior olhou `tools/`, e
ele mora em `agent/`.

São quatro classes, e a terceira é a interessante: **arbitragem por
sobreposição de caminho**. Leitores compartilham subárvore entre si; um
escritor conflita com qualquer reserva sobreposta. A razão está no próprio
código: impedir que um `search_files`/`read_file` em lote observe estado
**pré-mutação** quando o modelo os agrupa junto com o `patch`/`write_file` de
que dependem — *"the classic same-block write→read race"*.

### D-08.7 — Dois testes meus afirmavam a propriedade errada

Ao testar a segmentação, assumi que "ordenado" significava "segmentos
separados". Não significa: trechos sequenciais adjacentes são **fundidos**, e
a fusão preserva a ordem. A propriedade real é **não estarem na mesma corrida
paralela**, e virou o helper `assert_ordenados`.

O segundo era mais sutil: misturei caminho absoluto no corpo do patch com
caminho absoluto no leitor, sem perceber que cabeçalhos de patch são
**relativos à raiz do repositório**. O teste realista usa a forma relativa nos
dois lados. E acrescentei o caso complementar: o leitor do `path=` obsoleto
**não** deve ser ordenado atrás, porque o patch não toca aquele arquivo.

### D-08.8 — O teste de deriva do Dockerfile funcionou

`kairos_tools` entrou no `pyproject` e não no `COPY`. O teste criado na
Tarefa 07 — depois de o build real pegar exatamente isso com
`kairos_container` — reprovou sozinho, antes de qualquer build. É a diferença
entre aprender com um bug e prevenir o próximo.

---

## Tarefa 09 — skills

### D-09.1 — A política de divergência não é decisão nova: é herança

`questions.md#pergunta-8` registrou *"a edição local vence e a sincronização
apenas avisa"* como **decisão de produto do Kairos**, com a ressalva de que o
comportamento do legado "não foi lido".

Foi lido agora, e **o legado já faz exatamente isso**. A docstring de
`tools/skills_sync.py:12-20` descreve a política letra por letra: *"If bundled
changed and user copy differs: user customized it → SKIP"* e *"DELETED by
user: respected, not re-added"*.

A resposta não muda; a **natureza** muda — de "decisão a tomar" para
"mecanismo existente a preservar". A diferença importa: reproduz-se algo
testado em vez de inventar.

### D-09.2 — O caso do meio é o inteligente

A lógica de atualização tem três casos, e o segundo é o que faz o mecanismo
funcionar:

1. Bundled casa com o `origin_hash` → pula **sem ler** a cópia do usuário.
2. Bundled mudou **e** a cópia do usuário casa com o `origin_hash` → atualiza.
3. Bundled mudou **e** a cópia difere → preserva e avisa.

A comparação do caso 2 é contra o **hash de origem registrado**, não contra o
bundled atual. Comparar contra o atual não distinguiria nada — e há um teste
para o caso adversarial: o usuário edita para exatamente o conteúdo do próximo
bundled. Contra o atual pareceria "igual"; contra a origem, é edição, e é
preservada.

### D-09.3 — Manifesto v1 migra de forma conservadora

Entrada v1 (sem hash) vira hash vazio, e hash vazio cai no caso 3 —
preservação. Sem conhecer a origem, não há como afirmar que o usuário não
editou, e a suposição segura é que editou.

### D-09.4 — O hash cobre o diretório, não só o `SKILL.md`

Uma skill é um diretório: `SKILL.md` mais `references/`, `scripts/`,
`templates/`. Hashear só o `SKILL.md` deixaria a edição de um script passar
por "inalterada" e ser sobrescrita.

### D-09.5 — O limite de 60 caracteres é funcional

`SKILL_PROMPT_DESC_LIMIT = 60`. O índice de skills é carregado em **toda
sessão** e trunca a descrição; o que passa é cortado em silêncio e **a skill
nunca roteia**, porque o modelo decide carregá-la a partir da versão truncada.

Uma descrição de 80 caracteres não produz um índice feio: produz uma skill que
nunca é usada. A mensagem de erro diz isso, e diz quantos caracteres sobraram.

### D-09.6 — `author` nunca vem do ambiente

Nem de login, nem de git config. Skills são compartilhadas e publicadas, e um
nome vindo dali seria *"a privacy leak the user never opted into"*. Ausente é
ausente — o validador aceita `None` e recusa string vazia.

### D-09.7 — A quarentena é etapa obrigatória, não gatilho de suspeita

Download → quarentena → scan → instalação, sem caminho alternativo. Uma
quarentena condicional só protege contra o que a condição prevê.

Três defesas, cada uma cobrindo o que a outra não cobre:

- **Nome de arquivo dentro do bundle é entrada não confiável** — um
  `../../.bashrc` escaparia antes de qualquer scan, então a contenção acontece
  na **gravação**, não só na promoção.
- **Contenção por caminho resolvido** na promoção — sem `resolve()`, um `..`
  no meio passaria.
- **Symlink rejeitado duas vezes**, no scan e na promoção: entre os dois o
  conteúdo pode mudar. Um link para `~/.ssh/id_rsa` transformaria `skill_view`
  num leitor de arquivo arbitrário.

### D-09.8 — Skill protegida devolve o motivo, não some

`apply_automatic_transitions` devolvia só as transições; o linter apontou um
`try/except/continue` silencioso e tinha razão. Passou a devolver
`PruneResult` com `protected: {nome: motivo}`.

Uma skill que nunca é arquivada e ninguém sabe explicar por que vira mistério
operacional — e o relatório do passe existe exatamente para isso. Uma
protegida também não aborta a poda das demais.

### D-09.9 — A evidência prevalece sobre a declaração

`reconcile_classification` cruza o que o modelo **declarou** absorver com o
que as chamadas de ferramenta **provam**. Divergindo, a evidência vence:
declaração é intenção, chamada de ferramenta é fato.

Mas a divergência **não é descartada** — vai para `discrepancies`. Um modelo
que declara sistematicamente o que não faz é sinal de problema no prompt, e
apagar o sintoma esconderia isso.

### D-09.10 — Correções de spec: dois 🔴 e um 🟡

- **`skills/` vs `optional-skills/`** — são três diretórios: 82 bundled
  auto-semeadas, 117 oficiais não ativadas, e o runtime em `~/.kairos/skills/`.
- **Protocolo de sync** — manifesto v2 `nome:origin_hash`, com auto-migração
  de v1 e os três casos acima.
- **O 🟡 da pergunta 8** — corrigido para herança, ver D-09.1.

---

## Tarefa 10 — plugins

### D-10.1 — G-21 fechado por capacidade declarada, não por convenção

O alerta: o payload de `transform_api_error_classification` pode carregar
*"an unredacted provider error dump"*. No legado o controle existe apenas como
cláusula de docstring — *"callbacks must not log or forward them without
redaction"* — sem barreira técnica nenhuma. Uma convenção documentada protege
contra o descuido honesto e contra mais nada.

**Decisão:** redação é o **default**; receber o dump cru exige
`requires_raw_error: true` no manifesto. A autorização é **por plugin**, não
global — dá para listar quem a pediu, e um plugin curioso instalado ao lado de
um confiável continua vendo o campo redigido.

### D-10.2 — *Run-all*, não *stop-at-first*

`invoke_hook` executa **todos** os callbacks; uma resposta cedo nunca impede
os seguintes. Muitos hooks têm efeito colateral legítimo (telemetria, log), e
curto-circuitar entregaria comportamento que depende da **ordem de
instalação** — que o usuário não controla nem enxerga.

O desempate acontece depois, sobre os resultados, e o perdedor vai para
`skipped` com warning: dois plugins disputando a mesma classificação é
conflito de configuração, e silenciá-lo faria o usuário depurar por
adivinhação.

### D-10.3 — `kind` desconhecido não rejeita o plugin

Coage para `standalone` com warning. Rejeitar quebraria todo plugin escrito
contra uma versão futura com um `kind` novo, e a coerção degrada para o
comportamento **mais restrito**, não para o mais permissivo.

O contraste com `provides_hooks` é deliberado: hook inexistente **é** recusado,
porque um hook que nunca dispara deixa o plugin "instalado e sem fazer nada" —
falha silenciosa em vez de degradação anunciada.

### D-10.4 — Falha suave de requisito de ambiente

Plugin sem a variável vai para `disabled_missing_env` e **continua visível**,
dizendo o que falta. Não é erro de instalação: é configuração pendente, e
omitir o plugin faria o usuário procurar por que ele "não instalou".

### D-10.5 — O dado do plugin mora fora da árvore de instalação

`<kairos home>/plugin-data/<nome>/`, nunca dentro de `plugins/<nome>/`. Aquela
árvore é gerenciada pelo gerenciador: `remove` a apaga e `update` faz git-pull
dentro dela. Dado estacionado ali **morre com o código que o escreveu**, e o
usuário não tem como prever isso.

E `KAIROS_HOME` é resolvido **a cada chamada**: o perfil ativo pode mudar no
meio da vida do processo, e um caminho cacheado escreveria no perfil errado.

### D-10.6 — Aridade não discrimina plugin

Anti-padrão **Won't** de `architecture.md`. Callbacks com assinaturas
diferentes convivem no mesmo hook, e há teste para isso — o despacho passa
`**kwargs` e deixa o callback pegar o que quiser.

---

## Tarefa 11 — providers-gateway

### D-11.1 — `OMIT_TEMPERATURE` é sentinela porque `None` é valor legítimo

`temperature=None` é aceito por alguns provedores, então `if temperature is
None` não distinguiria "não definido" de "explicitamente nulo". E modelos de
raciocínio (`o1`, `o3`) **rejeitam** o parâmetro: a chave precisa **sumir do
corpo**, não ir com valor.

### D-11.2 — Precedência por camada, não por ordem de importação

Last-writer-wins **dentro** da camada; camada mais alta sempre vence. Sem a
ordem por camada, a precedência dependeria da ordem de importação — que muda
com o sistema de arquivos e é impossível de depurar.

### D-11.3 — O `User-Agent` não é telemetria

WAFs de provedor devolvem **403** para o User-Agent padrão do `urllib`. Sem o
header, o provedor parece fora do ar e o erro não diz nada sobre o motivo
real. Vai em toda requisição, montado por `build_request_kwargs`.

### D-11.4 — Os 7 eventos são congelados, e os nomes da spec anterior não existem

`architecture.md` já registrava que a lista antiga (`StreamDelta`,
`ToolCallComplete`, `TurnFinished`…) não corresponde a nada no repositório. Há
teste que reprova se algum daqueles nomes reaparecer.

Todos `frozen=True`: um evento mutável depois de emitido produz corrida entre
consumidor e produtor do stream.

### D-11.5 — `CapabilityDescriptor` declara em vez de sobrescrever

Substitui a herança cega de um `BasePlatformAdapter` com 131 métodos, que a
`architecture.md` marca como violação da Lei 2.

Acrescentei uma coerência que a spec não pedia: `supports_draft_streaming`
exige `supports_edit`. Prometer streaming progressivo sem saber editar produz
uma UI que nunca atualiza — falha silenciosa em vez de erro de configuração.

### D-11.6 — Falha permanente não tem cooldown

O circuit breaker distingue transitório de permanente. Não há espera que
conserte um canal deletado ou um usuário que bloqueou o bot — pôr esses casos
na escada faria o gateway tentar para sempre, cada tentativa consumindo uma
conexão do pool e um slot de rate limit de que a conversa **viva** ao lado
precisava.

E sucesso **zera** o contador: a escada mede falha consecutiva, não acumulada.

### D-11.7 — A obrigação só é quitada pelo adapter

Marcar entregue antes da confirmação seria fingir a entrega. O modo de falha
coberto: um cron job produz resposta, o processo morre antes do envio, e
ninguém percebe — porque não havia registro de que a entrega era devida.

Recuperação exige **prova de morte** (`pid` + `started_at`), a mesma convenção
do cron: o PID sozinho é reciclado pelo SO.

---

## Tarefa 12 — mcp

### D-12.1 — T-24 decidido: as ferramentas de aprovação NÃO são publicadas

O 🔴 pedia uma das duas: implementar IPC real com o gateway, ou não publicar.
**Decidi não publicar.**

O diagnóstico de G-28 já mostrava que `_pending_approvals` nunca é populado e
que `respond_to_approval` é *"best-effort without gateway IPC"*. Publicar
manteria uma ferramenta que devolve `{"resolved": true}` **sem efeito** — e o
projeto já estabeleceu a regra: *uma ferramenta que reporta sucesso sem efeito
é pior que uma ferramenta ausente*. O cliente acredita ter aprovado, e nada
aprovou.

A IPC real é escopo do gateway, não desta unit. `UNPUBLISHED_TOOLS` guarda os
dois nomes **com o motivo**, para que a ausência seja decisão registrada e não
esquecimento.

### D-12.2 — T-21: a ordem entre as duas camadas é o requisito

`MCP_HARD_RESULT_CAP_CHARS = 2_000_000`, **40× acima** do limiar de spillover.
A ordem é o que importa:

- resultado grande **normal** → chega **íntegro** ao spillover (disco + preview)
- enxurrada **patológica** → truncada aqui, com perda

Um teto no nível do spillover seria proteção correta no lugar errado. Há teste
comparando as duas constantes.

O corte é 40% cabeça / 60% cauda porque erro e conclusão vivem no fim da
saída: cortar só a cabeça descartaria justamente a parte que responde.

### D-12.3 — T-24b: a ausência de autenticação fica escrita, e condicionada

O servidor é stdio puro e a fronteira é o SO — quem spawna já tem os
privilégios do usuário, e um token protegeria contra nada, porque quem inicia
o processo pode lê-lo.

`AUTHENTICATION_RATIONALE` registra isso **e** a condição de revisão: se
houver transporte remoto, a decisão precisa ser revista **antes** de o
transporte existir. Há teste que reprova se essa cláusula sumir.

### D-12.4 — T-24c: a armadilha da correção óbvia

O bug (#13414) é o bridge despejar meses de histórico no cliente MCP ao subir.
A correção óbvia — baselinar por marca de tempo de início — **perde a primeira
mensagem de uma conversa nova**, porque a conversa não existia no start e
portanto não tem baseline. É exatamente o caso que mais importa.

Baseline **por sessão**, e sessão desconhecida entrega desde a primeira
mensagem. Há teste para os dois lados.

### D-12.5 — Validação de config nas duas pontas

Formas de abuso são recusadas **no salvamento e de novo no spawn**. Validar só
na gravação protegeria apenas o caminho que passa pela UI — e o `config.yaml`
é editável à mão.

O que se recusa: comando que é um shell (`sh`, `bash`, `powershell`) e
argumento com marcador de shell. Sem isso, uma entrada de config vira execução
de comando arbitrário com a inocência de um campo de texto.

### D-12.6 — Cache corrompido conta como cache ausente

Levantar ao ler um manifesto corrompido impediria o boot por causa de um
arquivo **descartável**. O cache existe para não acordar processos stdio a
cada montagem de prompt; sua ausência custa uma partida, não uma falha.

---

## Ainda em aberto

### `messages.id` continua não sendo estável

`AUTOINCREMENT`, re-sequenciado pela compactação; os consumidores
re-resolvem por conteúdo. Foi **oferecido e não selecionado** na Tarefa 01:
corrigir exige id imutável (ex.: `message_uid` ULID + `seq` de ordenação) e
toca compressão, FTS, watermark, rewind e todos os consumidores — alcance
muito além do schema. Fica registrado como dívida herdada consciente, a ser
revisitada na Tarefa 05 (`hermes-state`) se ainda fizer sentido.
