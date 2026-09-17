"""Webhooks de entrada WhatsApp: rotas públicas verificadas pelo próprio canal.

`GET /api/inbound/whatsapp` responde o ``hub.challenge`` só com Verify Token
correto; `POST` aceita ``EVENT_RECEIVED`` só com assinatura HMAC-SHA256 válida.
Segredos reais via cofre (monkeypatch do serviço de credenciais, como o
`test_messaging_api`), nenhuma rede.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kairos_gateway.service import SendResult
from kairos_security.credentials.encrypted import EncryptedFileVault
from kairos_security.credentials.keyring_backend import SystemKeyringVault
from kairos_security.credentials.service import CredentialService
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app

TEST_FILE = "senha-de-teste"
APP_SECRET = "app-secret-webhook"  # noqa: S105 - fixture sintética de teste
VERIFY_TOKEN = "verify-token-webhook"  # noqa: S105 - fixture sintética de teste
PHONE = "5511999888777"


class _FakeWhatsAppAdapter:
    name = "whatsapp"

    def send(self, target: str, payload: str) -> SendResult:
        return SendResult(ok=True)


class _FakeEvent:
    def __init__(self, kind: str, **kwargs: Any) -> None:
        self.kind = kind
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeRouter:
    def __init__(self) -> None:
        self.envelopes = []

    async def stream(self, envelope: Any) -> Any:
        self.envelopes.append(envelope)
        yield _FakeEvent("turn_end")


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
    monkeypatch.setattr(app.state, "whatsapp_inbound", None, raising=False)
    monkeypatch.setattr(app.state, "interaction_service", None, raising=False)
    vault_path = tmp_path / "credentials.vault"
    encrypted = EncryptedFileVault(vault_path, scrypt_n=2**10)
    keyring = SystemKeyringVault(_DisabledKeyring(), index_path=tmp_path / "index.json")
    service = CredentialService(keyring=keyring, encrypted=encrypted)
    service.initialize(TEST_FILE)
    monkeypatch.setattr(
        "kairos_gateway.adapters.config.build_credential_service", lambda _home: service
    )
    monkeypatch.setattr(
        "kairos_gateway.adapters.build_platform_adapters",
        lambda _home: {"whatsapp": _FakeWhatsAppAdapter()},
    )
    monkeypatch.setattr("kairos_integration.build_interaction_router", lambda _home: _FakeRouter())
    return tmp_path


def _client():
    return TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})


def _signature(raw: bytes) -> str:
    expected = hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return f"sha256={expected}"


def _payload() -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "111111111111111",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "phone_number_id": "22222222222222",
                                    "display_phone_number": "5511900001111",
                                },
                                "contacts": [{"profile": {"name": "Teste"}, "wa_id": f"+{PHONE}"}],
                                "messages": [
                                    {
                                        "from": f"+{PHONE}",
                                        "id": "wamid.webhook-1==",
                                        "timestamp": "1730000000",
                                        "type": "text",
                                        "text": {"body": "Estou online?"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


def _habilitar_inbound(home, *, enabled: bool = True) -> None:
    resp = _client().put(
        "/api/messaging/whatsapp",
        json={
            "config": {
                "enabled": True,
                "phone_number_id": "22222222222222",
                "number_default": "",
                "inbound": {
                    "enabled": enabled,
                    "allowed_phone_numbers": [f"+{PHONE}"],
                    "experiences": False,
                },
            }
        },
    )
    assert resp.status_code == 200


def test_handshake_feliz_devolve_challenge(home):
    _habilitar_inbound(home)
    r = _client().post(
        "/api/messaging/whatsapp/inbound-secret",
        json={"app_secret": APP_SECRET, "verify_token": VERIFY_TOKEN},
    )
    assert r.status_code == 200
    resp = _client().get(
        "/api/inbound/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "desafio-42",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "desafio-42"


def test_handshake_sem_config_recusa_fechado(home):
    resp = _client().get(
        "/api/inbound/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "d"},
    )
    assert resp.status_code == 503


def test_handshake_token_diverge_recusa(home):
    _habilitar_inbound(home)
    _client().post(
        "/api/messaging/whatsapp/inbound-secret",
        json={"app_secret": APP_SECRET, "verify_token": VERIFY_TOKEN},
    )
    resp = _client().get(
        "/api/inbound/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "outro", "hub.challenge": "d"},
    )
    assert resp.status_code == 401


def test_receive_sem_segredo_recusa(home):
    _habilitar_inbound(home)
    resp = _client().post(
        "/api/inbound/whatsapp",
        content=_payload(),
        headers={"X-Hub-Signature-256": _signature(_payload())},
    )
    assert resp.status_code == 503


def test_receive_assinatura_invalida_recusa(home):
    _habilitar_inbound(home)
    _client().post(
        "/api/messaging/whatsapp/inbound-secret",
        json={"app_secret": APP_SECRET, "verify_token": VERIFY_TOKEN},
    )
    resp = _client().post(
        "/api/inbound/whatsapp",
        content=_payload(),
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert resp.status_code == 401


def test_receive_valido_acusa_event_received(home):
    _habilitar_inbound(home)
    _client().post(
        "/api/messaging/whatsapp/inbound-secret",
        json={"app_secret": APP_SECRET, "verify_token": VERIFY_TOKEN},
    )
    corpo = _payload()
    resp = _client().post(
        "/api/inbound/whatsapp",
        content=corpo,
        headers={"X-Hub-Signature-256": _signature(corpo)},
    )
    assert resp.status_code == 200
    assert resp.text == "EVENT_RECEIVED"


def test_inbound_rota_aberta_sem_sessao(home):
    # O webhook da Meta não tem cookie de sessão: a rota responde sem sessão.
    _habilitar_inbound(home)
    _client().post(
        "/api/messaging/whatsapp/inbound-secret",
        json={"app_secret": APP_SECRET, "verify_token": VERIFY_TOKEN},
    )
    anon = TestClient(app)
    resp = anon.get(
        "/api/inbound/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "aberto",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "aberto"


def test_segredos_merge_preserva_credencial(home):
    _client().post("/api/messaging/whatsapp/credential", json={"secret": "tk-meta:ABC"})
    r = _client().post(
        "/api/messaging/whatsapp/inbound-secret",
        json={"app_secret": APP_SECRET, "verify_token": VERIFY_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["saved"] == ["app_secret", "verify_token"]
    status = _client().get("/api/messaging").json()
    whatsapp = next(p for p in status["platforms"] if p["platform"] == "whatsapp")
    assert whatsapp["configured"] is True
    assert whatsapp["inbound"]["campo_secreto"] == {"app_secret": True, "verify_token": True}
    d = _client().delete("/api/messaging/whatsapp/inbound-secret")
    assert d.status_code == 200
    status2 = _client().get("/api/messaging").json()
    whatsapp2 = next(p for p in status2["platforms"] if p["platform"] == "whatsapp")
    assert whatsapp2["inbound"]["campo_secreto"] == {"app_secret": False, "verify_token": False}
    assert whatsapp2["configured"] is True


def test_segredo_entrada_sem_campos_recusa(home):
    resp = _client().post("/api/messaging/whatsapp/inbound-secret", json={})
    assert resp.status_code == 422
