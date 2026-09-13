"""Cron delivery: targets derived from registered adapters, durable obligations.

TT-10: destinos derivados dos adapters registrados — sem lista de plataforma
hardcoded — e a saída do job percorre o ledger durável de obrigações do gateway.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from kairos_cron.delivery import (
    DELIVERY_PAYLOAD_MAX_CHARS,
    DeliveryTargetError,
    cron_delivery_targets,
    record_cron_delivery,
    validate_delivery,
)
from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler
from kairos_state.repositories.ledger import LedgerRepository

NOW = datetime(2026, 9, 13, 9, tzinfo=UTC)


class TestDeliveryTargets:
    def test_targets_derive_exclusively_from_registered_adapters(self):
        derivados = cron_delivery_targets(["wpp", "telegram", "slack_pub"])
        assert [t["id"] for t in derivados] == ["slack_pub", "telegram", "wpp"]
        assert derivados[0]["name"] == "Slack Pub"

    def test_no_hardcoded_platform_list_anywhere(self):
        assert cron_delivery_targets({"nova_deploy"}) == [
            {"id": "nova_deploy", "name": "Nova Deploy"}
        ]
        assert cron_delivery_targets({"wpp"}) == [{"id": "wpp", "name": "Wpp"}]

    def test_local_is_implicit_to_the_listing_surface_only(self):
        assert cron_delivery_targets(["local"]) == [{"id": "local", "name": "Local"}]
        assert "local" not in {t["id"] for t in cron_delivery_targets(["wpp"])}

    def test_registers_without_names_are_ignored(self):
        derivados = cron_delivery_targets(["telegram", None, "", "  ", 7, "wpp"])
        assert [t["id"] for t in derivados] == ["telegram", "wpp"]


class TestValidateDelivery:
    def test_none_stays_none(self):
        assert validate_delivery(None) is None

    def test_well_formed_target_is_normalized(self):
        assert validate_delivery({"target": "wpp:+5511999990000"}) == {
            "target": "wpp:+5511999990000"
        }

    @pytest.mark.parametrize(
        "delivery",
        [
            "wpp:alice",
            {"alvo": "wpp:alice"},
            {"target": ""},
            {"target": "wpp:"},
            {"target": ":alice"},
            {"target": "semseparador"},
            {"target": "x" * 201},
            {"target": "wpp:a\nlice"},
        ],
    )
    def test_malformed_target_raises(self, delivery):
        with pytest.raises(DeliveryTargetError):
            validate_delivery(delivery)

    def test_unknown_platform_rejected_when_adapters_are_registered(self):
        with pytest.raises(DeliveryTargetError, match="não está entre os adapters registrados"):
            validate_delivery({"target": "orb:chat"}, adapters=["telegram", "wpp"])

    def test_registered_platform_accepted(self):
        assert validate_delivery({"target": "wpp:alice"}, adapters=["wpp"]) == {
            "target": "wpp:alice"
        }

    def test_empty_adapter_set_checks_shape_only(self):
        assert validate_delivery({"target": "orb:chat"}, adapters=[]) == {"target": "orb:chat"}


class TestRecordCronDelivery:
    def test_record_creates_pending_obligation_in_durable_ledger(self, tmp_path):
        registro = record_cron_delivery(tmp_path, "cron-abc", "wpp:alice", "relatório pronto")
        assert registro == {
            "obligation_id": "cron-abc",
            "target": "wpp:alice",
            "state": "pending",
        }
        from kairos_state import connect, migrate

        db = connect(tmp_path / "state.db")
        try:
            migrate(db)
            pendentes = LedgerRepository(db).pending()
        finally:
            db.close()
        assert [o.obligation_id for o in pendentes] == ["cron-abc"]
        assert pendentes[0].target == "wpp:alice"
        assert pendentes[0].payload == "relatório pronto"

    def test_rerecord_replaces_payload_idempotently(self, tmp_path):
        record_cron_delivery(tmp_path, "cron-x", "telegram:eu", "primeira")
        record_cron_delivery(tmp_path, "cron-x", "telegram:eu", "segunda")
        from kairos_state import connect, migrate

        db = connect(tmp_path / "state.db")
        try:
            migrate(db)
            pendentes = LedgerRepository(db).pending()
        finally:
            db.close()
        assert [o.obligation_id for o in pendentes] == ["cron-x"]
        assert pendentes[0].payload == "segunda"

    def test_payload_is_truncated_to_documented_cap(self, tmp_path):
        record_cron_delivery(tmp_path, "cron-y", "wpp:a", "x" * (DELIVERY_PAYLOAD_MAX_CHARS + 50))
        from kairos_state import connect, migrate

        db = connect(tmp_path / "state.db")
        try:
            migrate(db)
            pendentes = LedgerRepository(db).pending()
        finally:
            db.close()
        assert len(pendentes[0].payload) == DELIVERY_PAYLOAD_MAX_CHARS

    def test_clean_volume_stays_clean_without_delivery(self, tmp_path):
        store = JobStore(tmp_path)
        store.create(
            name="x",
            prompt="x",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
        )
        assert not (tmp_path / "state.db").exists()


def _obligations(home):
    from kairos_state import connect, migrate

    db = connect(home / "state.db")
    try:
        migrate(db)
        return LedgerRepository(db).pending()
    finally:
        db.close()


class FluentService:
    def __init__(self, *kinds):
        self.kinds = kinds
        self.envelopes = []

    async def stream(self, envelope):
        self.envelopes.append(envelope)
        for kind in self.kinds:
            if isinstance(kind, str):
                yield SimpleNamespace(kind="delta", text=kind)
            else:
                yield kind


class TestSchedulerDeliveryIntegration:
    def test_completed_turn_records_durable_obligation_with_output(self, tmp_path):
        store = JobStore(tmp_path)
        job = store.create(
            name="Relatório",
            prompt="Escreva o relatório",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
            delivery={"target": "wpp:alice"},
        )
        service = FluentService(
            "início do relatório",
            " — fechado.",
            SimpleNamespace(kind="turn_end"),
        )
        report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
        assert report["executed"] == 1
        assert report["failed"] == 0
        assert store.history(job["id"])[0]["status"] == "completed"
        pendentes = _obligations(tmp_path)
        assert len(pendentes) == 1
        assert pendentes[0].obligation_id == "cron-" + service.envelopes[0].conversation_id[5:]
        assert pendentes[0].target == "wpp:alice"
        assert pendentes[0].payload == "início do relatório — fechado."

    def test_job_without_delivery_creates_no_obligation(self, tmp_path):
        store = JobStore(tmp_path)
        store.create(
            name="Relatório",
            prompt="Escreva o relatório",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
        )
        asyncio.run(
            Scheduler(tmp_path, FluentService(SimpleNamespace(kind="turn_end"))).tick(now=NOW)
        )
        assert _obligations(tmp_path) == []

    def test_failed_turn_never_records_delivery(self, tmp_path):
        store = JobStore(tmp_path)
        job = store.create(
            name="Relatório",
            prompt="Escreva o relatório",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
            delivery={"target": "wpp:alice"},
        )
        service = FluentService("parcial", SimpleNamespace(kind="turn_error"))
        report = asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
        assert report["failed"] == 1
        assert store.history(job["id"])[0]["status"] == "failed"
        assert _obligations(tmp_path) == []

    def test_storage_failure_never_breaks_the_completed_turn(self, tmp_path, monkeypatch):
        store = JobStore(tmp_path)
        job = store.create(
            name="Relatório",
            prompt="Escreva o relatório",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
            delivery={"target": "wpp:alice"},
        )
        import kairos_cron.delivery as delivery_module

        def explode(_home, _obligation_id, _target, _payload):
            raise OSError("storage indisponível")

        monkeypatch.setattr(delivery_module, "record_cron_delivery", explode)
        report = asyncio.run(
            Scheduler(tmp_path, FluentService(SimpleNamespace(kind="turn_end"))).tick(now=NOW)
        )
        assert report["executed"] == 1
        assert report["failed"] == 0
        assert store.history(job["id"])[0]["status"] == "completed"
