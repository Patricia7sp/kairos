"""Settings must control canonical generation parameters without replacing config."""

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from kairos_web.settings_api import router

    app = FastAPI()
    app.state.kairos_home = tmp_path
    app.include_router(router)
    with TestClient(app) as instance:
        yield instance


def test_settings_patch_preserves_provider_runtime_and_unrelated_parameters(client, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "provider": "openrouter",
                "model": "test",
                "parameters": {"routing": {"data_collection": "deny"}, "max_tokens": 99},
                "agent_runtime": {"enabled": True},
                "private_key": "do-not-expose",
            }
        )
    )
    result = client.put("/api/settings", json={"generation": {"temperature": 0.7}})
    assert result.status_code == 200
    assert result.json()["generation"] == {"temperature": 0.7, "max_tokens": 99}
    config = yaml.safe_load(path.read_text())
    assert config["parameters"]["routing"]["data_collection"] == "deny"
    assert config["agent_runtime"]["enabled"] is True
    assert config["private_key"] == "do-not-expose"
    assert "do-not-expose" not in client.get("/api/settings").text
    assert client.put("/api/settings", json={"generation": {"max_tokens": None}}).status_code == 200
    assert "max_tokens" not in yaml.safe_load(path.read_text())["parameters"]


@pytest.mark.parametrize("value", [True, -1, "secret-value", [], {}])
def test_invalid_generation_does_not_overwrite_config_or_echo_input(client, tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text("provider: openrouter\n")
    result = client.put("/api/settings", json={"generation": {"max_tokens": value}})
    assert result.status_code == 422
    assert path.read_text() == "provider: openrouter\n"
    assert "secret-value" not in result.text


def test_settings_are_mounted_in_authenticated_application(tmp_path, monkeypatch):
    from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app

    monkeypatch.setattr(app.state, "kairos_home", tmp_path, raising=False)
    application = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    assert TestClient(app).put("/api/settings", json={"generation": {}}).status_code == 401
    saved = application.put("/api/settings", json={"generation": {"max_tokens": 128}})
    assert saved.status_code == 200
    assert application.get("/api/settings").json()["generation"] == {"max_tokens": 128}
