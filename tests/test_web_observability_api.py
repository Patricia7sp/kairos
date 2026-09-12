"""Observed status and accounting must not manufacture provider activity or costs."""

from pathlib import Path
from time import time

import pytest
from fastapi.testclient import TestClient

from kairos_providers.catalog import CatalogSnapshot
from kairos_providers.catalog_store import CatalogSnapshotStore
from kairos_providers.contracts import CatalogModel, ModelCapabilities, ProviderModelRef
from kairos_runtime import RuntimeErrorInfo
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository
from kairos_state.repositories.usage import BillingRoute, TokenDelta, UsageRepository
from kairos_web import server


@pytest.fixture
def observed_api(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server.app.state, "kairos_home", tmp_path, raising=False)
    monkeypatch.setattr(server.app.state, "runtime_client", None, raising=False)
    return TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}), tmp_path


def test_status_without_observation_does_not_claim_running_or_invent_model(observed_api):
    client, _ = observed_api
    data = client.get("/api/status").json()
    assert data["gateway"] == "unknown"
    assert data["runtime"]["state"] == "unknown"
    assert data["model"] is None
    assert data["provider"] is None
    assert data["status"] == "ok"


@pytest.mark.parametrize("available", [True, False])
def test_status_observes_runtime_without_leaking_internal_errors(
    observed_api, monkeypatch, available
):
    client, _ = observed_api

    class Runtime:
        async def status(self):
            if not available:
                raise RuntimeErrorInfo("unavailable", "secret-private-path", False)
            return {"enabled": True, "state": "ready", "authorized_projects": ["/private"]}

    monkeypatch.setattr(server.app.state, "runtime_client", Runtime())
    response = client.get("/api/status")
    assert response.json()["runtime"]["state"] == ("ready" if available else "unavailable")
    assert "private" not in response.text
    assert response.json()["gateway"] == "unknown"


def test_model_info_respects_application_home_and_unknown_capabilities(observed_api):
    client, home = observed_api
    (home / "config.yaml").write_text("provider: custom\nmodel: local-unknown\n")
    data = client.get("/api/model/info").json()
    assert (data["provider"], data["model"]) == ("custom", "local-unknown")
    assert all(value is None for value in data["capabilities"].values())
    assert client.get("/api/status").json()["model"] == "local-unknown"


def test_model_info_uses_catalog_capabilities(observed_api):
    client, home = observed_api
    (home / "config.yaml").write_text("provider: openrouter\nmodel: fixture-model\n")
    CatalogSnapshotStore(home / "model-catalog.json").save(
        "openrouter",
        CatalogSnapshot(
            models=(
                CatalogModel(
                    ref=ProviderModelRef("openrouter", "fixture-model"),
                    display_name="Fixture",
                    capabilities=ModelCapabilities(tools=False, vision=True, streaming=None),
                ),
            ),
            fetched_at=time(),
            expires_at=time() + 3600,
        ),
    )
    data = client.get("/api/model/info").json()
    assert data["provider"] == "openrouter"
    assert data["capabilities"] == {
        "supports_tools": False,
        "supports_vision": True,
        "supports_streaming": None,
        "supports_reasoning": None,
    }


def test_usage_aggregates_billing_routes_without_calling_unknown_cost_zero(observed_api):
    client, home = observed_api
    db = connect(home / "state.db")
    initialize_schema(db)
    SessionRepository(db).create("usage", "web")
    usage = UsageRepository(db)
    for provider, delta in [
        (
            "actual",
            TokenDelta(
                api_call_count=2,
                input_tokens=100,
                output_tokens=20,
                actual_cost_usd=0.3,
                cost_status="actual",
            ),
        ),
        (
            "estimate",
            TokenDelta(
                api_call_count=1,
                input_tokens=50,
                output_tokens=10,
                estimated_cost_usd=0.2,
                cost_status="estimated",
            ),
        ),
        (
            "unknown",
            TokenDelta(api_call_count=1, input_tokens=5, output_tokens=1, cost_status="unknown"),
        ),
    ]:
        usage.queue(BillingRoute("usage", "model", provider, "https://private", "api"), delta)
    usage.flush(now=100)
    db.close()
    response = client.get("/api/analytics/usage")
    data = response.json()
    assert data["totals"]["requests"] == 4
    assert data["totals"]["tokens"] == 186
    assert data["totals"]["actual_cost_usd"] == pytest.approx(0.3)
    assert data["totals"]["estimated_cost_usd"] == pytest.approx(0.2)
    assert data["totals"]["cost_status"] == "unknown"
    assert data["totals"]["unknown_cost_routes"] == 1
    assert data["period"] == "all_time"
    assert data["daily_status"] == "unavailable"
    assert "private" not in response.text


def test_absent_usage_database_is_explicitly_unavailable_and_not_created(observed_api):
    client, home = observed_api
    data = client.get("/api/analytics/usage").json()
    assert data["availability"] == "unavailable"
    assert data["totals"]["actual_cost_usd"] is None
    assert not (home / "state.db").exists()


def test_observability_requires_authentication(observed_api):
    for path in ("/api/status", "/api/model/info", "/api/analytics/usage"):
        assert TestClient(server.app).get(path).status_code == 401


def test_profiles_use_saved_routing_profiles_and_exclude_private_fields(observed_api):
    import yaml

    client, home = observed_api
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider": "openrouter",
                "model": "base",
                "profiles": {
                    "Pesquisa": {
                        "provider": "openai",
                        "model": "research",
                        "api_key": "private-secret",
                        "parameters": {"temperature": 0.2},
                    }
                },
            }
        )
    )
    data = client.get("/api/profiles").json()
    assert {p["name"] for p in data["profiles"]} == {"Pesquisa"}
    profile = next(p for p in data["profiles"] if p["name"] == "Pesquisa")
    assert profile["model"] == "research"
    assert profile["provider"] == "openai"
    assert "private-secret" not in client.get("/api/profiles").text
    assert client.get("/api/profiles/active").json() == {
        "name": None,
        "provider": "openrouter",
        "model": "base",
    }
