"""Testes do processo do gateway: ledger durável, drenagem e ciclo de vida."""

import tempfile
import unittest
from pathlib import Path

from kairos_gateway.delivery import DeliveryState
from kairos_gateway.service import DRAIN_MARKER, GatewayService, SendResult
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.ledger import LedgerRepository


class FakeAdapter:
    """Adapter controlável: o teste decide o que cada envio devolve."""

    def __init__(self, name="fake", results=None):
        self.name = name
        self.sent = []
        self._results = list(results or [])

    def send(self, target, payload):
        self.sent.append((target, payload))
        return self._results.pop(0) if self._results else SendResult(ok=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.conn = connect(self.home / "state.db")
        migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _svc(self, adapter=None, **kw):
        svc = GatewayService(self.home, conn=self.conn, **kw)
        if adapter is not None:
            svc.register_adapter(adapter)
        return svc


class LedgerRepositoryTests(_Base):
    def setUp(self):
        super().setUp()
        self.repo = LedgerRepository(self.conn)

    def test_obligation_survives_the_process_that_created_it(self):
        """O ponto inteiro do ledger durável."""
        self.repo.record("o1", "fake:alice", "oi")
        outra_conexao = connect(self.home / "state.db")
        try:
            self.assertEqual(
                ["o1"], [o.obligation_id for o in LedgerRepository(outra_conexao).pending()]
            )
        finally:
            outra_conexao.close()

    def test_only_one_claimer_wins_the_race(self):
        self.repo.record("o1", "fake:a", "x")
        self.assertTrue(self.repo.claim("o1", pid=1, started_at=1))
        self.assertFalse(self.repo.claim("o1", pid=2, started_at=2))

    def test_confirm_requires_a_prior_claim(self):
        """Confirmar sem reivindicar seria dar por entregue o que ninguém enviou."""
        self.repo.record("o1", "fake:a", "x")
        self.assertFalse(self.repo.confirm("o1"))
        self.repo.claim("o1", pid=1, started_at=1)
        self.assertTrue(self.repo.confirm("o1"))
        self.assertEqual(1, self.repo.counts_by_state()[DeliveryState.DELIVERED.value])

    def test_release_returns_it_to_the_queue(self):
        self.repo.record("o1", "fake:a", "x")
        self.repo.claim("o1", pid=1, started_at=1)
        self.assertTrue(self.repo.release("o1"))
        self.assertEqual(["o1"], [o.obligation_id for o in self.repo.pending()])

    def test_claim_counts_attempts(self):
        self.repo.record("o1", "fake:a", "x")
        self.repo.claim("o1", pid=1, started_at=1)
        self.repo.release("o1")
        self.repo.claim("o1", pid=1, started_at=1)
        attempts = self.conn.execute(
            "SELECT attempts FROM delivery_obligations WHERE obligation_id='o1'"
        ).fetchone()[0]
        self.assertEqual(2, attempts)

    def test_reclaim_dead_frees_orphans_of_a_dead_owner(self):
        self.repo.record("o1", "fake:a", "x")
        self.repo.claim("o1", pid=999_999, started_at=1)
        self.assertEqual([], self.repo.pending(), "reivindicada não está pendente")
        self.assertEqual(["o1"], self.repo.reclaim_dead(live_pids={1, 2}))
        self.assertEqual(["o1"], [o.obligation_id for o in self.repo.pending()])

    def test_reclaim_dead_spares_a_live_owner(self):
        self.repo.record("o1", "fake:a", "x")
        self.repo.claim("o1", pid=4242, started_at=1)
        self.assertEqual([], self.repo.reclaim_dead(live_pids={4242}))


class GatewayServiceTests(_Base):
    def test_delivers_a_pending_obligation(self):
        adapter = FakeAdapter()
        svc = self._svc(adapter)
        svc.ledger.record("o1", "fake:alice", "olá")
        self.assertEqual(1, svc.tick())
        self.assertEqual([("fake:alice", "olá")], adapter.sent)
        self.assertEqual(1, svc.ledger.counts_by_state()["delivered"])

    def test_transient_failure_returns_it_to_the_queue(self):
        adapter = FakeAdapter(results=[SendResult(ok=False, retryable=True)])
        svc = self._svc(adapter)
        svc.ledger.record("o1", "fake:a", "x")
        svc.tick()
        self.assertEqual(1, svc.ledger.counts_by_state()["pending"])
        self.assertTrue(svc.dead_targets.is_suspended("fake:a"))

    def test_permanent_failure_abandons_instead_of_retrying_forever(self):
        adapter = FakeAdapter(results=[SendResult(ok=False, retryable=False)])
        svc = self._svc(adapter)
        svc.ledger.record("o1", "fake:a", "x")
        svc.tick()
        self.assertEqual(1, svc.ledger.counts_by_state()["abandoned"])
        self.assertTrue(svc.dead_targets.is_permanently_dead("fake:a"))

    def test_adapter_exception_does_not_kill_the_loop(self):
        class Explodes:
            name = "fake"

            def send(self, target, payload):
                raise RuntimeError("boom")

        svc = self._svc(Explodes())
        svc.ledger.record("o1", "fake:a", "x")
        with self.assertLogs("kairos_gateway.service", level="ERROR"):
            svc.tick()
        # Tratada como transitória: o defeito pode ser do adapter, não do alvo.
        self.assertEqual(1, svc.ledger.counts_by_state()["pending"])

    def test_success_heals_a_previously_failing_target(self):
        adapter = FakeAdapter(results=[SendResult(ok=False, retryable=True), SendResult(ok=True)])
        svc = self._svc(adapter)
        svc.ledger.record("o1", "fake:a", "x")
        svc.tick()
        svc.dead_targets.record_success("fake:a")  # cooldown vencido
        svc.tick()
        self.assertFalse(svc.dead_targets.is_suspended("fake:a"))

    def test_unknown_platform_does_not_lose_the_obligation(self):
        svc = self._svc(FakeAdapter(name="fake"))
        svc.ledger.record("o1", "telegram:bob", "x")
        svc.tick()
        self.assertEqual(1, svc.ledger.counts_by_state()["pending"])

    def test_drain_marker_halts_delivery(self):
        adapter = FakeAdapter()
        svc = self._svc(adapter)
        svc.ledger.record("o1", "fake:a", "x")
        (self.home / DRAIN_MARKER).write_text("{}", encoding="utf-8")
        self.assertEqual(0, svc.tick())
        self.assertTrue(svc.draining)
        self.assertEqual([], adapter.sent)

    def test_removing_the_marker_resumes_delivery(self):
        adapter = FakeAdapter()
        svc = self._svc(adapter)
        svc.ledger.record("o1", "fake:a", "x")
        marker = self.home / DRAIN_MARKER
        marker.write_text("{}", encoding="utf-8")
        svc.tick()
        marker.unlink()
        self.assertEqual(1, svc.tick())
        self.assertFalse(svc.draining)

    def test_boot_recovers_obligations_stuck_under_a_dead_owner(self):
        svc = self._svc(FakeAdapter())
        svc.ledger.record("o1", "fake:a", "x")
        svc.ledger.claim("o1", pid=999_999, started_at=1)
        self.assertEqual(1, svc.boot())
        self.assertEqual(1, svc.ledger.counts_by_state()["pending"])

    def test_shutdown_releases_in_flight_work(self):
        """SIGTERM devolve à fila em vez de deixar preso para o próximo boot."""
        adapter = FakeAdapter(results=[SendResult(ok=False, retryable=True)])
        svc = self._svc(adapter)
        svc.ledger.record("o1", "fake:a", "x")
        svc.tick()
        svc.shutdown()
        self.assertEqual(0, svc.ledger.counts_by_state()["claimed"])

    def test_run_stops_at_max_ticks_without_sleeping(self):
        dormidas = []
        svc = self._svc(FakeAdapter(), poll_interval=99.0)
        self.assertEqual(0, svc.run(max_ticks=3, sleep=dormidas.append))
        self.assertEqual(3, svc.stats.ticks)
        self.assertTrue(all(d == 99.0 for d in dormidas))

    def test_stop_signal_breaks_the_loop(self):
        svc = self._svc(FakeAdapter())

        def parar(_):
            svc.stop()

        svc.run(max_ticks=100, sleep=parar)
        self.assertEqual(1, svc.stats.ticks)

    def test_status_reports_no_adapter_honestly(self):
        svc = self._svc()
        self.assertEqual([], svc.status()["adapters"])


if __name__ == "__main__":
    unittest.main()
