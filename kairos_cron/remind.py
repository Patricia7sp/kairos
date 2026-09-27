"""Lembrete de propósito geral: formato de produto do legado → agenda `once`.

O `data-dictionary.md` do `_reversa_sdd/` fala de entradas como `"30m"`,
`"2h"`, `"2026-02-03T14:00"` com `kind=once` e `run_at` ISO. O scheduler Kairos
já executa `once`; o que falava era a superfície que parseia o texto e cria o
job. Este módulo é só essa tradução, até o `JobStore.create`.

Horários sem fuso (ISO ou `HH:MM`) são interpretados na hora local do processo
— um lembrete às 14:00 é às 14:00 do relógio de quem criou.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_DELTA_RE = re.compile(r"^(\d+)([mhd])$")
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

__all__ = ["parse_when"]


def parse_when(value: str, now: datetime | None = None) -> dict:
    """Traduz o formato texto do usuário para `{"kind": "once", "run_at": iso}`.

    Formatos aceitos: `30m`/`2h`/`1d` (daqui a), ISO com fuso, ISO sem fuso
    (hora local) e `HH:MM` (hoje; amanhã se já passou). Expressões cron de
    cinco campos não são lembrete único — recusa apontando para `cron create`.
    Horário no passado também é recusa: um lembrete que já deveria ter tocado
    não nasce disparado.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.astimezone()
    texto = str(value).strip()
    if not texto:
        raise ValueError("quando é obrigatório")
    run_at = _resolve(texto, now)
    if run_at <= now:
        raise ValueError(
            f"horário {texto!r} já passou; use um momento futuro "
            "(ex.: '30m', '2h', '14:00' ou ISO com fuso)"
        )
    return {"kind": "once", "run_at": run_at.isoformat()}


def _resolve(texto: str, now: datetime) -> datetime:
    m = _DELTA_RE.match(texto)
    if m:
        quantidade, unidade = int(m.group(1)), m.group(2)
        duracao = {
            "m": timedelta(minutes=quantidade),
            "h": timedelta(hours=quantidade),
            "d": timedelta(days=quantidade),
        }[unidade]
        if quantidade <= 0:
            raise ValueError("o intervalo deve ser positivo (ex.: 30m, 2h, 1d)")
        return now + duracao

    if texto.count(":") == 1:
        m = _HHMM_RE.match(texto)
        if m:
            return _at_hora_local(int(m.group(1)), int(m.group(2)), now)
        raise ValueError(
            f"{texto!r} não é reconhecido — use '30m', '2h', '1d', 'HH:MM' ou ISO com fuso"
        )

    try:
        parsed = datetime.fromisoformat(texto)
    except ValueError:
        raise ValueError(
            f"{texto!r} não é reconhecido — use '30m', '2h', '1d', 'HH:MM' ou ISO com fuso"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_local_tz())
    return parsed


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _at_hora_local(hora: int, minuto: int, now: datetime) -> datetime:
    local = now.astimezone().replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if local <= now:
        local = local + timedelta(days=1)
    return local.astimezone(UTC)
