"""The first draft selection survives reconnect without overriding later preferences."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_turn_cost_persistence import _ContextLoader, _PreparedGateway, _Resolver

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_service import InteractionService
from kairos_providers import ModelPrice, ProviderEvent, ProviderModelRef
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository


class InitialSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_turn_remembers_draft_profile_and_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "state.db")
            initialize_schema(conn)
            sessions = SessionRepository(conn)
            service = InteractionService(
                gateway=_PreparedGateway(
                    [ProviderEvent(kind="finish", finish_reason="stop")], ModelPrice()
                ),
                resolver=_Resolver(),
                context_loader=_ContextLoader(),
                sessions=sessions,
                messages=MessageRepository(conn),
                usage=UsageRepository(conn),
            )
            envelope = InteractionEnvelope(
                conversation_id="draft",
                source="web",
                content="hello",
                profile="pesquisa",
                parameters={"routing": {"allow_fallbacks": False}},
            )
            try:
                _ = [event async for event in service.stream(envelope)]
                selection = sessions.selection("draft")
                self.assertIsNotNone(selection)
                self.assertEqual(selection.parameters, {"routing": {"allow_fallbacks": False}})
                self.assertEqual(selection.profile, "pesquisa")
                sessions.set_selection("draft", ProviderModelRef("fake", "saved"), {"seed": 7})
                _ = [
                    event
                    async for event in service.stream(replace(envelope, parameters={"seed": 9}))
                ]
                self.assertEqual(sessions.selection("draft").parameters, {"seed": 7})
            finally:
                conn.close()
