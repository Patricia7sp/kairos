# Calendário / lembretes — fonte local, ferramenta gated (etapa 3)

**Item 4/P2 decidido em 21/09/2026:** a usuária autorizou a **opção 1** do plano
(`docs/plano-ferramentas.md`): fonte local (arquivo `.ics` ou diretório de
`.ics` sincronizado), entregue na **etapa 3** — ferramenta gated por
`Toolset.requirement` —, zero conta de terceiros, sem nova dependência externa.
Expansão de recorrência (RRULE) e CalDAV via rede ficam **fora do v1** (abaixo).

## Objetivo

O `kairos_cron` já agenda e entrega; falta a **fonte**. Esta entrega adiciona a
ferramenta `calendar` ao toolset, espelhando a entrega P1 do `git`
(`kairos_tools/git.py`):

- **Gate de exibição** por `Toolset.requirement`: só aparece quando a configuração
  `calendar.source` existe no home (`<home>/config.yaml`) e o caminho aponta para
  algo que existe no disco (arquivo `.ics` ou diretório com `.ics`). Fonte
  ausente ⇒ ferramenta **omitida** do schema que o modelo vê — nunca exposta
  para falhar na chamada (regra de `registry.get_definitions`).
- **Gate de mutação** por subcomando no Chat
  (`kairos_integration.chat_tools.needs_tool_approval`): `add` e `rm` pedem
  aprovação por turno; `today` e `range` fluem sem prompt (mesmo padrão do
  `GIT_MUTATING_SUBCOMMANDS`).

## Escopo v1

- Leitura: `today` (eventos do dia, janela local) e `range` (`desde`/`ate`
  ISO, `limite` 1–200, padrão 50). Fonte = um arquivo `.ics` ou todos os
  `*.ics` de um diretório (ordem alfabética dos arquivos).
- Mutação (fonte **obrigatoriamente arquivo único**; diretório é leitura):
  `add` (título, início, fim ou duração em min, descrição/UID opcionais) e
  `rm` (por UID). Escrita atômica via `secure_atomic_write_text`.
- Parse `.ics` próprio (stdlib, sem dependência nova): VCALENDAR/VEVENT,
  dobramento de linhas (folding), escapes (`\,` `\;` `\\` `\n`),
  `ENCODING=QUOTED-PRINTABLE`, `TZID=...`/sufixo `Z`/flutuante, `DTEND` ou
  `DURATION` (`PTnH/nM/nS`, `PnD`), `SUMMARY`/`DESCRIPTION`/`LOCATION`/`STATUS`/`UID`.
- **Recorrência honorável:** `RRULE` é lido e marcado, porém **não expandido**
  — cada evento recorrente entra como a ocorrência de `DTSTART` com o campo
  `"recorrencia": "rrule-nao-expandida"`. Nunca silencioso. (O `croniter` da
  base 6.2.4 não aceita expressões RRULE; sem nova dependência, a expansão é
  de propósito.)
- Resultados: datetimes ISO com offset quando conscientes; flutuantes saem
  naíves e comparados como **fuso local do sistema** (suposição documentada
  por evento: `"fuso": "local-assumido"`).

## Fora do v1 (registrado, não fingido)

- Expansão de RRULE (exige autorização de dependência tipo
  `recurring-ical-events` ou motor próprio).
- CalDAV via rede / VTIMEZONE / timezone-lookup de `TZID` não resolvível por
  `zoneinfo` (virou flutuante com aviso).
- `MUTATING_TOOLS` na tela de inventário: `calendar` aparece `mutating: False`
  porque o flag web espelha só `MUTATING_TOOLS`; a mutação por subcomando é
  resposta de `needs_tool_approval` (mesma assimetria já aceita para o `git`).

## Entrega (espelhando o `git`)

1. `kairos_tools/ics.py` — parser/escritor puro (sem env, sem rede),
   fail-closed: arquivo `.ics` corrompido ⇒ erro nomeado, nunca leitura parcial
   silenciosa; arquivo sem `END:VCALENDAR` em mutação ⇒ erro.
2. `kairos_tools/calendar.py` — `register_calendar_tool(reg)` registra o
   toolset `calendar` com `requirement=_calendar_available` e a ferramenta com
   schema `additionalProperties: False`; resolução de `calendar.source`:
   relativo ⇒ dentro do home; segredos não entram (`config.yaml` é config
   não-secreta; cofre continua para credenciais de rede).
3. `kairos_tools/__init__.py` — exporta `register_calendar_tool`.
4. `kairos_integration/chat_tools.py` — `CALENDAR_MUTATING_SUBCOMMANDS =
   {"add","rm"}`; `calendar` em `CHAT_TOOLS`; `needs_tool_approval` com o mesmo
   ramo do `git`.
5. Testes de comportamento (unittest, sem ler fonte):
   `tests/test_ics_parse.py` (parser) e `tests/test_calendar_tool.py` (gate —
   canônico `all(d["function"]["name"] != "calendar" ...)` —, today, range,
   add/rm com estado real no disco, aprovação por subcomando, fonte
   diretório=só leitura, arquivo corrompido=fail-closed).

## Gate

Suíte local inteira verde + `uv run ruff check .`/`format --check` + jack
`scripts/ci.sh --fast` antes do PR; CI 10/10 verde antes do merge
(disponibilidade pública não é pré-condição do recorte). Após merge, `git diff
HEAD~1..HEAD` sem remoções acidentais e registro em `docs/functional-progress.md`.