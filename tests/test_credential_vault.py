"""Contratos e backends seguros do cofre de credenciais."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kairos_security.credentials import (
    CredentialMetadata,
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    EncryptedFileVault,
    InvalidMasterPasswordError,
    VaultLockedError,
    VaultState,
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


class EncryptedFileVaultTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "credentials.vault"
        self.vault = EncryptedFileVault(self.path, scrypt_n=2**10)

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_criptografado_nao_contem_plaintext(self):
        self.vault.initialize("senha-mestra-forte")
        ref = CredentialRef("openai", "primary")

        metadata = self.vault.put(
            ref,
            CredentialSecret({"api_key": "sk-nao-vazar"}),
            auth_method="api_key",
        )

        self.assertEqual(self.vault.get(ref).reveal()["api_key"], "sk-nao-vazar")
        self.assertNotIn(b"sk-nao-vazar", self.path.read_bytes())
        self.assertEqual(metadata.ref, ref)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_restart_volta_bloqueado(self):
        self.vault.initialize("senha-mestra-forte")
        fresh = EncryptedFileVault(self.path, scrypt_n=2**10)

        self.assertEqual(fresh.state, VaultState.LOCKED)
        with self.assertRaises(VaultLockedError):
            fresh.list("openai")

    def test_senha_incorreta_nao_altera_o_arquivo(self):
        self.vault.initialize("correta")
        before = self.path.read_bytes()

        with self.assertRaises(InvalidMasterPasswordError):
            EncryptedFileVault(self.path, scrypt_n=2**10).unlock("errada")

        self.assertEqual(self.path.read_bytes(), before)

    def test_lock_apaga_acesso_ate_novo_unlock(self):
        self.vault.initialize("senha-mestra-forte")
        ref = CredentialRef("openai", "primary")
        self.vault.put(ref, CredentialSecret({"api_key": "sk-x"}))

        self.vault.lock()

        self.assertEqual(self.vault.state, VaultState.LOCKED)
        with self.assertRaises(VaultLockedError):
            self.vault.get(ref)
        self.vault.unlock("senha-mestra-forte")
        self.assertEqual(self.vault.get(ref).reveal(), {"api_key": "sk-x"})

    def test_list_e_delete_operam_por_referencia(self):
        self.vault.initialize("senha-mestra-forte")
        openai = CredentialRef("openai", "primary")
        anthropic = CredentialRef("anthropic", "primary")
        self.vault.put(openai, CredentialSecret({"api_key": "sk-openai"}))
        self.vault.put(anthropic, CredentialSecret({"api_key": "sk-anthropic"}))

        self.assertEqual([item.ref for item in self.vault.list("openai")], [openai])
        self.vault.delete(openai)

        with self.assertRaises(CredentialNotFoundError):
            self.vault.get(openai)


if __name__ == "__main__":
    unittest.main()
