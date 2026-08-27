from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kairos_providers import ProviderModelRef, ResolvedModelSelection, SelectionReason, TokenUsage
from kairos_state import connect, initialize_schema
from kairos_state.repositories import (
    BillingRoute,
    MessageRepository,
    SessionRepository,
    TokenDelta,
    UsageRepository,
)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state.db"
        self.db = connect(self.path)
        initialize_schema(self.db)
        self.sessions = SessionRepository(self.db)
        self.messages = MessageRepository(self.db)
        self.usage = UsageRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    @staticmethod
    def selection(
        provider: str = "openrouter",
        model: str = "openrouter/free",
        *,
        reason: SelectionReason = SelectionReason.CONVERSATION_OVERRIDE,
    ) -> ResolvedModelSelection:
        return ResolvedModelSelection(
            ref=ProviderModelRef(provider, model),
            reason=reason,
        )


class SessionSelectionTests(Base):
    def test_troca_de_modelo_preserva_historico_e_afeta_proximo_turno(self) -> None:
        self.sessions.create(
            "s1",
            source="web",
            model="old",
            model_config=json.dumps({"_branched_from": "origem", "legacy": True}, sort_keys=True),
        )

        self.sessions.set_selection(
            "s1",
            ProviderModelRef("openrouter", "openrouter/free"),
            {"free_only": True},
            reason=SelectionReason.CONVERSATION_OVERRIDE,
        )

        persisted = self.sessions.selection("s1")
        assert persisted is not None
        self.assertEqual(persisted.ref, ProviderModelRef("openrouter", "openrouter/free"))
        self.assertEqual(persisted.parameters, {"free_only": True})
        self.assertEqual(persisted.reason, SelectionReason.CONVERSATION_OVERRIDE)
        row = self.db.execute("SELECT model, model_config FROM sessions WHERE id='s1'").fetchone()
        self.assertEqual(row["model"], "openrouter/free")
        self.assertEqual(self.messages.for_api("s1"), [])
        self.assertEqual(
            row["model_config"],
            json.dumps(
                {
                    "_branched_from": "origem",
                    "legacy": True,
                    "parameters": {"free_only": True},
                    "provider": "openrouter",
                    "reason": "conversation_override",
                },
                sort_keys=True,
            ),
        )

    def test_sessao_antiga_sem_provider_permanece_legivel(self) -> None:
        self.sessions.create("s1", source="web", model="gpt-legado")

        self.assertIsNone(self.sessions.selection("s1"))


class TurnMessageTests(Base):
    def test_mensagem_guarda_selecao_efetiva_em_display_metadata(self) -> None:
        self.sessions.create("s1", source="web")

        message_id = self.messages.append_turn_message(
            "s1",
            "assistant",
            "ok",
            self.selection(),
        )

        row = self.db.execute(
            "SELECT content, display_metadata FROM messages WHERE id=?",
            (message_id,),
        ).fetchone()
        self.assertEqual(row["content"], "ok")
        self.assertEqual(
            row["display_metadata"],
            json.dumps(
                {
                    "model": "openrouter/free",
                    "provider": "openrouter",
                    "reason": "conversation_override",
                },
                sort_keys=True,
            ),
        )


