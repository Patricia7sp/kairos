"""History exposes only structured accounting and preserves unknown legacy costs."""

import json

from test_web_provider_api import WebProviderApiContractTests as _BaseApiTests

from kairos_state import connect, default_db_path, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository


class ChatMetadataTests(_BaseApiTests):
    def test_history_exposes_cost_and_usage_without_arbitrary_metadata(self):
        conn = connect(default_db_path())
        initialize_schema(conn)
        SessionRepository(conn).create("cost-history", source="web")
        messages = MessageRepository(conn)
        cost = {"estimated_usd": 0, "actual_usd": None, "status": "estimated", "source": "catalog"}
        messages.append(
            "cost-history",
            "assistant",
            content="zero",
            display_metadata=json.dumps(
                {
                    "cost": {**cost, "api_key": "never-expose"},
                    "usage": {"total_tokens": 3},
                    "credential_id": "never-expose",
                }
            ),
        )
        messages.append("cost-history", "assistant", content="legacy")
        messages.append("cost-history", "assistant", content="malformed", display_metadata="[]")
        conn.close()
        response = self.client.get("/api/sessions/cost-history/messages")
        self.assertEqual(response.status_code, 200)
        payload = response.json()["messages"]
        self.assertEqual(payload[0]["cost"], cost)
        self.assertEqual(payload[0]["usage"], {"total_tokens": 3})
        self.assertIsNone(payload[1]["cost"])
        self.assertIsNone(payload[2]["cost"])
        self.assertNotIn("never-expose", response.text)
