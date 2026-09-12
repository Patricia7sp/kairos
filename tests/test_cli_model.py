"""Model commands share the application's catalog and persisted selection."""

import json

import httpx
import pytest
import yaml

from kairos_cli.main import main


@pytest.fixture
def model_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    return tmp_path


def test_show_does_not_invent_default_or_create_config(model_home, capsys):
    assert main(["model", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"provider": None, "model": None}
    assert not (model_home / "config.yaml").exists()


def test_set_persists_known_selection_without_replacing_other_configuration(model_home, capsys):
    config = model_home / "config.yaml"
    config.write_text(
        yaml.safe_dump({"parameters": {"temperature": 0.2}, "agent_runtime": {"enabled": False}})
    )
    assert main(["model", "set", "openai", "gpt-4o", "--json"]) == 0
    saved = yaml.safe_load(config.read_text())
    assert saved == {
        "provider": "openai",
        "model": "gpt-4o",
        "parameters": {"temperature": 0.2},
        "agent_runtime": {"enabled": False},
    }
    assert json.loads(capsys.readouterr().out)["model"] == "gpt-4o"
    before = config.read_bytes()
    assert main(["model", "set", "openai", "nonexistent", "--json"]) == 1
    assert config.read_bytes() == before


def test_refresh_then_list_reads_persisted_discovery_in_new_gateway(
    model_home, monkeypatch, capsys
):
    from kairos_providers.composition import _ProviderHttpClients
    from kairos_security.credentials import (
        CredentialRef,
        CredentialSecret,
        build_credential_service,
    )

    passphrase = model_home / "passphrase"
    passphrase.write_text("fixture-passphrase")
    passphrase.chmod(0o600)
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    build_credential_service(model_home).put(
        CredentialRef("openrouter", "primary"), CredentialSecret({"api_key": "synthetic-key"})
    )
    available = True

    def upstream(request):
        if not available:
            return httpx.Response(500)
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {}})
        assert request.url.path == "/api/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "fixture/chat",
                        "name": "Fixture",
                        "architecture": {
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                        },
                        "pricing": {"prompt": "0", "completion": "0"},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        _ProviderHttpClients,
        "_new_http_client",
        staticmethod(lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream))),
    )
    assert main(["model", "refresh", "openrouter", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["models"] == 1
    assert main(["model", "list", "--provider", "openrouter", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["models"]
    assert "fixture/chat" in [m["model"] for m in listed]
    assert {m["provider"] for m in listed} == {"openrouter"}
    assert main(["model", "test", "openrouter", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    available = False
    assert main(["model", "refresh", "openrouter", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["source"] == "cache"


def test_set_rejects_non_chat_model_without_mutating_default(model_home, capsys):
    from time import time

    from kairos_providers import CatalogModel, ModelCapabilities, ProviderModelRef
    from kairos_providers.catalog import CatalogSnapshot
    from kairos_providers.catalog_store import CatalogSnapshotStore

    CatalogSnapshotStore(model_home / "model-catalog.json").save(
        "openrouter",
        CatalogSnapshot(
            (
                CatalogModel(
                    ref=ProviderModelRef("openrouter", "fixture/embedding"),
                    display_name="Embedding",
                    capabilities=ModelCapabilities(chat=False),
                ),
            ),
            time(),
            time() + 3600,
        ),
    )
    config = model_home / "config.yaml"
    config.write_text("provider: openai\nmodel: gpt-4o\n")
    assert main(["model", "set", "openrouter", "fixture/embedding", "--json"]) == 1
    assert config.read_text() == "provider: openai\nmodel: gpt-4o\n"
