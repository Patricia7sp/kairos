"""Escada de retry de escrita, instrumentada.

RF-04 e T-13. Reconstruído de `_reversa_sdd/hermes-state/` (Tarefa 05).
"""

from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

from kairos_state.contention import BUDGET_SECONDS, Budget, record_wait

__all__ = ["WriteGaveUp", "write_with_retry", "is_busy_error"]

T = TypeVar("T")

#: Abaixo disso o jitter é pequeno (reclamação rápida em contenção de
#: milissegundos); acima, recua para não martelar um lock longo com
#: tentativas de BEGIN IMMEDIATE.
_SLOW_AFTER_S = 1.0
_FAST_JITTER = (0.002, 0.020)
_SLOW_JITTER = (0.250, 1.000)


class WriteGaveUp(sqlite3.OperationalError):
    """A paciência do orçamento acabou com o banco ainda ocupado."""


def is_busy_error(exc: BaseException) -> bool:
    """`SQLITE_BUSY` / `SQLITE_LOCKED`, distinguidos de erro real.

    A distinção importa: erro de sintaxe ou violação de constraint **não**
    deve ser repetido, e tratá-lo como contenção esconderia o bug atrás de
    uma espera de 20 segundos.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _sleep_before_retry(elapsed: float) -> None:
    """Jitter aleatório em vez do escalonamento determinístico do SQLite.

    O handler de ocupado embutido usa uma sequência fixa de esperas, o que
    sob concorrência alta faz vários escritores acordarem juntos — efeito
    comboio. Jitter os escalona naturalmente.
    """
    low, high = _FAST_JITTER if elapsed < _SLOW_AFTER_S else _SLOW_JITTER
    time.sleep(random.uniform(low, high))


def write_with_retry(
    operation: Callable[[], T],
    *,
    budget: Budget = Budget.ROUTINE,
    detail: str | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> T:
    """Executa `operation`, repetindo enquanto o banco estiver ocupado.

    A paciência é por **tempo**, não por tentativa: um orçamento contado em
    tentativas perde a corrida contra um checkpoint TRUNCATE longo ou um
    VACUUM de processo irmão, e a falha aparece como turno destruído mesmo
    com o banco saudável e apenas ocupado.

    Toda espera é registrada, inclusive a bem-sucedida — medir só as falhas
    esconderia justamente a degradação progressiva que antecede a falha.
    """
    patience = BUDGET_SECONDS[budget]
    started = _clock()

    while True:
        try:
            result = operation()
        except sqlite3.OperationalError as exc:
            if not is_busy_error(exc):
                raise
            elapsed = _clock() - started
            if elapsed >= patience:
                record_wait(budget, elapsed, gave_up=True, detail=detail or str(exc))
                raise WriteGaveUp(
                    f"banco ocupado por {elapsed:.1f}s, acima da paciência de "
                    f"{patience:.1f}s do orçamento '{budget.value}': {exc}"
                ) from exc
            _sleep_before_retry(elapsed)
        else:
            elapsed = _clock() - started
            if elapsed > 0:
                record_wait(budget, elapsed, detail=detail)
            return result
