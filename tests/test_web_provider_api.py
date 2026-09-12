"""Contratos públicos das APIs canônicas de providers e modelos."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from kairos_providers.contracts import ProviderModelRef, SelectionReason
from kairos_security.credentials import CredentialRef, CredentialSecret, build_credential_service
from kairos_state import connect, default_db_path, initialize_schema
from kairos_state.repositories.sessions import SessionRepository
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


class WebProviderApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._environment = {
            name: os.environ.get(name)
            for name in (
                "KAIROS_HOME",
                "KAIROS_VAULT_PASSPHRASE_FILE",
                "KAIROS_DISABLE_KEYRING",
            )
        }
        home = Path(self._tmp.name)
        passphrase = home / "vault-passphrase"
        passphrase.write_text("senha-mestra-de-teste\n", encoding="utf-8")
        passphrase.chmod(0o600)
        os.environ.update(
            {
                "KAIROS_HOME": str(home),
                "KAIROS_VAULT_PASSPHRASE_FILE": str(passphrase),
                "KAIROS_DISABLE_KEYRING": "1",
            }
        )
        self.client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def tearDown(self) -> None:
        for name, value in self._environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmp.cleanup()

    def test_models_expoe_campos_canonicos_e_filtra_openrouter_gratuito(self) -> None:
        response = self.client.get(
            "/api/models", params={"provider": "openrouter", "free_only": "true"}
        )

        self.assertEqual(response.status_code, 200)
        models = response.json()["models"]
        self.assertGreater(len(models), 0)
        self.assertTrue(all(model["provider"] == "openrouter" for model in models))
        self.assertTrue(all(model["is_free"] for model in models))
        self.assertGreaterEqual(
            set(models[0]),
            {"id", "provider", "is_free", "pricing", "capabilities", "origins"},
        )

    def test_providers_nunca_expoem_identificador_mascarado_ou_segredo(self) -> None:
        secret = "sk-test-nao-pode-aparecer"  # noqa: S105 - fixture de redação
        build_credential_service(Path(self._tmp.name)).put(
            CredentialRef("openai", "primary"), CredentialSecret({"api_key": secret})
        )
        response = self.client.get("/api/providers")

        self.assertEqual(response.status_code, 200)
        payload = response.text
        self.assertNotIn("masked_key", payload)
        self.assertNotIn("masked_identifier", payload)
        self.assertNotIn(secret, payload)

    def test_selecao_global_persiste_provider_e_modelo_canonicos(self) -> None:
        response = self.client.post(
            "/api/models/selection",
            json={
                "provider": "openai",
                "model": "gpt-5.6-terra",
                "scope": "global",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["selection"],
            {"provider": "openai", "model": "gpt-5.6-terra", "scope": "global"},
        )
        config = self.client.get("/api/config").json()
        self.assertEqual((config["provider"], config["model"]), ("openai", "gpt-5.6-terra"))

    def test_selecao_rejeita_modelo_que_nao_pertence_ao_provider(self) -> None:
        response = self.client.post(
            "/api/models/selection",
            json={"provider": "openai", "model": "inexistente", "scope": "global"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "modelo indisponível para o provider")

    def test_detalhe_da_sessao_expoe_somente_a_selecao_persistida_segura(self) -> None:
        conn = connect(default_db_path())
        initialize_schema(conn)
        sessions = SessionRepository(conn)
        sessions.create("sessao-com-override", source="web")
        sessions.set_selection(
            "sessao-com-override",
            ProviderModelRef("openrouter", "openrouter/free"),
            {
                "temperature": 0.2,
                "max_tokens": 512,
                "api_key": "segredo-nao-expor",
                "routing": {"credential_id": "identificador-nao-expor"},
            },
            reason=SelectionReason.CONVERSATION_OVERRIDE,
        )
        conn.close()

        response = self.client.get("/api/sessions/sessao-com-override")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["selection"],
            {
                "provider": "openrouter",
                "model": "openrouter/free",
                "parameters": {"temperature": 0.2, "max_tokens": 512},
                "reason": "conversation_override",
            },
        )
        self.assertNotIn("segredo-nao-expor", response.text)
        self.assertNotIn("identificador-nao-expor", response.text)

    def test_salvar_credencial_canonica_retorna_somente_metadados_nao_sensiveis(self) -> None:
        secret = "sk-canonica-nao-retornar"  # noqa: S105 - fixture de redação
        response = self.client.post(
            "/api/providers/openai/credentials",
            json={"secret": secret, "auth_method": "api_key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "provider": "openai",
                "credential_id": "primary",
                "auth_method": "api_key",
                "state": "configured",
            },
        )
        self.assertNotIn(secret, response.text)

    def test_provider_sem_credencial_falha_teste_sem_chamada_externa(self) -> None:
        response = self.client.post("/api/providers/anthropic/test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "unavailable")
        self.assertFalse(response.json()["connected"])


if __name__ == "__main__":
    unittest.main()
