"""Ferramenta `calendar` — agenda a partir de fonte local `.ics`.

Etapa 3 do plano de ferramentas (P2, opção 1): a fonte é **local** — um
arquivo `.ics` ou um diretório de `.ics` sincronizado — sem conta de terceiros
nem rede. O gate de exibição é o toolset `calendar` (requisito: `calendar.source`
configurado em `<home>/config.yaml` e o caminho existir no disco); o gate de
mutação é por subcomando no Chat (`needs_tool_approval`) — `add`/`rm` pedem
aprovação por turno, `today`/`range` fluem sem prompt (mesmo padrão do `git`).

Limitações honestas: recorrência (RRULE) é **expandida na janela consultada**
(cada ocorrência vira evento; `EXDATE`/`RECURRENCE-ID` ficam fora do escopo);
diretório é leitura — mutação exige arquivo único; datetimes flutuantes são
comparados como fuso local do sistema (sinalizado por evento).
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from dateutil.rrule import rrulestr

from kairos_security.credentials.io import secure_atomic_write_text
from kairos_tools.ics import (
    IcsEvent,
    IcsParseError,
    add_event_to_text,
    format_event_block,
    parse_ics,
    remove_event_by_uid,
)
from kairos_tools.registry import ToolRegistry, registry

#: Subcomandos aceitos — a ferramenta não é um shell de agenda completo.
_ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset({"today", "range", "add", "rm"})

MAX_LIMIT = 200
MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 4000
MAX_DURATION_MINUTES = 30 * 24 * 60

#: Teto de ocorrências expandidas **por evento na janela**. Estourou ⇒ erro
#: fail-closed ("refine o intervalo"), nunca truncamento silencioso de lembrete.
_MAX_EXPANDED_PER_EVENT = 5000

_ISO_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:[Tt ](?P<time>\d{2}:\d{2})(?::(?P<sec>\d{2}))?"
    r"(?P<offset>Z|[+-]\d{2}:?\d{2})?)?$"
)


def _home() -> Path:
    return Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))


def _load_config(home: Path) -> Mapping[str, Any]:
    path = home / "config.yaml"
    if not path.exists():
        return {}
    import yaml

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return config if isinstance(config, Mapping) else {}


def calendar_source(home: Path) -> Path | None:
    config = _load_config(home)
    raw = config.get("calendar", {})
    if not isinstance(raw, Mapping):
        return None
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    path = Path(source.strip())
    if not path.is_absolute():
        path = home / path
    return path


def _calendar_available() -> bool:
    """Requisito do toolset: fonte configurada **e** presente no disco."""
    source = calendar_source(_home())
    return source is not None and source.exists()


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, "success": False, **extra}


def _parse_iso(value: Any, *, campo: str) -> tuple[datetime | None, dict[str, Any] | None]:
    if not isinstance(value, str):
        return None, _error(f"{campo} deve ser data/hora em ISO 8601")
    match = _ISO_RE.match(value.strip())
    if not match:
        return None, _error(f"{campo} fora do formato ISO 8601: {value!r}")
    date = datetime.strptime(match.group("date"), "%Y-%m-%d")
    if match.group("time"):
        hour, minute = map(int, match.group("time").split(":"))
        second = int(match.group("sec") or 0)
        date = date.replace(hour=hour, minute=minute, second=second)
    offset_raw = match.group("offset")
    if offset_raw == "Z":
        return date.replace(tzinfo=UTC), None
    if offset_raw:
        sign = 1 if offset_raw[0] == "+" else -1
        digits = offset_raw[1:].replace(":", "")
        delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]) or 0)
        return date.replace(tzinfo=timezone(sign * delta)), None
    return date, None


def _to_utc(value: datetime) -> datetime:
    # Flutuante apertado: astimezone(UTC) em naíve assume fuso local do sistema.
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat(timespec="seconds")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _serialize_event(event: IcsEvent) -> dict[str, Any]:
    out: dict[str, Any] = {
        "uid": event.uid,
        "titulo": event.summary,
        "inicio": _iso(event.start),
    }
    if event.end is not None:
        out["fim"] = _iso(event.end)
    if event.description:
        out["descricao"] = event.description
    if event.location:
        out["local"] = event.location
    if event.status:
        out["status"] = event.status
    if event.recurrence:
        out["recorrencia"] = "expandida"
        out["rrule"] = event.recurrence
    fuso = event.flag("fuso")
    if fuso:
        out["fuso"] = fuso
    else:
        out["fuso"] = "consciente"
    tzid = event.flag("tzid")
    if tzid:
        out["tzid"] = tzid
    return out


def _shift_occurrence(event: IcsEvent, start: datetime) -> IcsEvent:
    """Cópia do evento com o início da ocorrência; duração e metadados fiéis."""
    if event.end is None:
        return replace(event, start=start)
    return replace(event, start=start, end=start + (event.end - event.start))


def occurrences_in_window(
    events: list[IcsEvent], window_start: datetime, window_end: datetime
) -> list[IcsEvent]:
    """Ocorrências cujo início cai em ``[window_start, window_end)`` (UTC).

    Eventos sem RRULE passam quando a janela pega o início. Recorrentes são
    expandidos **dentro da janela** (nunca a série inteira materializada): cada
    ocorrência vira uma cópia do evento com ``start``/``end`` deslocados.
    Expansão é ancorada no ``DTSTART`` e honra `FREQ`/`INTERVAL`/`COUNT`/
    `UNTIL`/`BYDAY` etc. via `dateutil.rrule`; `EXDATE`/`RECURRENCE-ID` não
    são honrados (escopo documentado). RRULE inválido ou além do teto de
    ``_MAX_EXPANDED_PER_EVENT`` ⇒ ``IcsParseError`` — fail-closed, nunca
    parcial, nunca truncamento silencioso.
    """
    expanded: list[IcsEvent] = []
    for event in events:
        if not event.recurrence:
            if window_start <= _to_utc(event.start) < window_end:
                expanded.append(event)
            continue
        try:
            rule = rrulestr(event.recurrence, dtstart=_to_utc(event.start))
            starts = [
                dt for dt in rule.between(window_start, window_end, inc=True) if dt < window_end
            ]
        except (ValueError, TypeError) as exc:
            raise IcsParseError(f"recorrência inválida ({event.recurrence!r}): {exc}") from exc
        if len(starts) > _MAX_EXPANDED_PER_EVENT:
            raise IcsParseError(
                "recorrência gera mais de "
                f"{_MAX_EXPANDED_PER_EVENT} ocorrências na janela — refine o intervalo"
            )
        expanded.extend(_shift_occurrence(event, start) for start in starts)
    return expanded


def read_events(source: Path) -> tuple[list[IcsEvent], list[dict[str, Any]]]:
    if source.is_dir():
        files = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() == ".ics")
    else:
        files = [source]
    events: list[IcsEvent] = []
    problems: list[dict[str, Any]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append({"arquivo": str(path), "erro": f"leitura falhou: {exc}"})
            continue
        try:
            events.extend(parse_ics(text))
        except (IcsParseError, ValueError) as exc:
            problems.append({"arquivo": str(path), "erro": str(exc)})
    return events, problems


def _bounded(events: list[IcsEvent], limit: int) -> list[IcsEvent]:
    return events[:limit]


def _require_file_source(source: Path) -> dict[str, Any] | None:
    if source.is_dir():
        return _error("fonte em diretório é leitura; mutação exige arquivo único")
    return None


def _run_query(
    *, subcommand: str, now: str | None, desde: str | None, ate: str | None, limite: int | None
) -> dict[str, Any]:
    if type(limite) is not int or not 1 <= limite <= MAX_LIMIT:
        return _error(f"limite deve ser inteiro obrigatório entre 1 e {MAX_LIMIT}")

    source = calendar_source(_home())
    if source is None:
        return _error("fonte de calendário não configurada (calendar.source)")
    if not source.exists():
        return _error(f"fonte de calendário não existe: {source}")

    if subcommand == "today":
        raw_now = now or datetime.now().isoformat(timespec="seconds")
        parsed_now, error = _parse_iso(raw_now, campo="now")
        if error:
            return error
        assert parsed_now is not None
        local = parsed_now if parsed_now.tzinfo is not None else parsed_now.astimezone()
        day_start = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
        window_start = _to_utc(day_start)
        window_end = _to_utc(day_start + timedelta(days=1))
    else:
        parsed_from, error = _parse_iso(desde, campo="desde")
        if error:
            return error
        parsed_to, error = _parse_iso(ate, campo="ate")
        if error:
            return error
        assert parsed_from is not None and parsed_to is not None
        window_start = _to_utc(parsed_from)
        window_end = _to_utc(parsed_to)
        if window_start > window_end:
            return _error("desde deve ser anterior ou igual a ate")

    events, problems = read_events(source)
    if problems:
        return _error(
            "fonte corrompida — leitura recusada (fail-closed), nenhum parcial devolvido",
            arquivos_com_erro=problems,
        )
    try:
        matched = occurrences_in_window(events, window_start, window_end)
    except IcsParseError as exc:
        return _error(
            "fonte inválida (recorrência) — leitura recusada (fail-closed), "
            "nenhum parcial devolvido",
            erro=str(exc),
        )
    matched.sort(key=lambda event: (_to_utc(event.start), event.uid or ""))
    truncated = len(matched) > limite
    serialized = [_serialize_event(event) for event in _bounded(matched, limite)]
    return {
        "success": True,
        "source": str(source),
        "subcommand": subcommand,
        "count": len(serialized),
        "total_no_intervalo": len(matched),
        "truncado": truncated,
        "eventos": serialized,
    }


def _text_fields(
    *, titulo: Any, descricao: Any, local: Any, uid: Any
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(titulo, str) or not titulo.strip():
        return {}, _error("titulo obrigatório e não vazio")
    title = titulo.strip()
    if len(title) > MAX_TITLE_CHARS:
        return {}, _error(f"titulo longo demais (máx. {MAX_TITLE_CHARS} caracteres)")
    if descricao is not None and (
        not isinstance(descricao, str) or len(descricao) > MAX_DESCRIPTION_CHARS
    ):
        return {}, _error(f"descricao deve ser texto até {MAX_DESCRIPTION_CHARS} caracteres")
    if local is not None and (not isinstance(local, str) or len(local) > MAX_TITLE_CHARS):
        return {}, _error(f"local deve ser texto até {MAX_TITLE_CHARS} caracteres")
    if uid is not None and (not isinstance(uid, str) or not uid.strip() or len(uid) > 128):
        return {}, _error("uid deve ser texto até 128 caracteres")
    event_uid = uid.strip() if isinstance(uid, str) and uid.strip() else uuid.uuid4().hex
    return {"title": title, "description": descricao, "local": local, "uid": event_uid}, None


def _event_end(
    start: datetime, fim: Any, duracao_min: Any
) -> tuple[datetime | None, dict[str, Any] | None]:
    if fim is not None and duracao_min is not None:
        return None, _error("informe fim ou duracao_min, não ambos")
    if fim is not None:
        end, error = _parse_iso(fim, campo="fim")
        if error:
            return None, error
        assert end is not None
    elif duracao_min is not None:
        if type(duracao_min) is not int or not 1 <= duracao_min <= MAX_DURATION_MINUTES:
            return None, _error(f"duracao_min deve ser inteiro entre 1 e {MAX_DURATION_MINUTES}")
        end = start + timedelta(minutes=duracao_min)
    else:
        return None, _error("fim ou duracao_min obrigatório")
    if _to_utc(end) <= _to_utc(start):
        return None, _error("fim deve ser posterior ao inicio")
    return end, None


def _resolve_write_source() -> tuple[Path | None, dict[str, Any] | None]:
    source = calendar_source(_home())
    if source is None:
        return None, _error("fonte de calendário não configurada (calendar.source)")
    if not source.exists():
        return None, _error(f"fonte de calendário não existe: {source}")
    blocked = _require_file_source(source)
    if blocked:
        return None, blocked
    return source, None


def _run_add(
    *,
    titulo: Any,
    inicio: Any,
    fim: Any,
    duracao_min: Any,
    descricao: Any,
    local: Any,
    uid: Any,
) -> dict[str, Any]:
    fields, error = _text_fields(titulo=titulo, descricao=descricao, local=local, uid=uid)
    if error:
        return error
    start, error = _parse_iso(inicio, campo="inicio")
    if error:
        return error
    assert start is not None
    end, error = _event_end(start=start, fim=fim, duracao_min=duracao_min)
    if error:
        return error
    assert end is not None
    source, error = _resolve_write_source()
    if error:
        return error
    assert source is not None
    block = format_event_block(
        uid=fields["uid"],
        summary=fields["title"],
        start=start,
        end=end,
        description=fields["description"],
        location=fields["local"],
    )
    return _apply_mutation(source, block, fields["uid"])


def _run_rm(*, uid: Any) -> dict[str, Any]:
    if not isinstance(uid, str) or not uid.strip() or len(uid) > 128:
        return _error("uid obrigatório, texto até 128 caracteres")
    target = uid.strip()
    source, error = _resolve_write_source()
    if error:
        return error
    assert source is not None
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _error(f"leitura da fonte falhou: {exc}")
    new_text, removed = remove_event_by_uid(text, target)
    if removed == 0:
        return _error(f"evento não encontrado (UID: {target})")
    try:
        secure_atomic_write_text(source, new_text)
    except OSError as exc:
        return _error(f"escrita da fonte falhou: {exc}")
    return {"success": True, "uid": target, "removidos": removed, "arquivo": str(source)}


def _apply_mutation(source: Path, block: str, event_uid: str) -> dict[str, Any]:
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _error(f"leitura da fonte falhou: {exc}")
    try:
        new_text = add_event_to_text(text, block)
    except IcsParseError as exc:
        return _error(str(exc))
    try:
        secure_atomic_write_text(source, new_text)
    except OSError as exc:
        return _error(f"escrita da fonte falhou: {exc}")
    return {"success": True, "uid": event_uid, "arquivo": str(source)}


def calendar_tool(
    *,
    subcommand: str,
    now: str | None = None,
    desde: str | None = None,
    ate: str | None = None,
    limite: int | None = None,
    titulo: Any = None,
    inicio: Any = None,
    fim: Any = None,
    duracao_min: int | None = None,
    descricao: str | None = None,
    local: str | None = None,
    uid: Any = None,
) -> dict[str, Any]:
    """Lê e altera a agenda local (.ics). add/rm alteram o arquivo e exigem aprovação."""
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        return _error(f"subcomando não suportado: {subcommand}")
    source = calendar_source(_home())
    if source is None:
        return _error("fonte de calendário não configurada (calendar.source)")
    if not source.exists():
        return _error(f"fonte de calendário não existe: {source}")
    if subcommand in {"today", "range"}:
        return _run_query(subcommand=subcommand, now=now, desde=desde, ate=ate, limite=limite)
    if subcommand == "add":
        return _run_add(
            titulo=titulo,
            inicio=inicio,
            fim=fim,
            duracao_min=duracao_min,
            descricao=descricao,
            local=local,
            uid=uid,
        )
    return _run_rm(uid=uid)


def register_calendar_tool(reg: ToolRegistry | None = None) -> None:
    """Registra `calendar` no toolset próprio, gated pela fonte local existente."""
    r = reg or registry
    r.register_toolset("calendar", requirement=_calendar_available)
    r.register(
        name="calendar",
        handler=calendar_tool,
        schema={
            "type": "function",
            "function": {
                "name": "calendar",
                "description": (
                    "Lê e altera a agenda local (arquivo .ics ou diretório de .ics "
                    "sincronizado, configurado em calendar.source). today e range "
                    "são leitura; add e rm alteram o arquivo e exigem aprovação do "
                    "usuário. Recorrência (RRULE) é expandida dentro da janela "
                    "consultada; EXDATE/RECURRENCE-ID não são honrados."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subcommand": {
                            "type": "string",
                            "enum": ["today", "range", "add", "rm"],
                            "description": "A operação a executar. add/rm exigem aprovação.",
                        },
                        "now": {
                            "type": "string",
                            "description": "Referência ISO 8601 para today (padrão: agora)",
                        },
                        "desde": {
                            "type": "string",
                            "description": "Início ISO 8601 da janela em range",
                        },
                        "ate": {
                            "type": "string",
                            "description": "Fim ISO 8601 da janela em range",
                        },
                        "limite": {
                            "type": "integer",
                            "description": "Máx. de eventos, inteiro obrigatório entre 1 e 200",
                        },
                        "titulo": {
                            "type": "string",
                            "description": "Título do evento (obrigatório em add)",
                        },
                        "inicio": {
                            "type": "string",
                            "description": "Início ISO 8601 (obrigatório em add)",
                        },
                        "fim": {
                            "type": "string",
                            "description": "Fim ISO 8601 em add (alternativo a duracao_min)",
                        },
                        "duracao_min": {
                            "type": "integer",
                            "description": "Duração em minutos em add (alternativo a fim)",
                        },
                        "descricao": {
                            "type": "string",
                            "description": "Descrição do evento (opcional)",
                        },
                        "local": {
                            "type": "string",
                            "description": "Local do evento (opcional)",
                        },
                        "uid": {
                            "type": "string",
                            "description": "UID do evento (rm obrigatório; add gera se ausente)",
                        },
                    },
                    "required": ["subcommand"],
                    "additionalProperties": False,
                },
            },
        },
        toolset="calendar",
        max_result_size_chars=100_000,
    )


#: Registra por padrão — mesmo padrão do `git`, `builtin` e `memory`.
register_calendar_tool()
