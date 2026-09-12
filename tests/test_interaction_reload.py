from __future__ import annotations

import json
import time

import httpx
import pytest
import yaml

from kairos_integration import InteractionEnvelope, build_interaction_service, composition
from kairos_providers.catalog import CatalogSnapshot
from kairos_providers.composition import build_provider_gateway as build_real_gateway
from kairos_providers.contracts import (
    CatalogModel,
    ModelCapabilities,
    ProviderModelRef,
)
from kairos_providers.selection import ModelSelectionUnavailableError
from kairos_providers.settings import EndpointCatalogSnapshotStore, settings_from_document
from kairos_security.credentials import (
    CredentialRef,
    CredentialSecret,
    build_credential_service,
)


def _config(
    home,
    *,
    origin: str,
    model: str,
    title: str,
    profile_model: str | None = None,
) -> None:
    document = {
        "provider": "custom",
        "model": model,
        "provider_settings": {
            "custom": {
                "base_url": f"{origin}/v1",
                "models_url": f"{origin}/models",
                "trusted_remote": True,
                "headers": {"X-Title": title},
            }
        },
    }
    if profile_model is not None:
        document["profiles"] = {"research": {"provider": "custom", "model": profile_model}}
    (home / "config.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


def _catalog(home, *models: str, expired: bool = False) -> None:
    document = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    settings = settings_from_document(document, "custom")
    snapshot = CatalogSnapshot(
        tuple(
            CatalogModel(
                ProviderModelRef("custom", model),
                model,
                capabilities=ModelCapabilities(chat=True, streaming=True),
            )
            for model in models
        ),
        fetched_at=time.time(),
        expires_at=time.time() - 1 if expired else time.time() + 3600,
    )
    EndpointCatalogSnapshotStore(
        home,
        {
            "base_url": settings["base_url"],
            "models_url": settings["models_url"],
            "headers": settings["headers"],
        },
    ).save("custom", snapshot)


def _credential(home, value: str) -> None:
    build_credential_service(home).put(
        CredentialRef("custom", "primary"), CredentialSecret({"api_key": value})
    )


def _envelope(session_id: str, *, profile: str | None = None) -> InteractionEnvelope:
    return InteractionEnvelope(
        conversation_id=session_id,
        source="test",
        content="hello",
        profile=profile,
    )


def _mocked_gateway_factory(
    requests,
    clients,
    gateways,
    *,
    discoveries=None,
    discovered_models: tuple[str, ...] | None = (),
):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert discoveries is not None, "discovery was not expected"
            discoveries.append(str(request.url))
            if discovered_models is None:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={"data": [{"id": model, "name": model} for model in discovered_models]},
            )
        requests.append(
            {
                "url": str(request.url),
                "authorization": request.headers["authorization"],
                "title": request.headers["x-title"],
                "model": json.loads(request.content)["model"],
            }
        )
        body = (
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    def build(home):
        gateway = build_real_gateway(
            home,
            client_factory=lambda: (
                clients.append(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
                or clients[-1]
            ),
        )
        gateways.append(gateway)
        return gateway

    return build


@pytest.fixture
def composed_home(tmp_path, monkeypatch):
    passphrase = tmp_path / "vault-passphrase"
    passphrase.write_text("reload-test-passphrase\n", encoding="utf-8")
    passphrase.chmod(0o600)
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    return tmp_path


@pytest.mark.anyio
async def test_next_turn_reloads_origin_catalog_credential_and_global_selection(
    composed_home, monkeypatch
):
    old_key = "old-key"
    new_key = "new-key"
    _config(
        composed_home,
        origin="https://old.example",
        model="old-model",
        title="Old title",
    )
    _catalog(composed_home, "old-model")
    _credential(composed_home, old_key)
    requests = []
    clients = []
    gateways = []
    monkeypatch.setattr(
        composition,
        "build_provider_gateway",
        _mocked_gateway_factory(requests, clients, gateways),
    )
    service = build_interaction_service(composed_home)
    first = service.stream(_envelope("first"))
    try:
        first_start = await anext(first)
        assert first_start.snapshot is not None
        assert first_start.snapshot.ref == ProviderModelRef("custom", "old-model")

        _config(
            composed_home,
            origin="https://new.example",
            model="new-model",
            title="New title",
        )
        _catalog(composed_home, "new-model")
        _credential(composed_home, new_key)

        first_tail = [event async for event in first]
        assert first_tail[-1].kind == "turn_end"

        second_events = [event async for event in service.stream(_envelope("second"))]
        assert second_events[0].snapshot is not None
        assert second_events[0].snapshot.ref == ProviderModelRef("custom", "new-model")
        assert second_events[-1].kind == "turn_end"

        assert requests == [
            {
                "url": "https://old.example/v1/chat/completions",
                "authorization": f"Bearer {old_key}",
                "title": "Old title",
                "model": "old-model",
            },
            {
                "url": "https://new.example/v1/chat/completions",
                "authorization": f"Bearer {new_key}",
                "title": "New title",
                "model": "new-model",
            },
        ]
        assert len(gateways) == 3  # lifespan-compatible base plus one per turn
        assert len(clients) == 2
        assert all(client.is_closed for client in clients)
    finally:
        await first.aclose()
        await service.aclose()


@pytest.mark.anyio
async def test_next_turn_reloads_profile_and_cancelled_turn_closes_its_gateway(
    composed_home, monkeypatch
):
    api_key = "profile-key"
    _config(
        composed_home,
        origin="https://profiles.example",
        model="global-old",
        profile_model="profile-old",
        title="Profiles",
    )
    _catalog(composed_home, "global-old", "profile-old")
    _credential(composed_home, api_key)
    requests = []
    clients = []
    gateways = []
    monkeypatch.setattr(
        composition,
        "build_provider_gateway",
        _mocked_gateway_factory(requests, clients, gateways),
    )
    service = build_interaction_service(composed_home)
    cancelled = service.stream(_envelope("cancelled", profile="research"))
    try:
        start = await anext(cancelled)
        assert start.snapshot is not None
        assert start.snapshot.ref == ProviderModelRef("custom", "profile-old")
        assert (await anext(cancelled)).kind == "delta"
        await cancelled.aclose()
        assert len(clients) == 1
        assert clients[0].is_closed

        _config(
            composed_home,
            origin="https://profiles.example",
            model="global-new",
            profile_model="profile-new",
            title="Profiles",
        )
        _catalog(composed_home, "global-new", "profile-new")

        global_events = [event async for event in service.stream(_envelope("global"))]
        profile_events = [
            event async for event in service.stream(_envelope("profile", profile="research"))
        ]
        assert global_events[0].snapshot is not None
        assert global_events[0].snapshot.ref == ProviderModelRef("custom", "global-new")
        assert profile_events[0].snapshot is not None
        assert profile_events[0].snapshot.ref == ProviderModelRef("custom", "profile-new")
        assert len(clients) == 3
        assert all(client.is_closed for client in clients)
    finally:
        await cancelled.aclose()
        await service.aclose()


@pytest.mark.anyio
async def test_expired_selected_model_is_discovered_before_generation(composed_home, monkeypatch):
    _config(
        composed_home,
        origin="https://aged.example",
        model="same-model",
        title="Aged catalog",
    )
    _catalog(composed_home, "same-model", expired=True)
    _credential(composed_home, "aged-key")
    requests = []
    discoveries = []
    clients = []
    gateways = []
    monkeypatch.setattr(
        composition,
        "build_provider_gateway",
        _mocked_gateway_factory(
            requests,
            clients,
            gateways,
            discoveries=discoveries,
            discovered_models=("same-model",),
        ),
    )
    service = build_interaction_service(composed_home)
    try:
        events = [event async for event in service.stream(_envelope("aged"))]

        assert events[0].snapshot is not None
        assert events[0].snapshot.ref == ProviderModelRef("custom", "same-model")
        assert events[-1].kind == "turn_end"
        assert discoveries == ["https://aged.example/models"]
        assert [request["model"] for request in requests] == ["same-model"]
        assert all(client.is_closed for client in clients)
    finally:
        await service.aclose()


@pytest.mark.anyio
async def test_failed_refresh_does_not_generate_with_lower_precedence_model(
    composed_home, monkeypatch
):
    _config(
        composed_home,
        origin="https://failed.example",
        model="gpt-4o",
        profile_model="missing-custom",
        title="Failed discovery",
    )
    document = yaml.safe_load((composed_home / "config.yaml").read_text(encoding="utf-8"))
    document.update(provider="openai", model="gpt-4o")
    (composed_home / "config.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    _catalog(composed_home, "missing-custom", expired=True)
    _credential(composed_home, "failed-key")
    requests = []
    discoveries = []
    clients = []
    gateways = []
    monkeypatch.setattr(
        composition,
        "build_provider_gateway",
        _mocked_gateway_factory(
            requests,
            clients,
            gateways,
            discoveries=discoveries,
            discovered_models=None,
        ),
    )
    service = build_interaction_service(composed_home)
    try:
        with pytest.raises(ModelSelectionUnavailableError, match="missing-custom"):
            await anext(service.stream(_envelope("failed", profile="research")))

        assert discoveries == ["https://failed.example/models"]
        assert requests == []
        assert all(client.is_closed for client in clients)
    finally:
        await service.aclose()


@pytest.mark.anyio
async def test_missing_selection_fails_without_discovery(composed_home, monkeypatch):
    requests = []
    discoveries = []
    clients = []
    gateways = []
    monkeypatch.setattr(
        composition,
        "build_provider_gateway",
        _mocked_gateway_factory(
            requests,
            clients,
            gateways,
            discoveries=discoveries,
            discovered_models=("irrelevant",),
        ),
    )
    service = build_interaction_service(composed_home)
    try:
        with pytest.raises(ModelSelectionUnavailableError, match="nenhuma seleção"):
            await anext(service.stream(_envelope("unconfigured")))

        assert discoveries == []
        assert requests == []
        assert clients == []
    finally:
        await service.aclose()
