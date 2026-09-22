"""Monitor de cron do tipo `calendar` — lembretes proativos da agenda local.

Ao contrário do monitor de script (que executa um comando), a fonte aqui é
lida **em processo**: a mesma resolução de `calendar.source` e o mesmo parser
da ferramenta `calendar` (`kairos_tools.calendar`). O agente só roda quando
há uma ocorrência na janela `[agora, agora+janela]` que ainda não foi lembrada;
o conjunto de ocorrências lembradas fica no `monitor_state` do job (campo
`remindidos`), persistido no mesmo storage que executa o turno.

Recorrência (RRULE) é **expandida na janela** (mesma função da ferramenta):
cada ocorrência tem chave própria (`start_utc|uid`) e é lembrada uma vez — um
evento diário lembra em todos os dias. `EXDATE`/`RECURRENCE-ID` não são
honrados (escopo documentado na ferramenta). Tick sem novidade ⇒
`suppressed`, sem custo de modelo.

O módulo importa `MonitorOutcome` de `kairos_cron.monitor` no topo (ciclo
quebrado: `monitor` importa `calendar_monitor` somente dentro de funções).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kairos_cron.monitor import MonitorOutcome
from kairos_tools.calendar import calendar_source, occurrences_in_window, read_events
from kairos_tools.ics import IcsEvent, IcsParseError

#: Tipo de monitor registrado no cron.
MONITOR_TYPE = "calendar"

#: Janela padrão e teto (minutos).
DEFAULT_WINDOW_MINUTES = 120
MAX_WINDOW_MINUTES = 24 * 60

#: Tetos de sanidade.
_MAX_REMINDED_ENTRIES = 500
_MAX_REMINDED_KEY_CHARS = 200


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------


class CalendarMonitorError(ValueError):
    """Forma inválida de monitor ou estado de calendário."""


# ---------------------------------------------------------------------------
# Forma do monitor (validação fail-closed)
# ---------------------------------------------------------------------------


def validate_calendar_monitor(monitor: dict[str, Any]) -> dict[str, Any]:
    """Valida e normaliza um monitor ``{"type": "calendar", ...}``.

    Aceita ``janela_min`` como inteiro 1–MAX_WINDOW_MINUTES (default
    DEFAULT_WINDOW_MINUTES). Extras e forma inválida ⇒ ``CalendarMonitorError``.
    """
    if not isinstance(monitor, dict):
        raise CalendarMonitorError("monitor deve ser um dicionário")
    monitor_type = monitor.get("type")
    if monitor_type != MONITOR_TYPE:
        raise CalendarMonitorError(f"tipo esperado {MONITOR_TYPE!r}, recebido {monitor_type!r}")
    allowed = {"type", "janela_min"}
    unknown = sorted(set(monitor) - allowed)
    if unknown:
        raise CalendarMonitorError(f"campos desconhecidos em monitor calendar: {unknown}")
    raw_window = monitor.get("janela_min")
    if raw_window is None:
        window = DEFAULT_WINDOW_MINUTES
    else:
        try:
            window = int(raw_window)
        except (TypeError, ValueError):
            raise CalendarMonitorError(
                f"janela_min deve ser inteiro, recebido {raw_window!r}"
            ) from None
        if not 1 <= window <= MAX_WINDOW_MINUTES:
            raise CalendarMonitorError(f"janela_min fora de 1–{MAX_WINDOW_MINUTES}: {window}")
    return {"type": MONITOR_TYPE, "janela_min": window}


# ---------------------------------------------------------------------------
# Estado (chaves lembradas, persistido no monitor_state do job)
# ---------------------------------------------------------------------------


def default_calendar_state() -> dict[str, Any]:
    """Estado inicial para um novo monitor calendar."""
    return {"remindidos": [], "last_changed_at": None, "last_checked_at": None}


def validate_calendar_state(state: dict[str, Any]) -> dict[str, Any]:
    """Valida e normaliza o estado de um monitor calendar.

    Aceita somente ``remindidos`` (lista de strings), ``last_changed_at`` e
    ``last_checked_at`` (strings ou None).
    """
    if not isinstance(state, dict):
        raise CalendarMonitorError("estado deve ser um dicionário")
    allowed = {"remindidos", "last_changed_at", "last_checked_at"}
    unknown = sorted(set(state) - allowed)
    if unknown:
        raise CalendarMonitorError(f"campos desconhecidos no estado calendar: {unknown}")

    reminded = state.get("remindidos", [])
    if not isinstance(reminded, list):
        raise CalendarMonitorError("remindidos deve ser lista")
    pruned: list[str] = []
    for entry in reminded:
        if not isinstance(entry, str):
            raise CalendarMonitorError(
                f"chave em remindidos deve ser string, recebido {type(entry)}"
            )
        if len(entry) > _MAX_REMINDED_KEY_CHARS:
            raise CalendarMonitorError(
                f"chave em remindidos excede {_MAX_REMINDED_KEY_CHARS} chars"
            )
        pruned.append(entry)
    if len(pruned) > _MAX_REMINDED_ENTRIES:
        pruned = pruned[-_MAX_REMINDED_ENTRIES:]

    last_changed = state.get("last_changed_at")
    last_checked = state.get("last_checked_at")
    if last_changed is not None and not isinstance(last_changed, str):
        raise CalendarMonitorError("last_changed_at deve ser string ou None")
    if last_checked is not None and not isinstance(last_checked, str):
        raise CalendarMonitorError("last_checked_at deve ser string ou None")

    return {
        "remindidos": pruned,
        "last_changed_at": last_changed,
        "last_checked_at": last_checked,
    }


def _state_from_dict(state: dict[str, Any] | None) -> dict[str, Any]:
    """Retorna estado validado ou o default quando ausente/vazio."""
    if not state:
        return default_calendar_state()
    return validate_calendar_state(state)


def _state_dict(state: dict[str, Any], *, checked_at: str | None = None) -> dict[str, Any]:
    """Serializa estado para persistência (mantém last_checked_at atualizado)."""
    out = dict(state)
    if checked_at is not None:
        out["last_checked_at"] = checked_at
    return out


# ---------------------------------------------------------------------------
# Chave de ocorrência + janela
# ---------------------------------------------------------------------------


def _to_utc(value: datetime) -> datetime:
    """Converte datetime para UTC (flutuante assume fuso local do sistema)."""
    return value.astimezone(UTC)


def _occurrence_key(event: IcsEvent) -> str:
    """Chave estável ``start_utc_iso|uid`` para deduplicação de lembretes."""
    start_utc = _to_utc(event.start)
    start_iso = start_utc.isoformat(timespec="seconds")
    uid = (event.uid or "").strip()
    return f"{start_iso}|{uid}"


def _key_start_expired(key: str, now_utc: datetime) -> bool:
    """``True`` quando a data de início da chave é anterior a ``now_utc``."""
    prefix = key.split("|", 1)[0]
    try:
        dt = datetime.fromisoformat(prefix)
    except (ValueError, TypeError):
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt < now_utc


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------


class CalendarDecision:
    """Resultado de ``check_calendar_monitor``."""

    __slots__ = ("next_state", "outcome", "run_agent", "window_events")

    def __init__(
        self,
        *,
        outcome: MonitorOutcome,
        next_state: dict[str, Any],
        run_agent: bool,
        window_events: tuple[IcsEvent, ...] = (),
    ) -> None:
        self.outcome = outcome
        self.next_state = next_state
        self.run_agent = run_agent
        self.window_events = window_events


def check_calendar_monitor(
    home: Any,
    monitor: dict[str, Any],
    state: dict[str, Any] | None,
    now: datetime,
) -> CalendarDecision:
    """Decide se o agente deve rodar para o monitor de calendário.

    - Fonte ausente/não configurada ou parse com problemas ⇒ ``SOURCE_ERROR``.
    - Nenhuma ocorrência nova na janela ⇒ ``NO_CHANGE``.
    - Ocorrência nova ⇒ roda o agente.
    """
    from pathlib import Path

    home_path = Path(home) if not isinstance(home, Path) else home
    validated_monitor = validate_calendar_monitor(monitor)
    janela_min = validated_monitor["janela_min"]
    previous = _state_from_dict(state)
    now_utc = _to_utc(now)
    window = timedelta(minutes=janela_min)

    source = calendar_source(home_path)
    if source is None or not source.exists():
        return CalendarDecision(
            outcome=MonitorOutcome.SOURCE_ERROR,
            next_state=_state_dict(previous, checked_at=now.isoformat()),
            run_agent=False,
        )

    events, problems = read_events(source)
    if problems:
        return CalendarDecision(
            outcome=MonitorOutcome.SOURCE_ERROR,
            next_state=_state_dict(previous, checked_at=now.isoformat()),
            run_agent=False,
        )
    try:
        in_window = occurrences_in_window(events, now_utc, now_utc + window)
    except IcsParseError:
        return CalendarDecision(
            outcome=MonitorOutcome.SOURCE_ERROR,
            next_state=_state_dict(previous, checked_at=now.isoformat()),
            run_agent=False,
        )

    candidates = sorted(
        in_window,
        key=lambda e: (_to_utc(e.start), e.uid or ""),
    )
    keys = {_occurrence_key(e) for e in candidates}

    pruned = [k for k in previous["remindidos"] if not _key_start_expired(k, now_utc)]
    new = sorted(keys - set(pruned))

    if not new:
        return CalendarDecision(
            outcome=MonitorOutcome.NO_CHANGE,
            next_state=_state_dict(previous, checked_at=now.isoformat()),
            run_agent=False,
        )

    outcome = MonitorOutcome.FIRST_RUN if not previous["remindidos"] else MonitorOutcome.CHANGED
    next_reminded = sorted(set(pruned) | keys)
    next_state = _state_dict(
        {
            "remindidos": next_reminded,
            "last_changed_at": now.isoformat(),
            "last_checked_at": now.isoformat(),
        },
        checked_at=now.isoformat(),
    )
    return CalendarDecision(
        outcome=outcome,
        next_state=next_state,
        run_agent=True,
        window_events=tuple(candidates),
    )


# ---------------------------------------------------------------------------
# Renderização do bloco de lembrete (injetado no prompt)
# ---------------------------------------------------------------------------

_DISPLAY_EVENTS_LIMIT = 50


def render_calendar_reminder(events: tuple[IcsEvent, ...]) -> str:
    """Renderiza o bloco de lembretes para o prompt do modelo.

    O bloco lista os eventos da janela, com data/hora local e título,
    e avisa quando há mais do que ``_DISPLAY_EVENTS_LIMIT``.
    """
    lines: list[str] = [
        "INSTRUÇÃO DE CALENDÁRIO — eventos na janela de lembretes deste tick",
        "",
    ]
    for event in events[:_DISPLAY_EVENTS_LIMIT]:
        start_utc = _to_utc(event.start)
        start_local = start_utc.astimezone().isoformat(timespec="minutes")
        summary = event.summary or "(sem título)"
        lines.append(f"- {start_local}: {summary}")
    remaining = len(events) - _DISPLAY_EVENTS_LIMIT
    if remaining > 0:
        lines.append(f"- ... e mais {remaining} evento(s)")
    return "\n".join(lines)
