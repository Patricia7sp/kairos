"""Agendamento: os três `kind`, cadência e colapso de backlog.

`_reversa_sdd/cron/` (Tarefa 14).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CADENCE_CACHE_LIMIT",
    "ScheduleKind",
    "collapse_backlog",
    "compute_next_run",
    "croniter_available",
    "normalize_repeat",
]


class ScheduleKind(StrEnum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


#: Cache de cadência por expressão. Limpo **inteiro** ao atingir o limite,
#: para que expressões editadas ou apagadas não cresçam o cache sem limite
#: num gateway de vida longa. Poda seletiva exigiria saber quais ainda estão
#: em uso — e o custo de recalcular é baixo.
CADENCE_CACHE_LIMIT = 256

_cadence_cache: dict[str, float] = {}


def croniter_available() -> bool:
    """`croniter` é dependência **opcional**.

    Sem ela, um job `cron` não tem cadência calculável e `compute_next_run`
    devolve `None` — o job fica visível e inerte, em vez de o gateway inteiro
    falhar por causa de uma dependência que muitos usuários não precisam.
    """
    try:
        import croniter  # noqa: F401
    except ImportError:
        return False
    return True


def normalize_repeat(repeat: int | None, kind: ScheduleKind) -> int | None:
    """`None` = para sempre; `0` ou negativo → `None`.

    Agenda `once` sem `repeat` explícito recebe `1`: uma agenda de disparo
    único que repetisse para sempre seria contradição, e o default silencioso
    ("para sempre") é o mais perigoso dos dois.
    """
    if repeat is not None and repeat <= 0:
        repeat = None
    if kind is ScheduleKind.ONCE and repeat is None:
        return 1
    return repeat


def compute_next_run(schedule: dict[str, Any], last_run_at: str | None = None) -> str | None:
    """Próxima execução em ISO, ou `None`.

    Assinatura real do legado: **dict + ISO string → ISO string**. A spec
    anterior exemplificava com uma expressão cron solta, o que não é o
    contrato.
    """
    kind_raw = schedule.get("kind")
    try:
        kind = ScheduleKind(kind_raw)
    except ValueError:
        logger.warning("schedule.kind desconhecido: %r", kind_raw)
        return None

    base = _parse_iso(last_run_at) or datetime.now(UTC)

    if kind is ScheduleKind.ONCE:
        return schedule.get("run_at") if last_run_at is None else None

    if kind is ScheduleKind.INTERVAL:
        minutos = schedule.get("minutes")
        if not isinstance(minutos, int) or minutos <= 0:
            return None
        return (base + timedelta(minutes=minutos)).isoformat()

    expr = schedule.get("expr")
    if not expr or not croniter_available():
        return None
    import croniter as _croniter

    if len(_cadence_cache) >= CADENCE_CACHE_LIMIT:
        _cadence_cache.clear()
    try:
        return _croniter.croniter(expr, base).get_next(datetime).isoformat()
    except Exception:  # noqa: BLE001
        # DELIBERADO: expressão cron inválida é dado do usuário, não bug.
        # O job fica sem próxima execução; derrubar o tick puniria os demais.
        logger.warning("expressão cron inválida: %r", expr)
        return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def collapse_backlog(missed_periods: int) -> int:
    """Backlog acumulado **colapsa** para um único disparo (#33315).

    Sem o colapso, um gateway que ficou dias fora dispara uma rajada no
    restart. Com colapso mas sem o disparo, o job é adiado indefinidamente. A
    regra resolve os dois: **uma** vez, e o catch-up **consome uma unidade**
    do orçamento `repeat.times` — senão um job `once` atrasado disparia sem
    debitar nada e poderia repetir.
    """
    if missed_periods < 0:
        raise ValueError("missed_periods não pode ser negativo")
    return 1 if missed_periods else 0
