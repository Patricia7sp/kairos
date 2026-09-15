"""Messaging API — authenticated endpoints for config, credentials, test and send."""

import pytest
from fastapi.testclient import TestClient

from kairos_gateway.service import SendResult
from kairos_security.credentials.encrypted import EncryptedFileVault
from kairos_security.credentials.keyring_backend import SystemKeyringVault
from kairos_security.credentials.service import CredentialService
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app

TEST_FILE = "senha-de-teste"


class _FakeAdapter:
    name = "telegram"

    def verify(self):
        return {"ok": True, "message": "conectado (fake)"}

    def send(self, target, payload):
        return SendResult(ok=True)


class _DisabledKeyring:
    priority = 0.0

    def set_password(self, service, username, password):
        raise RuntimeError("keyring desabilitado")

    def get_password(self, service, username):
        return None

    def delete_password(self, service, username):
        raise RuntimeError("keyring desabilitado")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(app.state, "kairos_home", tmp_path, raising=False)
    vault_path = tmp_path / "credentials.vault"
    encrypted = EncryptedFileVault(vault_path, scrypt_n=2**10)
    keyring = SystemKeyringVault(_DisabledKeyring(), index_path=tmp_path / "index.json")
    service = CredentialService(keyring=keyring, encrypted=encrypted)
    service.initialize(TEST_FILE)
    monkeypatch.setattr(
        "kairos_gateway.adapters.config.build_credential_service", lambda _home: service
    )
    monkeypatch.setattr(
        "kairos_web.messaging_api.build_platform_adapters",
        lambda _home: {"telegram": _FakeAdapter()},
        raising=False,
    )
    return tmp_path


def _client():
    return TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})


def test_messaging_status_returns_four_platforms(home):
    resp = _client().get("/api/messaging")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["platforms"]) == 4
    assert body["platforms"][0]["platform"] == "telegram"
    assert "vault" in body["platforms"][0]


def test_put_config_updates_file(home):
    resp = _client().put(
        "/api/messaging/telegram",
        json={"config": {"enabled": True, "chat_id_default": "42"}},
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["enabled"] is True
    import json

    doc = json.loads((home / "messaging.json").read_text(encoding="utf-8"))
    assert doc["telegram"]["chat_id_default"] == "42"
    assert resp.json()["config"]["chat_id_default"] == "42"


def test_put_config_unknown_platform_returns_404(home):
    resp = _client().put("/api/messaging/inventado", json={"config": {}})
    assert resp.status_code == 404


def test_put_config_invalid_shape_returns_422(home):
    resp = _client().put(
        "/api/messaging/telegram",
        json={"config": {"enabled": "não"}, "extra": True},
    )
    assert resp.status_code == 422


def test_credential_save_and_delete(home):
    r = _client().post(
        "/api/messaging/telegram/credential",
        json={"secret": "tok123:ABC"},
    )
    assert r.status_code == 200
    assert r.json()["saved"] is True
    status = _client().get("/api/messaging").json()
    telegram = next(p for p in status["platforms"] if p["platform"] == "telegram")
    assert telegram["configured"] is True
    d = _client().delete("/api/messaging/telegram/credential")
    assert d.status_code == 200
    assert d.json()["removed"] is True
    telegram2 = next(
        p
        for p in _client().get("/api/messaging").json()["platforms"]
        if p["platform"] == "telegram"
    )
    assert telegram2["configured"] is False


def test_credential_empty_rejected(home):
    resp = _client().post(
        "/api/messaging/telegram/credential",
        json={"secret": "   "},
    )
    assert resp.status_code == 422


def test_unknown_credential_platform_returns_422(home):
    resp = _client().post(
        "/api/messaging/inventado/credential",
        json={"secret": "x"},
    )
    assert resp.status_code == 404


def test_test_platform_calls_adapter(home):
    resp = _client().post(
        "/api/messaging/telegram/test",
        json={"target": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["sent"] is False


def test_test_with_target_sends(home):
    resp = _client().post(
        "/api/messaging/telegram/test",
        json={"target": "telegram:42"},
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] is True


def test_send_delivers_via_fake_adapter(home):
    resp = _client().post(
        "/api/messaging/send",
        json={"target": "telegram:42", "text": "olá"},
    )
    assert resp.status_code == 200
    assert resp.json()["delivered"] is True
    assert resp.json()["obligation_id"]


def test_send_rejects_bad_target(home):
    resp = _client().post(
        "/api/messaging/send",
        json={"target": "telegram", "text": "x"},
    )
    assert resp.status_code == 422


def test_send_rejects_unknown_platform(home):
    resp = _client().post(
        "/api/messaging/send",
        json={"target": "inventado:42", "text": "x"},
    )
    assert resp.status_code == 400


def test_auth_rejected(home):
    assert TestClient(app).get("/api/messaging").status_code == 401
    assert (
        TestClient(app)
        .post("/api/messaging/send", json={"target": "telegram:1", "text": "x"})
        .status_code
        == 401
    )
