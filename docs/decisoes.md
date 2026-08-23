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

## Ainda em aberto

### `messages.id` continua não sendo estável

`AUTOINCREMENT`, re-sequenciado pela compactação; os consumidores
re-resolvem por conteúdo. Foi **oferecido e não selecionado** na Tarefa 01:
corrigir exige id imutável (ex.: `message_uid` ULID + `seq` de ordenação) e
toca compressão, FTS, watermark, rewind e todos os consumidores — alcance
muito além do schema. Fica registrado como dívida herdada consciente, a ser
revisitada na Tarefa 05 (`hermes-state`) se ainda fizer sentido.
