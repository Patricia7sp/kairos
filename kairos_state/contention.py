"""Medição de contenção de escrita.

Decisão `questions.md#pergunta-9` (fecha **G-20**), Tarefa 05 / T-13.

**Por que existe.** No legado a contenção é intensamente *tratada* — escada
de retry com jitter, `busy_timeout` curto para evitar comboio, três
orçamentos de paciência — e **nunca medida**. Todos esses números foram
sintonizados às cegas, reagindo a incidentes já visíveis ao usuário. Sem
métrica, cada ajuste é uma hipótese que só se valida quando alguém reclama de
novo.

**O idioma é herdado**, não inventado: `cron/scheduler.py::get_inflight_guard_stats`
já faz exatamente isto — contadores monotônicos sob lock, ring limitado de
eventos recentes, espelho JSONL best-effort, e todo o caminho de telemetria
dentro de `try/except` porque *"never let telemetry break a tick"*.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

__all__ = [
    "BUDGET_SECONDS",
    "Budget",
    "ContentionRecorder",
    "get_write_contention_stats",
    "record_wait",
    "reset_stats",
]


class Budget(StrEnum):
    """Os três orçamentos de paciência, com propósitos distintos.

    A separação existe porque falhar significa coisas diferentes em cada um:
    uma escrita rotineira perdida atrasa a UI, uma de transcript **destrói o
    turno do usuário**, e um heartbeat perdido não custa nada porque a próxima
    janela o repete.
    """

    #: Escrita rotineira: background e UI não devem travar demais.
    ROUTINE = "routine"
    #: Transcript (append de mensagem, criação de sessão). A falha aborta o
    #: turno, então aguenta muito mais — um `state.db` compartilhado fica
    #: legitimamente ocupado por segundos (checkpoint TRUNCATE, VACUUM,
    #: recuperação offline, processo de versão antiga ainda rodando).
    TRANSCRIPT = "transcript"
    #: Heartbeat/rótulo de atividade: roda no caminho de resposta e **nunca**
    #: pode esperar a paciência rotineira. Escrita pulada é retomada sozinha.
    ACTIVITY = "activity"


#: Paciência é por TEMPO, não por tentativa. Um orçamento contado em
#: tentativas perde silenciosamente a corrida contra um checkpoint longo e
#: aparece como turno destruído, mesmo com o banco saudável e apenas ocupado.
BUDGET_SECONDS: dict[Budget, float] = {
    Budget.ROUTINE: 20.0,
    Budget.TRANSCRIPT: 60.0,
    Budget.ACTIVITY: 0.5,
}

#: Acima disso a espera é "lenta" e entra no ring de eventos recentes.
SLOW_WAIT_SECONDS = 1.0

#: Quantos eventos lentos manter em memória.
_RECENT_LIMIT = 50


@dataclass
class _BudgetCounters:
    waits_total: int = 0
    waits_over_1s: int = 0
    gaveup_total: int = 0
    wait_ms_samples: list[float] = field(default_factory=list)

    def snapshot(self) -> dict:
        samples = sorted(self.wait_ms_samples)
        return {
            "waits_total": self.waits_total,
            "waits_over_1s": self.waits_over_1s,
            "gaveup_total": self.gaveup_total,
            "wait_p50_ms": _percentile(samples, 0.50),
            "wait_p99_ms": _percentile(samples, 0.99),
        }


def _percentile(sorted_samples: list[float], q: float) -> float:
    if not sorted_samples:
        return 0.0
    index = min(len(sorted_samples) - 1, round(q * (len(sorted_samples) - 1)))
    return round(sorted_samples[index], 1)


class ContentionRecorder:
    """Contadores monotônicos, protegidos pelo mesmo lock em leitura e escrita."""

    def __init__(self, *, jsonl_path: Path | None = None, max_samples: int = 2000) -> None:
        self._lock = threading.Lock()
        self._by_budget: dict[Budget, _BudgetCounters] = {b: _BudgetCounters() for b in Budget}
        self._recent: list[dict] = []
        self._jsonl_path = jsonl_path
        self._max_samples = max_samples

    def record(
        self,
        budget: Budget,
        wait_seconds: float,
        *,
        gave_up: bool = False,
        detail: str | None = None,
    ) -> None:
        """Registra uma espera. **Nunca levanta.**"""
        try:
            self._record(budget, wait_seconds, gave_up=gave_up, detail=detail)
        except Exception:  # noqa: BLE001, S110 — ver abaixo
            # DELIBERADO. "never let telemetry break a write": uma falha aqui
            # não pode propagar para o caminho de escrita que estamos medindo.
            # Capturar estreito exigiria prever toda falha possível de
            # contador, ring e serialização — e errar a previsão derrubaria
            # justamente a escrita que a telemetria deveria só observar.
            pass

    def _record(self, budget, wait_seconds, *, gave_up, detail) -> None:
        wait_ms = wait_seconds * 1000.0
        slow = wait_seconds >= SLOW_WAIT_SECONDS
        entry = None

        with self._lock:
            counters = self._by_budget[budget]
            counters.waits_total += 1
            if slow:
                counters.waits_over_1s += 1
            if gave_up:
                counters.gaveup_total += 1
            counters.wait_ms_samples.append(wait_ms)
            if len(counters.wait_ms_samples) > self._max_samples:
                # Janela deslizante: percentis de um processo de longa vida
                # não devem ser dominados pela primeira hora de uptime.
                del counters.wait_ms_samples[: len(counters.wait_ms_samples) - self._max_samples]

            if slow or gave_up:
                entry = {
                    "budget": budget.value,
                    "wait_ms": round(wait_ms, 1),
                    "gave_up": gave_up,
                    "at": time.time(),
                    "pid": os.getpid(),
                }
                if detail:
                    entry["detail"] = detail
                self._recent.append(entry)
                del self._recent[:-_RECENT_LIMIT]

        if entry is not None:
            self._mirror(entry)

    def _mirror(self, entry: dict) -> None:
        """Espelho JSONL best-effort, com rotação por tamanho."""
        if self._jsonl_path is None:
            return
        try:
            path = self._jsonl_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 5_000_000:
                path.replace(path.with_suffix(path.suffix + ".1"))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:  # noqa: BLE001, S110
            # DELIBERADO, mesma razão: o espelho JSONL é best-effort. Disco
            # cheio, permissão negada ou FS somente-leitura degradam a
            # observabilidade; não podem degradar a escrita.
            pass

    def stats(self) -> dict:
        """Snapshot probe-visible.

        ``gaveup_total`` do orçamento ``transcript`` é o número que mais
        importa: diferente de zero significa **turno destruído por banco
        ocupado**, que é a falha que a escada de retry existe para evitar.
        """
        with self._lock:
            by_budget = {b.value: c.snapshot() for b, c in self._by_budget.items()}
            recent = list(self._recent)

        total = {
            "waits_total": sum(v["waits_total"] for v in by_budget.values()),
            "waits_over_1s": sum(v["waits_over_1s"] for v in by_budget.values()),
            "gaveup_total": sum(v["gaveup_total"] for v in by_budget.values()),
        }
        return {**total, "by_budget": by_budget, "recent_slow_waits": recent}

    def reset(self) -> None:
        with self._lock:
            self._by_budget = {b: _BudgetCounters() for b in Budget}
            self._recent = []


def _default_jsonl() -> Path:
    home = os.environ.get("KAIROS_HOME")
    root = Path(home).expanduser() if home else Path.home() / ".kairos"
    return root / "state" / "write_contention.jsonl"


_RECORDER = ContentionRecorder(jsonl_path=None)


def configure(jsonl_path: Path | None = None) -> None:
    global _RECORDER
    _RECORDER = ContentionRecorder(jsonl_path=jsonl_path if jsonl_path else _default_jsonl())


def record_wait(
    budget: Budget, wait_seconds: float, *, gave_up: bool = False, detail: str | None = None
) -> None:
    _RECORDER.record(budget, wait_seconds, gave_up=gave_up, detail=detail)


def get_write_contention_stats() -> dict:
    """Exposto por `kairos doctor` e pelo status do gateway."""
    return _RECORDER.stats()


def reset_stats() -> None:
    _RECORDER.reset()
