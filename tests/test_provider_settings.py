"""Settings validation, persistence and actual adapter destinations."""

import asyncio

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos_providers.adapter_contract import AdapterRequest, CanonicalMessage, ContentPart
from kairos_providers.composition import build_provider_gateway
from kairos_providers.contracts import ProviderModelRef


def test_saved_settings_drive_discovery_test_and_generation(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_settings": {
                    "custom": {
                        "base_url": "https://private.example/v2",
                        "models_url": "https://private.example/catalog",
                        "trusted_remote": True,
                        "headers": {"X-Title": "Kairos"},
                    },
                    "openrouter": {"referer": "https://kairos.example", "title": "Kairos test"},
                }
            }
        )
    )
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "private-chat"}]})
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"Content-Type": "text/event-stream"},
        )

    async def run():
        gateway = build_provider_gateway(
            tmp_path,
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )
        try:
            custom = gateway.registry.create("custom", api_key="test-key")
            assert (await custom.discover_models())[0].ref.model == "private-chat"
            assert (await custom.test_connection()).ok
            events = [
                event
                async for event in custom.stream(
                    AdapterRequest(
                        ProviderModelRef("custom", "private-chat"),
                        (CanonicalMessage("user", (ContentPart("text", "hello"),)),),
                    )
                )
            ]
            assert any(event.text == "ok" for event in events)
            router = gateway.registry.create("openrouter", api_key="router-key")
            await router.discover_models()
        finally:
            await gateway.aclose()

    asyncio.run(run())
    assert [str(request.url) for request in requests[:3]] == [
        "https://private.example/catalog",
        "https://private.example/catalog",
        "https://private.example/v2/chat/completions",
    ]
    assert all(request.headers["x-title"] == "Kairos" for request in requests[:3])
    assert requests[-1].headers["HTTP-Referer"] == "https://kairos.example"
    assert requests[-1].headers["X-OpenRouter-Title"] == "Kairos test"


@pytest.fixture
def settings_client(tmp_path):
    from kairos_web.provider_settings_api import router

    app = FastAPI()
    app.state.kairos_home = tmp_path
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_settings_preserve_config_and_require_explicit_destination_consent(
    settings_client, tmp_path
):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"model": "existing", "unrelated": {"value": 3}}))
    body = {"settings": {"base_url": "https://new.example/v1", "trusted_remote": True}}
    denied = settings_client.put("/api/providers/custom/settings", json=body)
    assert denied.status_code == 409
    assert yaml.safe_load(path.read_text()) == {"model": "existing", "unrelated": {"value": 3}}
    body["confirm_credential_transfer"] = True
    response = settings_client.put("/api/providers/custom/settings", json=body)
    assert response.status_code == 200
    assert response.json()["settings"]["models_url"] == "https://new.example/v1/models"
    assert settings_client.get("/api/providers/custom/settings").json() == response.json()
    saved = yaml.safe_load(path.read_text())
    assert saved["model"] == "existing"
    assert saved["unrelated"] == {"value": 3}
    assert "confirm_credential_transfer" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "settings",
    [
        {"base_url": "https://user:private-secret@example.test/v1", "trusted_remote": True},
        {"base_url": "https://example.test/v1?key=private-secret", "trusted_remote": True},
        {"base_url": "https://example.test/v1", "trusted_remote": "false"},
        {"base_url": "https://example.test/v1"},
        {
            "base_url": "https://example.test/v1",
            "models_url": "https://elsewhere.test/models",
            "trusted_remote": True,
        },
        {"headers": {"Authorization": "Bearer private-secret"}},
        {"headers": {"X-Title": "bad\r\nAuthorization: private-secret"}},
        {"secret": "private-secret"},
        {"headers": ["private-secret"]},
    ],
)
def test_invalid_custom_settings_never_echo_or_persist_secrets(settings_client, tmp_path, settings):
    response = settings_client.put(
        "/api/providers/custom/settings",
        json={
            "settings": settings,
            "confirm_credential_transfer": True,
        },
    )
    assert response.status_code == 422
    assert "private-secret" not in response.text
    assert not (tmp_path / "config.yaml").exists()


@pytest.mark.parametrize(
    "settings",
    [
        {"referer": "https://example.test/?secret=private-secret"},
        {"title": "bad\r\nprivate-secret"},
        {"headers": {"Authorization": "private-secret"}},
        {"title": 123},
    ],
)
def test_invalid_router_attribution_is_rejected(settings_client, settings):
    response = settings_client.put(
        "/api/providers/openrouter/settings", json={"settings": settings}
    )
    assert response.status_code == 422
    assert "private-secret" not in response.text


def test_unsupported_provider_settings_are_not_created(settings_client):
    assert (
        settings_client.put("/api/providers/ollama/settings", json={"settings": {}}).status_code
        == 404
    )
    assert settings_client.get("/api/providers/openai/settings").status_code == 404


def test_invalid_persisted_settings_fail_closed(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "provider_settings:\n  custom:\n    headers:\n      Authorization: secret\n"
    )
    with pytest.raises(ValueError):
        build_provider_gateway(tmp_path)


def test_custom_catalog_does_not_cross_endpoint_changes(tmp_path):
    from kairos_providers.catalog import CatalogSnapshot
    from kairos_providers.catalog_store import CatalogSnapshotStore
    from kairos_providers.contracts import CatalogModel, ModelCapabilities

    old = CatalogModel(
        ProviderModelRef("custom", "old-model"),
        "Old model",
        capabilities=ModelCapabilities(chat=True),
    )
    CatalogSnapshotStore(tmp_path / "model-catalog.json").save(
        "custom", CatalogSnapshot((old,), 1, 9999999999)
    )
    gateway = build_provider_gateway(tmp_path)
    assert not gateway.catalog.list_models(provider="custom")