class UsageEventTests(Base):
    def test_actual_only_costs_sum_without_requiring_estimates(self) -> None:
        self.sessions.create("s1", source="web")
        route = BillingRoute(
            "s1",
            "openrouter/free",
            "openrouter",
            "https://openrouter.ai/api/v1",
            "api_key",
        )
        self.usage.queue(
            route,
            TokenDelta(actual_cost_usd=0.02, cost_status="actual", cost_source="upstream"),
        )
        self.usage.flush(now=1)
        self.usage.queue(
            route,
            TokenDelta(actual_cost_usd=0.03, cost_status="actual", cost_source="upstream"),
        )
        self.usage.flush(now=2)

        row = self.db.execute(
            "SELECT estimated_cost_usd, actual_cost_usd, cost_status FROM session_model_usage"
        ).fetchone()
        assert row is not None
        self.assertIsNone(row["estimated_cost_usd"])
        self.assertEqual(row["actual_cost_usd"], 0.05)
        self.assertEqual(row["cost_status"], "actual")

    def test_estimated_and_actual_costs_keep_independent_sums(self) -> None:
        self.sessions.create("s1", source="web")
        route = BillingRoute(
            "s1",
            "openrouter/free",
            "openrouter",
            "https://openrouter.ai/api/v1",
            "api_key",
        )
        self.usage.queue(
            route,
            TokenDelta(
                estimated_cost_usd=0.04,
                cost_status="estimated",
                cost_source="catalog",
            ),
        )
        self.usage.flush(now=1)
        self.usage.queue(
            route,
            TokenDelta(
                actual_cost_usd=0.05,
                cost_status="actual",
                cost_source="upstream",
            ),
        )
        self.usage.flush(now=2)

        row = self.db.execute(
            "SELECT estimated_cost_usd, actual_cost_usd, cost_status, cost_source "
            "FROM session_model_usage"
        ).fetchone()
        assert row is not None
        self.assertEqual((row["estimated_cost_usd"], row["actual_cost_usd"]), (0.04, 0.05))
        self.assertEqual(row["cost_status"], "estimated")
        self.assertIsNone(row["cost_source"])

    def test_coalesced_unknown_cost_clears_partial_estimate(self) -> None:
        self.sessions.create("s1", source="web")
        route = BillingRoute(
            "s1",
            "openrouter/free",
            "openrouter",
            "https://openrouter.ai/api/v1",
            "api_key",
        )
        self.usage.queue(
            route,
            TokenDelta(
                estimated_cost_usd=0.04,
                cost_status="estimated",
                cost_source="catalog",
            ),
        )
        self.usage.queue(route, TokenDelta(input_tokens=1, cost_status="unknown"))
        self.usage.flush(now=1)

        row = self.db.execute(
            "SELECT estimated_cost_usd, actual_cost_usd, cost_status FROM session_model_usage"
        ).fetchone()
        assert row is not None
        self.assertEqual(tuple(row), (None, None, "unknown"))

    def test_persisted_unknown_cost_clears_partial_estimate(self) -> None:
        self.sessions.create("s1", source="web")
        route = BillingRoute(
            "s1",
            "openrouter/free",
            "openrouter",
            "https://openrouter.ai/api/v1",
            "api_key",
        )
        self.usage.queue(
            route,
            TokenDelta(
                estimated_cost_usd=0.04,
                cost_status="estimated",
                cost_source="catalog",
            ),
        )
        self.usage.flush(now=1)
        self.usage.queue(route, TokenDelta(input_tokens=1, cost_status="unknown"))
        self.usage.flush(now=2)

        row = self.db.execute(
            "SELECT estimated_cost_usd, actual_cost_usd, cost_status FROM session_model_usage"
        ).fetchone()
        assert row is not None
        self.assertEqual(tuple(row), (None, None, "unknown"))

    def test_record_event_enfileira_sem_gravar_no_banco(self) -> None:
        self.sessions.create("s1", source="web")

        self.usage.record_event(
            "s1",
            self.selection(),
            billing_provider="openrouter",
            billing_base_url="https://openrouter.ai/api/v1",
            billing_mode="api_key",
            usage=TokenUsage(input_tokens=3, output_tokens=5, reasoning_tokens=2),
            api_call_count=1,
        )

        self.assertEqual(self.usage.pending_count(), 1)
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM session_model_usage").fetchone()[0],
            0,
        )

        self.assertEqual(self.usage.flush(), 1)
        row = self.db.execute(
            "SELECT model, billing_provider, api_call_count, input_tokens, output_tokens, "
            "reasoning_tokens FROM session_model_usage"
        ).fetchone()
        self.assertEqual(
            (
                row["model"],
                row["billing_provider"],
                row["api_call_count"],
                row["input_tokens"],
                row["output_tokens"],
                row["reasoning_tokens"],
            ),
            ("openrouter/free", "openrouter", 1, 3, 5, 2),
        )

    def test_flush_persiste_custo_estimado_e_sumario_da_sessao(self) -> None:
        """Ignorar colunas existentes deixa dashboard e rateio sem custo consultável."""
        self.sessions.create("s1", source="web")
        self.usage.record_event(
            "s1",
            self.selection(provider="custom", model="priced"),
            billing_provider="custom",
            billing_base_url="https://models.example.test/v1",
            billing_mode="api_key",
            usage=TokenUsage(input_tokens=3, output_tokens=2),
            api_call_count=1,
            estimated_cost_usd=0.017,
            actual_cost_usd=None,
            cost_status="estimated",
            cost_source="catalog",
        )

        self.usage.flush(now=1.0)

        route = self.db.execute(
            "SELECT estimated_cost_usd, actual_cost_usd, cost_status, cost_source "
            "FROM session_model_usage WHERE session_id = 's1'"
        ).fetchone()
        session = self.db.execute(
            "SELECT estimated_cost_usd, actual_cost_usd, cost_status, cost_source, "
            "billing_provider, billing_base_url, billing_mode FROM sessions WHERE id = 's1'"
        ).fetchone()
        self.assertEqual(tuple(route), (0.017, None, "estimated", "catalog"))
        self.assertEqual(
            tuple(session),
            (
                0.017,
                None,
                "estimated",
                "catalog",
                "custom",
                "https://models.example.test/v1",
                "api_key",
            ),
        )
