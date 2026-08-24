"""O processo do gateway: o serviço longo que o container supervisiona.

`_reversa_sdd/providers-gateway/design.md` §B e "Fluxos Alternativos".

Três responsabilidades, e nenhuma a mais:

1. **Reentrega no boot.** Uma morte suja (SIGKILL, OOM, VM removida) deixa
   obrigações presas em `claimed` sob um dono que não existe mais. Sem
   varrê-las no arranque, elas nunca mais seriam tentadas — que é o modo de
   falha exato para o qual o ledger existe.
2. **Drenagem por marcador.** A spec é explícita: `.drain_request.json`
   presente entra em drain, ausente sai. **Não há canal HTTP de controle** —
   um canal desses seria mais uma porta a proteger num processo que já fala
   com plataformas externas.
3. **Encerramento limpo.** SIGTERM devolve à fila o que estava em voo, em vez
   de deixá-lo `claimed` para o próximo boot recuperar. É o que faz
   `docker stop` custar milissegundos e não o timeout inteiro.

O que este módulo **não** faz: falar com plataformas. Adapter é registrado de
fora, via `register_adapter`. Sem nenhum registrado o gateway roda e diz que
está ocioso — em vez de fingir que entrega.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from kairos_gateway.delivery import DeadTargets, FailureKind

__all__ = ["DRAIN_MARKER", "GatewayService", "PlatformAdapter", "SendResult"]

logger = logging.getLogger(__name__)

#: Nome fixado pela spec; o gateway não escolhe, só observa.
DRAIN_MARKER = ".drain_request.json"


@dataclass(frozen=True)
class SendResult:
    """Resultado estruturado, nunca string parseada.

    `retry_after` vem da plataforma (rate limit) e é dado, não heurística: a
    escada de cooldown local só entra quando a plataforma não diz nada.
    """

    ok: bool
    retryable: bool = False
    retry_after: float | None = None
    error_kind: str | None = None


class PlatformAdapter(Protocol):
    """A cintura estreita: um adapter **declara** o que sabe fazer.

    Substitui a herança de um `BasePlatformAdapter` de 131 métodos, que a
    `architecture.md` marca como violação da Lei 2.
    """

    name: str

    def send(self, target: str, payload: str) -> SendResult: ...


@dataclass
class GatewayStats:
    ticks: int = 0
    delivered: int = 0
    released: int = 0
    abandoned: int = 0
    reclaimed: int = 0
    started_at: float = field(default_factory=time.time)


class GatewayService:
    def __init__(
        self,
        home: Path,
        *,
        poll_interval: float = 5.0,
        conn: sqlite3.Connection | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.home = home
        self.poll_interval = poll_interval
        self.stats = GatewayStats()
        self.dead_targets = DeadTargets()
        self._adapters: dict[str, PlatformAdapter] = {}
        self._clock = clock
        self._stopping = False
        self._draining = False
        self._owned: set[str] = set()

        self._conn = conn if conn is not None else self._open_db()
        from kairos_state.repositories.ledger import LedgerRepository

        self.ledger = LedgerRepository(self._conn)

    def _open_db(self) -> sqlite3.Connection:
        from kairos_state import connect
        from kairos_state.migrations import migrate

        conn = connect(self.home / "state.db")
        migrate(conn)
        return conn

    # --- adapters ---

    def register_adapter(self, adapter: PlatformAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def adapter_for(self, target: str) -> PlatformAdapter | None:
        """`target` é `plataforma:destino` — o prefixo escolhe o adapter."""
        plataforma = target.split(":", 1)[0]
        return self._adapters.get(plataforma)

    # --- drain ---

    @property
    def draining(self) -> bool:
        return self._draining

    def check_drain(self) -> bool:
        """Lê o marcador. A transição é registrada nos dois sentidos: entrar
        em drain sem dizer deixaria o operador achando que o gateway travou."""
        presente = (self.home / DRAIN_MARKER).exists()
        if presente and not self._draining:
            logger.warning("gateway: drenagem solicitada por %s", DRAIN_MARKER)
        elif not presente and self._draining:
            logger.info("gateway: marcador removido, retomando entregas")
        self._draining = presente
        return presente

    # --- ciclo ---

    def boot(self) -> int:
        """Recupera o que a morte anterior deixou preso. Devolve o total."""
        vivos = _live_pids()
        recuperadas = self.ledger.reclaim_dead(vivos)
        if recuperadas:
            logger.warning(
                "gateway: %d obrigação(ões) presas sob dono morto devolvidas à fila",
                len(recuperadas),
            )
        self.stats.reclaimed += len(recuperadas)
        return len(recuperadas)

    def tick(self) -> int:
        """Uma passada. Devolve quantas obrigações foram quitadas.

        Separado de `run` para ser testável sem relógio nem sinal — e para
        que `kairos gateway --once` seja o mesmo código do laço, não um
        caminho paralelo que diverge com o tempo.
        """
        self.stats.ticks += 1
        if self.check_drain():
            return 0

        pid, started = os.getpid(), int(self.stats.started_at)
        entregues = 0

        for ob in self.ledger.pending():
            if self._stopping:
                break
            if self.dead_targets.is_permanently_dead(ob.target):
                self.ledger.abandon(ob.obligation_id)
                self.stats.abandoned += 1
                continue
            if self.dead_targets.is_suspended(ob.target, now=self._clock()):
                continue

            adapter = self.adapter_for(ob.target)
            if adapter is None:
                # Sem adapter não há como entregar, e insistir a cada tick
                # gastaria o log. O alvo é suspenso pela mesma escada.
                self.dead_targets.record_failure(
                    ob.target, FailureKind.TRANSIENT, now=self._clock()
                )
                continue

            if not self.ledger.claim(ob.obligation_id, pid=pid, started_at=started):
                continue  # outro gateway ganhou a corrida
            self._owned.add(ob.obligation_id)

            try:
                resultado = adapter.send(ob.target, ob.payload)
            except Exception:
                logger.exception("gateway: adapter %s levantou ao enviar", adapter.name)
                resultado = SendResult(ok=False, retryable=True)

            self._settle(ob.obligation_id, ob.target, resultado)
            if resultado.ok:
                entregues += 1

        return entregues

    def _settle(self, obligation_id: str, target: str, resultado: SendResult) -> None:
        self._owned.discard(obligation_id)
        if resultado.ok:
            # Sucesso cura o alvo: a escada mede falha consecutiva.
            self.dead_targets.record_success(target)
            self.ledger.confirm(obligation_id)
            self.stats.delivered += 1
            return

        kind = FailureKind.TRANSIENT if resultado.retryable else FailureKind.PERMANENT
        self.dead_targets.record_failure(target, kind, now=self._clock())
        if resultado.retryable:
            self.ledger.release(obligation_id)
            self.stats.released += 1
        else:
            self.ledger.abandon(obligation_id)
            self.stats.abandoned += 1

    def stop(self, *_args: object) -> None:
        """Handler de sinal: só marca. Trabalhar dentro do handler é o que
        produz escrita parcial quando o sinal chega no meio de um commit."""
        self._stopping = True

    def shutdown(self) -> None:
        """Devolve o que estava em voo. Deixá-lo `claimed` funcionaria — o
        próximo boot recuperaria — mas atrasaria a entrega por um ciclo
        inteiro sem necessidade."""
        for oid in sorted(self._owned):
            self.ledger.release(oid)
        if self._owned:
            logger.info("gateway: %d obrigação(ões) em voo devolvidas à fila", len(self._owned))
        self._owned.clear()

    def run(
        self, *, max_ticks: int | None = None, sleep: Callable[[float], None] | None = None
    ) -> int:
        """Laço supervisionado. `max_ticks` existe para o teste terminar."""
        dormir = sleep if sleep is not None else time.sleep
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self.stop)
            except ValueError:
                # Fora da thread principal (teste, embed) não há handler; o
                # laço ainda respeita `stop()` chamado diretamente.
                pass

        self.boot()
        logger.info(
            "gateway: no ar (adapters: %s)",
            ", ".join(sorted(self._adapters)) or "nenhum registrado",
        )

        try:
            while not self._stopping:
                if max_ticks is not None and self.stats.ticks >= max_ticks:
                    break
                self.tick()
                if self._stopping:
                    break
                dormir(self.poll_interval)
        finally:
            self.shutdown()

        logger.info(
            "gateway: encerrado após %d tick(s); %d entregue(s)",
            self.stats.ticks,
            self.stats.delivered,
        )
        return 0

    def status(self) -> dict[str, object]:
        return {
            "adapters": sorted(self._adapters),
            "drenando": self._draining,
            "obrigações": self.ledger.counts_by_state(),
            "ticks": self.stats.ticks,
            "entregues": self.stats.delivered,
            "devolvidas": self.stats.released,
            "abandonadas": self.stats.abandoned,
            "recuperadas": self.stats.reclaimed,
            "uptime_s": round(self._clock() - self.stats.started_at, 1),
        }


def _live_pids() -> set[int]:
    """PIDs vivos. Em Linux é o /proc; noutro SO, degrada para 'só eu'.

    Degradar assim é conservador na direção certa: no pior caso o gateway
    devolve à fila algo que ainda tinha dono, e a obrigação é reentregue —
    duplicar é recuperável, perder não é.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return {os.getpid()}
    return {int(p.name) for p in proc.iterdir() if p.name.isdigit()}


def write_drain_request(home: Path, reason: str = "manual") -> Path:
    marker = home / DRAIN_MARKER
    marker.write_text(
        json.dumps({"reason": reason, "at": time.time()}, ensure_ascii=False), encoding="utf-8"
    )
    return marker
