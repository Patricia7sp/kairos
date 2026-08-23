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

### D-01.6 — Suíte em `unittest`, não `pytest`

A suíte usa `unittest` da biblioteca padrão para rodar sem dependência
nenhuma. `pytest` continua executando-a sem alteração, e permanece em
`[project.optional-dependencies].dev`.

---

## Ainda em aberto

### `messages.id` continua não sendo estável

`AUTOINCREMENT`, re-sequenciado pela compactação; os consumidores
re-resolvem por conteúdo. Foi **oferecido e não selecionado** na Tarefa 01:
corrigir exige id imutável (ex.: `message_uid` ULID + `seq` de ordenação) e
toca compressão, FTS, watermark, rewind e todos os consumidores — alcance
muito além do schema. Fica registrado como dívida herdada consciente, a ser
revisitada na Tarefa 05 (`hermes-state`) se ainda fizer sentido.
