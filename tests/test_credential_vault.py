"""Contratos e backends seguros do cofre de credenciais."""

from __future__ import annotations

import unittest

from kairos_security.credentials import (
    CredentialMetadata,
    CredentialRef,
    CredentialSecret,
)


class CredentialContractTests(unittest.TestCase):
    def test_metadata_mascara_identificador_sem_expor_no_repr(self):
        metadata = CredentialMetadata(
            ref=CredentialRef("openai", "primary"),
            auth_method="api_key",
            origin="vault",
            identifier="sk-producao-123456",
        )

        self.assertEqual(metadata.masked_identifier, "sk-p…56")
        self.assertNotIn("sk-producao-123456", repr(metadata))

    def test_identificador_curto_e_totalmente_ocultado(self):
        metadata = CredentialMetadata(
            ref=CredentialRef("openai", "primary"),
            auth_method="api_key",
            origin="vault",
            identifier="short",
        )

        self.assertEqual(metadata.masked_identifier, "••••")

    def test_secret_nao_expoe_valor_no_repr(self):
        secret = CredentialSecret({"api_key": "sk-nao-vazar"})

        self.assertNotIn("sk-nao-vazar", repr(secret))
        self.assertEqual(secret.reveal()["api_key"], "sk-nao-vazar")

    def test_reveal_devolve_copia_independente(self):
        secret = CredentialSecret({"api_key": "sk-original"})
        revealed = secret.reveal()
        revealed["api_key"] = "alterada"

        self.assertEqual(secret.reveal()["api_key"], "sk-original")

    def test_referencia_exige_provider_e_id(self):
        with self.assertRaisesRegex(ValueError, "provider"):
            CredentialRef("", "primary")
        with self.assertRaisesRegex(ValueError, "credential_id"):
            CredentialRef("openai", "")


if __name__ == "__main__":
    unittest.main()
