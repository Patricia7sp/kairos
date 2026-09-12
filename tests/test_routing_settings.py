"""Routing preferences survive selection and reach the selected model unchanged."""

import asyncio
import json
import unittest
from dataclasses import replace

import httpx
import pytest
from test_openrouter_adapter import simple_request, sse
from test_web_provider_api import WebProviderApiContractTests as _BaseApiTests

from kairos_providers import ProviderError
from kairos_providers.adapters.openrouter import OpenRouterAdapter


def test_explicit_routing_reaches_upstream_without_changing_model():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=sse(
                {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}, "[DONE]"
            ),
        )

    policy = {"data_collection": "allow", "require_parameters": False, "allow_fallbacks": False}

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = replace(simple_request(), parameters={"routing": policy})
            return [event async for event in OpenRouterAdapter(client, "test").stream(request)]

    asyncio.run(run())
    assert sent[0]["provider"] == policy
    assert sent[0]["model"] == "acme/chat"
    assert "models" not in sent[0]


@pytest.mark.parametrize(
    "routing",
    [
        {"allow_fallbacks": "false"},
        {"require_parameters": 1},
        {"data_collection": "invalid"},
        {"api_key": "do-not-expose"},
        [],
        None,
    ],
)
def test_invalid_routing_rejected_before_network(routing):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: pytest.fail("invalid routing must not make a request")
            )
        ) as client:
            with pytest.raises(ProviderError):
                async for _ in OpenRouterAdapter(client, "test").stream(
                    replace(simple_request(), parameters={"routing": routing})
                ):
                    pass

    asyncio.run(run())


class RoutingApiTests(unittest.TestCase):
    setUp = _BaseApiTests.setUp
    tearDown = _BaseApiTests.tearDown

    def test_general_config_cannot_bypass_endpoint_transfer_confirmation(self):
        response = self.client.put(
            "/api/config",
            json={
                "config": {
                    "provider_settings": {
                        "custom": {
                            "base_url": "https://another.example/v1",
                            "trusted_remote": True,
                        },
                    }
                }
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("provider_settings", self.client.get("/api/config").json())

    def test_conversation_policy_round_trip_and_profile_can_be_cleared(self):
        from kairos_state import connect, default_db_path, initialize_schema
        from kairos_state.repositories import SessionRepository

        conn = connect(default_db_path())
        initialize_schema(conn)
        SessionRepository(conn).create("profile-conversation", source="web")
        conn.close()
        selection = {
            "provider": "openrouter",
            "model": "openrouter/free",
            "scope": "conversation",
            "session_id": "profile-conversation",
            "profile": "pesquisa",
            "parameters": {"routing": {"allow_fallbacks": False}},
        }
        response = self.client.post("/api/models/selection", json=selection)
        self.assertEqual(response.status_code, 200)
        detail = self.client.get("/api/sessions/profile-conversation").json()["selection"]
        self.assertEqual(detail["profile"], "pesquisa")
        self.assertEqual(detail["parameters"], selection["parameters"])
        self.client.post("/api/models/selection", json={**selection, "profile": ""})
        detail = self.client.get("/api/sessions/profile-conversation").json()["selection"]
        self.assertNotIn("profile", detail)

    def test_profile_policy_is_public_and_does_not_replace_global(self):
        policy = {"data_collection": "deny", "require_parameters": True, "allow_fallbacks": False}
        response = self.client.post(
            "/api/models/selection",
            json={
                "provider": "openrouter",
                "model": "openrouter/free",
                "scope": "profile",
                "profile": "pesquisa",
                "parameters": {"routing": policy},
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = self.client.get("/api/models").json()
        self.assertEqual(
            payload["profiles"],
            [
                {
                    "name": "pesquisa",
                    "provider": "openrouter",
                    "model": "openrouter/free",
                    "parameters": {"routing": policy},
                }
            ],
        )
        self.assertNotEqual(payload["default_provider"], "openrouter")

    def test_invalid_policy_does_not_persist_or_echo_secret(self):
        response = self.client.post(
            "/api/models/selection",
            json={
                "provider": "openrouter",
                "model": "openrouter/free",
                "scope": "global",
                "parameters": {"routing": {"api_key": "never-return-me"}},
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("never-return-me", response.text)
        self.assertNotIn("parameters", self.client.get("/api/config").json())

    def test_changing_model_without_parameters_preserves_profile_policy(self):
        policy = {"routing": {"allow_fallbacks": False}}
        selection = {
            "provider": "openrouter",
            "model": "openrouter/free",
            "scope": "profile",
            "profile": "pesquisa",
        }
        self.client.post("/api/models/selection", json={**selection, "parameters": policy})
        self.client.post("/api/models/selection", json=selection)
        config = self.client.get("/api/config").json()
        self.assertEqual(config["profiles"]["pesquisa"]["parameters"], policy)

    def test_partial_parameter_updates_preserve_unrelated_settings_in_every_scope(self):
        from kairos_state import connect, default_db_path, initialize_schema
        from kairos_state.repositories import SessionRepository

        existing = {
            "temperature": 0.7,
            "routing": {
                "data_collection": "deny",
                "require_parameters": True,
                "allow_fallbacks": True,
            },
        }
        expected = {
            "temperature": 0.7,
            "routing": {
                "data_collection": "deny",
                "require_parameters": True,
                "allow_fallbacks": False,
            },
        }
        patch = {"routing": {"allow_fallbacks": False}}

        conn = connect(default_db_path())
        initialize_schema(conn)
        SessionRepository(conn).create("partial-parameters", source="web")
        conn.close()

        cases = (
            ({"scope": "conversation", "session_id": "partial-parameters"}, "conversation"),
            ({"scope": "profile", "profile": "pesquisa"}, "profile"),
            ({"scope": "global"}, "global"),
        )
        for scope, name in cases:
            with self.subTest(scope=name):
                selection = {
                    "provider": "openrouter",
                    "model": "openrouter/free",
                    **scope,
                }
                self.assertEqual(
                    self.client.post(
                        "/api/models/selection",
                        json={**selection, "parameters": existing},
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    self.client.post(
                        "/api/models/selection",
                        json={**selection, "parameters": patch},
                    ).status_code,
                    200,
                )
                if name == "conversation":
                    actual = self.client.get("/api/sessions/partial-parameters").json()[
                        "selection"
                    ]["parameters"]
                elif name == "profile":
                    actual = self.client.get("/api/config").json()["profiles"]["pesquisa"][
                        "parameters"
                    ]
                else:
                    actual = self.client.get("/api/config").json()["parameters"]
                self.assertEqual(actual, expected)


del _BaseApiTests
