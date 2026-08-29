"""Ausência dos caminhos de runtime substituídos pela pilha canônica."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient

from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


def test_rota_legacy_nao_existe() -> None:
    assert TestClient(app).get("/legacy/").status_code == 404


def test_adapters_legados_nao_sao_importaveis() -> None:
    assert importlib.util.find_spec("kairos_providers.adapters.openai_adapter") is None
    assert importlib.util.find_spec("kairos_providers.adapters.anthropic_adapter") is None
    assert importlib.util.find_spec("kairos_providers.adapters.gemini_adapter") is None
    assert importlib.util.find_spec("kairos_providers.adapters.ollama_adapter") is None


def test_alias_save_key_grava_no_cofre_sem_validacao_de_rede(tmp_path, monkeypatch) -> None:
    passphrase = tmp_path / "vault-passphrase"
    passphrase.write_text("senha-mestra-de-teste\n", encoding="utf-8")
    passphrase.chmod(0o600)
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    response = client.post(
        "/api/providers/save-key",
        json={"provider": "openai", "api_key": "sk-alias-sem-rede"},
    )

    assert response.status_code == 200
    assert response.json()["credential_status"] == "saved_unverified"
    assert "sk-alias-sem-rede" not in response.text
    assert os.environ["KAIROS_HOME"] == str(Path(tmp_path))
