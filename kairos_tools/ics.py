"""Parse/escritor mínimo de iCalendar (`.ics`) — stdlib, sem rede.

Subconjunto deliberado e honesto: `VCALENDAR` + `VEVENT`, com dobramento de
linhas (folding), parâmetros `TZID`/`ENCODING`, escapes RFC, `DTSTART`/`DTEND`/
`DURATION`, `SUMMARY`/`DESCRIPTION`/`LOCATION`/`STATUS`/`UID` e `RRULE`
**marcado, não expandido** (decisão de escopo v1 — uma ocorrência base, nunca
silencioso).

Fail-closed: um arquivo estruturalmente inválido (VEVENT sem `END`, VEVENT sem
`DTSTART` parseável) é um **erro nomeado** — nunca leitura parcial silenciosa.
Datetimes: sufixo `Z` ⇒ UTC; `TZID` resolvível por `zoneinfo` ⇒ consciente;
senão flutuante naíve (fuso local assumido, sinalizado por quem consome).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_BASIC_FORMATS: tuple[str, ...] = (
    "%Y%m%dT%H%M%S",
    "%Y%m%dT%H%M",
    "%Y%m%d",
)
_OUTPUT_FORMAT = "%Y%m%dT%H%M%S"

#: Campos de VEVENT que importam para a leitura. Qualquer outro (DTSTAMP,
#: CREATED, VALARM etc.) é metadado, não evento — ignorado sem alarde.
_EVENT_FIELDS: frozenset[str] = frozenset(
    {"UID", "SUMMARY", "DESCRIPTION", "LOCATION", "STATUS", "DTSTART", "DTEND", "DURATION", "RRULE"}
)


@dataclass(frozen=True)
class IcsEvent:
    """Evento de VEVENT já interpretado."""

    uid: str | None
    summary: str | None
    description: str | None
    location: str | None
    status: str | None
    start: datetime
    end: datetime | None
    #: RRULE cru, quando presente — v1 não expande, a chamada marca a limitação.
    recurrence: str | None = None
    #: Flags de interpretação: `tzid` (não resolvível), `fuso` (local-assumido).
    flags: tuple[tuple[str, str], ...] = ()

    def flag(self, key: str) -> str | None:
        for name, value in self.flags:
            if name == key:
                return value
        return None


class IcsParseError(ValueError):
    """Bloco `.ics` malformado — falha fechada, nunca parcial."""


def _unfold(text: str) -> list[str]:
    folded: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r").rstrip()
        if (line.startswith(" ") or line.startswith("\t")) and folded:
            folded[-1] += line[1:]
        else:
            folded.append(line)
    return folded


def _split_property(line: str) -> tuple[str, dict[str, str], str]:
    name, separator, value = line.partition(":")
    if not separator:
        raise IcsParseError(f"linha sem ':' — não é uma propriedade: {line[:40]!r}")
    base, *params = name.split(";")
    parsed: dict[str, str] = {}
    for param in params:
        key, equals, raw = param.partition("=")
        if not equals or not key:
            raise IcsParseError(f"parâmetro inválido: {param!r}")
        parsed[key.strip().upper()] = raw.strip()
    return base.strip().upper(), parsed, value


def _unescape(value: str) -> str:
    return re.sub(
        r"\\([\\,;n])",
        lambda m: {"\\": "\\", ",": ",", ";": ";", "n": "\n"}[m.group(1)],
        value,
    )


def _unquote_printable(value: str) -> str:
    chunks = re.split(r"=([0-9A-Fa-f]{2})", value)
    buffer = bytearray()
    for index, chunk in enumerate(chunks):
        if index % 2 == 1:
            buffer.append(int(chunk, 16))
        else:
            buffer.extend(chunk.encode("latin-1"))
    raw = bytes(buffer)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # Excel e calendários antigos exportam QP em latin-1; a decodificação
        # de fallback preserva os bytes como texto, sem ser silenciosa.
        return raw.decode("latin-1")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _parse_duration(raw: str) -> timedelta:
    match = re.fullmatch(
        r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", raw
    )
    if not match or raw == "P":
        raise IcsParseError(f"duração inválida: {raw!r}")
    negative = match.group(1) == "-"
    total = timedelta(
        weeks=int(match.group(2) or 0),
        days=int(match.group(3) or 0),
        hours=int(match.group(4) or 0),
        minutes=int(match.group(5) or 0),
        seconds=int(match.group(6) or 0),
    )
    return -total if negative else total


def _parse_basic(raw: str) -> datetime:
    for fmt in _BASIC_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise IcsParseError(f"data/hora iCal inválida: {raw!r}")


def _parse_datetime(
    raw: str, tzid: str | None, value_param: str | None
) -> tuple[datetime, tuple[tuple[str, str], ...]]:
    flags: list[tuple[str, str]] = []
    utc = raw.endswith("Z")
    body = raw[:-1] if utc else raw
    parsed = _parse_basic(body)
    if value_param == "DATE":
        return parsed, tuple(flags)
    if utc:
        return parsed.replace(tzinfo=UTC), tuple(flags)
    if tzid:
        try:
            zone = ZoneInfo(tzid)
        except ZoneInfoNotFoundError:
            flags.append(("tzid", tzid))
            flags.append(("fuso", "local-assumido"))
            return parsed, tuple(flags)
        return parsed.replace(tzinfo=zone), tuple(flags)
    flags.append(("fuso", "local-assumido"))
    return parsed, tuple(flags)


def _vevent_ranges(lines: list[str]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        if lines[index] != "BEGIN:VEVENT":
            index += 1
            continue
        depth = 1
        end = index + 1
        while end < len(lines) and depth > 0:
            if lines[end] == "BEGIN:VEVENT":
                depth += 1
            elif lines[end] == "END:VEVENT":
                depth -= 1
            end += 1
        if depth != 0:
            raise IcsParseError("VEVENT aberto sem END:VEVENT")
        ranges.append((index, end))
        index = end
    return ranges


def _parse_vevent(lines: list[str]) -> IcsEvent:
    fields: dict[str, tuple[dict[str, str], str]] = {}
    nested = 0
    for line in lines:
        if line.startswith("BEGIN:"):
            if line != "BEGIN:VEVENT":
                nested += 1
            continue
        if line.startswith("END:"):
            if line != "END:VEVENT":
                nested = max(0, nested - 1)
            continue
        if nested:
            continue
        name, params, value = _split_property(line)
        if name in _EVENT_FIELDS:
            fields[name] = (params, value)

    start_params, raw_start = fields.get("DTSTART", ({}, None))
    if raw_start is None:
        raise IcsParseError("VEVENT sem DTSTART")
    start, flags = _parse_datetime(raw_start, start_params.get("TZID"), start_params.get("VALUE"))
    end: datetime | None = None
    if "DTEND" in fields:
        end_params, raw_end = fields["DTEND"]
        end, end_flags = _parse_datetime(raw_end, end_params.get("TZID"), end_params.get("VALUE"))
        flags = flags + end_flags
    elif "DURATION" in fields:
        duration = fields["DURATION"][1]
        end = start + _parse_duration(duration.strip())

    def _field(name: str) -> str | None:
        if name not in fields:
            return None
        params, value = fields[name]
        decoded = _unquote_printable(value) if "ENCODING" in params else value
        return _unescape(decoded).strip()

    return IcsEvent(
        uid=_field("UID"),
        summary=_field("SUMMARY"),
        description=_field("DESCRIPTION"),
        location=_field("LOCATION"),
        status=_field("STATUS"),
        start=start,
        end=end,
        recurrence=fields["RRULE"][1].strip() if "RRULE" in fields else None,
        flags=tuple(flags),
    )


def parse_ics(text: str) -> list[IcsEvent]:
    """Lê `.ics` e devolve os eventos. Arquivo corrompido ⇒ `IcsParseError`."""
    lines = _unfold(text)
    ranges = _vevent_ranges(lines)
    return [_parse_vevent(lines[start:end]) for start, end in ranges]


def format_event_block(
    *,
    uid: str,
    summary: str,
    start: datetime,
    end: datetime | None,
    description: str | None = None,
    location: str | None = None,
    status: str = "CONFIRMED",
) -> str:
    """VEVENT como texto `.ics`, com datas UTC (`Z`) ou flutuantes naíves."""
    if start.tzinfo is not None:
        start = start.astimezone(UTC).replace(tzinfo=None)
        end_raw = end.astimezone(UTC).replace(tzinfo=None) if end is not None else None
        suffix = "Z"
    else:
        end_raw = end if end is not None else None
        suffix = ""
    start_raw = start.strftime(_OUTPUT_FORMAT) + suffix
    if end_raw is not None:
        end_raw = end_raw.strftime(_OUTPUT_FORMAT) + suffix

    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART:{start_raw}"]
    if end_raw is not None:
        lines.append(f"DTEND:{end_raw}")
    lines.append(f"SUMMARY:{_escape(summary)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _fold(line: str) -> str:
    """Dobra a linha em ≤75 octetos (RFC 5545) sem quebrar octetos multi-byte."""
    if len(line) <= 74:
        return line
    pieces: list[str] = []
    current = ""
    for char in line:
        if len((current + char).encode("utf-8")) > 74:
            pieces.append(current)
            current = char
        else:
            current += char
    if current:
        pieces.append(current)
    return "\r\n".join([pieces[0], *(" " + piece for piece in pieces[1:])])


def _empty_vcalendar() -> str:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Kairos//Calendario local//PT\r\n"
        "END:VCALENDAR\r\n"
    )


def add_event_to_text(text: str, block: str) -> str:
    """Insere um VEVENT antes de `END:VCALENDAR`. Cria o envelope se vazio."""
    if not text.strip():
        return _empty_vcalendar().replace("END:VCALENDAR", f"{block}END:VCALENDAR", 1)
    if "END:VCALENDAR" not in text:
        raise IcsParseError("arquivo sem END:VCALENDAR — mutação recusada")
    return text.replace("END:VCALENDAR", f"{block}END:VCALENDAR", 1)


def remove_event_by_uid(text: str, uid: str) -> tuple[str, int]:
    """Remove todo VEVENT cujo UID casa; devolve o texto e a quantidade removida."""
    pattern = re.compile(r"BEGIN:VEVENT.*?END:VEVENT", flags=re.DOTALL)
    removed = 0
    output: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        if _vevent_uid(match.group(0)) == uid:
            removed += 1
        else:
            output.append(text[cursor : match.end()])
        cursor = match.end()
    output.append(text[cursor:])
    return "".join(output), removed


def _vevent_uid(block: str) -> str | None:
    for line in _unfold(block):
        if line.startswith("UID:"):
            return line[len("UID:") :]
    return None
