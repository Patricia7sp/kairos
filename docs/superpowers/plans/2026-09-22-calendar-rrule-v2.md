# Calendário v2 — expansão de RRULE via `python-dateutil`

**Passo 3/P2 decidido em 22/09/2026:** a usuária autorizou promover
`python-dateutil` a dependência **direta** do Kairos (hoje já transitiva via
`croniter` 6.2.4, `2.9.0.post0` no `uv.lock`) e entregar a expansão de RRULE
prometida como limitação honesta do v1 (PR #77) e do monitor de calendário
(PR #79). O passo 4 (MCP de terceiros) segue depois deste.

## Objetivo

O v1 lia `RRULE` e **marcava** (`"recorrencia": "rrule-nao-expandida"`): uma
recorrência diária era lembrada uma vez só pelo monitor e uma consulta a
janela futura voltava vazia — honesto, mas incompleto. Este lote expande a
recorrência **dentro da janela consultada**:

- `calendar today/range`: cada ocorrência cujo início cai em `[desde, ate)`
  vira um evento na resposta (ordenado por início, `limite` aplicado depois).
- Monitor `calendar`: cada ocorrência na janela `[agora, agora+janela)` tem
  chave própria (`start_utc|uid`) e é lembrada **uma vez por ocorrência** —
  um evento diário lembra todos os dias, não só no primeiro.

## Escopo e limites (honestos)

- Dependência: `python-dateutil>=2.9.0.post0` em `[project].dependencies` +
  `uv lock` (grafo não muda: só sobe de transitiva para direta).
- Expansão é **por janela, nunca materializa a série inteira**: `rrule.between`
  limitado a `[janela_inicio, janela_fim]`, com teto de
  `_MAX_EXPANDED_PER_EVENT = 5000` ocorrências por evento na janela — estourou
  ⇒ `IcsParseError` (fail-closed: "refine o intervalo", nunca truncamento
  silencioso de um lembrete).
- `FREQ`/`INTERVAL`/`COUNT`/`UNTIL`/`BYDAY`/etc. vêm do motor do `dateutil`
  (`rrulestr`); `DTSTART` é a primeira ocorrência; a série é ancorada nele.
- Datetimes: mesmo tratamento do v1 — `Z`/`TZID` resolvível ⇒ ciente,
  flutuante ⇒ fuso local do sistema (sinalizado). A expansão opera em UTC
  (`_to_utc`) para casar com a janela.
- **Fora do escopo (documentado, não fingido):** `EXDATE`, `RECURRENCE-ID`,
  cancelamento por ocorrência e `VTIMEZONE` próprio continuam ignorados
  (metadados fora de `_EVENT_FIELDS`) — expansão honra só o `RRULE` do
  VEVENT. Mutação (`add`) continua escrevendo evento único; criar recorrência
  via ferramenta não faz parte deste lote.
- `parse_ics` continua puro/stdlib: a expansão vive na camada de consulta
  (`kairos_tools/calendar.py`), não no parser.

## Entrega

1. `pyproject.toml` — `python-dateutil>=2.9.0.post0` em `dependencies`;
   `uv lock` atualizado (job de lock do CI precisa passar).
2. `kairos_tools/calendar.py`:
   - `occurrences_in_window(events, window_start_utc, window_end_utc) ->
     list[IcsEvent]` — para cada evento, devolve as ocorrências com início em
     `[inicio, fim)`; não recorrente ⇒ o próprio evento se caber; recorrente
     ⇒ `rrulestr(event.recurrence, dtstart=_to_utc(event.start)).between(...)`
     com cópia do `IcsEvent` deslocada (`dataclasses.replace`, duração
     preservada, `recurrence`/`flags` mantidos). `ValueError`/excesso de teto
     ⇒ `IcsParseError` nomeado.
   - `_run_query`: `matched = occurrences_in_window(...)` (antes `_matches`);
     erro de expansão ⇒ resposta `success: False` fail-closed, mesmo tom do
     arquivo corrompido.
   - `_serialize_event`: `rrule-nao-expandida`/`observacao` saem; evento com
     `recurrence` agora emite `"recorrencia": "expandida"` + `"rrule": <regra
     crua>` (transparência do que foi expandido).
   - Docstring e descrição da ferramenta atualizadas: RRULE expandido na
     janela; `EXDATE` fora do escopo.
3. `kairos_cron/calendar_monitor.py`:
   - `check_calendar_monitor` usa `occurrences_in_window` (substitui o filtro
     `_window_match` por evento base) — chaves por ocorrência já distinguem
     dias distintos pelo próprio `start_utc`.
   - Docstring: lembrete **uma vez por ocorrência** (não mais "uma vez por
     UID"); `EXDATE` fora do escopo.
   - `render_calendar_reminder` inalterado (opera na lista de ocorrências).
4. `kairos_tools/ics.py` — só docstring do campo `recurrence` (a expansão é
   da camada de consulta; o parser segue marcando o cru).
5. `docs/plano-ferramentas.md` — linha do "Escopo honesto do v1" passa a
   registrar a expansão como entregue (v2).
6. Testes (comportamento, sem ler fonte):
   - `tests/test_calendar_tool.py`: `test_recorrencia_e_marcada_mas_nao_expandida`
     vira `test_recorrencia_e_expandida_na_janela` (janela futura devolve
     ocorrências com `recorrencia == "expandida"` e `rrule` cru; janela base
     devolve a de `DTSTART`); novo caso de `COUNT` finito; novo caso de
     `RRULE` inválido ⇒ `success: False` fail-closed.
   - `tests/test_cron_calendar_monitor.py`: recorrência diária lembra na
     primeira ocorrência e **lembra de novo** no dia seguinte (chave nova),
     e suprime no mesmo dia (chave repetida).
   - `tests/test_ics_parse.py`: parser continua guardando o cru (asseveração
     de parse, não de expansão).

## Gate

Suíte local inteira verde (`-m 'not runtime_live'`) + `uv run ruff check .`/
`format --check` + `uv lock --check` + `scripts/ci.sh --fast` antes do PR;
CI 10/10 verde antes do merge. Após merge, `git diff HEAD~1..HEAD` sem
remoções acidentais e registro em `docs/functional-progress.md`.
