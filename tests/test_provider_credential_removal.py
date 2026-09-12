"""Credential removal changes the vault and preserves the surrounding profile."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kairos_security.credentials import (
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    ExternalCredentialSource,
    build_credential_service,
)
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    passphrase = tmp_path / "passphrase"
    passphrase.write_text("test-passphrase")
    passphrase.chmod(0o600)
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    vault = build_credential_service(tmp_path)
    for provider, credential_id in (
        ("openai", "primary"),
        ("openai", "other"),
        ("groq", "primary"),
    ):
        vault.put(
            CredentialRef(provider, credential_id), CredentialSecret({"api_key": "private-value"})
        )
    document = {
        "revision": 17,
        "credential_pool": {
            "openai": [
                {"credential_id": "primary", "auth_method": "api_key"},
                {"credential_id": "other", "auth_method": "api_key"},
            ],
            "groq": [{"credential_id": "primary", "auth_method": "api_key"}],
        },
    }
    (tmp_path / "auth.json").write_text(json.dumps(document))
    (tmp_path / "config.yaml").write_text("provider: openai\nmodel: retained\n")
    return TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN}), tmp_path, document


def test_removal_deletes_only_primary_and_reloads_real_state(credentials):
    client, home, before = credentials
    response = client.delete("/api/providers/openai/credentials")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert "private-value" not in response.text
    vault = build_credential_service(home)
    with pytest.raises(CredentialNotFoundError):
        vault.get(CredentialRef("openai", "primary"))
    assert vault.get(CredentialRef("openai", "other"))
    assert vault.get(CredentialRef("groq", "primary"))
    before["credential_pool"]["openai"].pop(0)
    assert json.loads((home / "auth.json").read_text()) == before
    assert (home / "config.yaml").read_text() == "provider: openai\nmodel: retained\n"
    provider = next(
        p for p in client.get("/api/providers").json()["providers"] if p["id"] == "openai"
    )
    assert provider["configured"] is True  # The other credential still exists.
    assert provider["can_remove_credential"] is False
    assert client.delete("/api/providers/openai/credentials").status_code == 404


def test_removal_is_authenticated_and_unknown_provider_is_rejected(credentials):
    client, home, before = credentials
    assert TestClient(app).delete("/api/providers/openai/credentials").status_code == 401
    assert client.delete("/api/providers/absent/credentials").status_code == 404
    assert json.loads((home / "auth.json").read_text()) == before


def test_removal_rolls_back_vault_when_metadata_write_fails(credentials):
    client, home, before = credentials
    with patch(
        "kairos_web.provider_credentials_api.secure_atomic_write_text", side_effect=OSError("disk")
    ):
        response = client.delete("/api/providers/openai/credentials")
    assert response.status_code == 503
    assert build_credential_service(home).get(CredentialRef("openai", "primary"))
    assert json.loads((home / "auth.json").read_text()) == before


def test_external_credential_is_read_only(credentials):
    client, home, before = credentials
    source = ExternalCredentialSource(
        {CredentialRef("openai", "primary"): CredentialSecret({"api_key": "external-secret"})}
    )
    with patch("kairos_web.provider_credentials_api.build_credential_service", return_value=source):
        response = client.delete("/api/providers/openai/credentials")
    assert response.status_code == 409
    assert "externa" in response.json()["detail"]
    assert "external-secret" not in response.text
    assert json.loads((home / "auth.json").read_text()) == before


def test_locked_vault_cannot_be_reported_as_removed(credentials, monkeypatch):
    client, home, before = credentials
    monkeypatch.delenv("KAIROS_VAULT_PASSPHRASE_FILE")
    response = client.delete("/api/providers/openai/credentials")
    assert response.status_code == 503
    assert json.loads((home / "auth.json").read_text()) == before


def test_replacement_preserves_other_credentials_and_document_fields(credentials):
    client, home, before = credentials
    response = client.post(
        "/api/providers/openai/credentials", json={"secret": "replacement-private"}
    )
    assert response.status_code == 200
    assert json.loads((home / "auth.json").read_text()) == before
    assert "replacement-private" not in (home / "auth.json").read_text()


def test_simultaneous_delete_and_save_share_the_same_transaction_lock(credentials):
    from kairos_security.credentials.service import CredentialService

    client, home, before = credentials
    deleting = threading.Event()
    release = threading.Event()
    saving = threading.Event()
    original_delete = CredentialService.delete

    def delayed_delete(service, ref):
        deleting.set()
        assert release.wait(5)
        return original_delete(service, ref)

    def save():
        saving.set()
        return client.post("/api/providers/openai/credentials", json={"secret": "new-private"})

    with patch.object(CredentialService, "delete", delayed_delete), ThreadPoolExecutor(2) as pool:
        deletion = pool.submit(client.delete, "/api/providers/openai/credentials")
        assert deleting.wait(5)
        replacement = pool.submit(save)
        try:
            assert saving.wait(5)
            assert not replacement.done()
        finally:
            release.set()
        assert deletion.result(timeout=5).status_code == 200
        assert replacement.result(timeout=5).status_code == 200
    assert json.loads((home / "auth.json").read_text()) == before
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "new-private"
    }


def test_external_listing_reports_actual_source_without_exposing_secret(credentials):
    client, _, _ = credentials
    source = ExternalCredentialSource(
        {CredentialRef("openai", "primary"): CredentialSecret({"api_key": "external-secret"})}
    )
    with patch("kairos_providers.composition.build_credential_service", return_value=source):
        response = client.get("/api/providers")
    provider = next(p for p in response.json()["providers"] if p["id"] == "openai")
    assert provider["credential_source"] == "external"
    assert provider["can_remove_credential"] is False
    assert "external-secret" not in response.text


def test_invalid_auth_document_does_not_delete_vault_secret(credentials):
    client, home, _ = credentials
    (home / "auth.json").write_text("[invalid")
    response = client.delete("/api/providers/openai/credentials")
    assert response.status_code == 503
    assert build_credential_service(home).get(CredentialRef("openai", "primary"))
    assert (home / "auth.json").read_text() == "[invalid"


def test_save_and_remove_honor_the_same_application_profile(credentials, monkeypatch):
    client, home, before = credentials
    profile = home / "another-profile"
    monkeypatch.setattr(app.state, "kairos_home", profile, raising=False)
    assert (
        client.post(
            "/api/providers/openai/credentials", json={"secret": "profile-private"}
        ).status_code
        == 200
    )
    assert build_credential_service(profile).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "profile-private"
    }
    assert client.delete("/api/providers/openai/credentials").status_code == 200
    assert json.loads((home / "auth.json").read_text()) == before
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "private-value"
    }


@pytest.mark.parametrize("entries", [["invalid-entry"], {"primary": "invalid-entry"}])
def test_malformed_metadata_rejects_replacement_before_mutating_vault(credentials, entries):
    client, home, before = credentials
    before["credential_pool"]["openai"] = entries
    original = json.dumps(before)
    (home / "auth.json").write_text(original)
    response = client.post("/api/providers/openai/credentials", json={"secret": "replacement"})
    assert response.status_code == 503
    assert "replacement" not in response.text
    assert (
        build_credential_service(home).get(CredentialRef("openai", "primary")).reveal()["api_key"]
        == "private-value"
    )
    assert (home / "auth.json").read_text() == original
