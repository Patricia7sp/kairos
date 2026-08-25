"""Migração segura e explícita das credenciais legadas em auth.json."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kairos_security.credentials import (
    CredentialRef,
    CredentialSecret,
    EncryptedFileVault,
    LegacyCredentialMigration,
    MigrationConfirmationRequired,
    MigrationVerificationError,
)


class CorruptingVault:
    def __init__(self, delegate: EncryptedFileVault) -> None:
        self._delegate = delegate

    @property
    def state(self):
        return self._delegate.state

    def put(self, ref, secret, *, auth_method="api_key"):
        return self._delegate.put(
            ref, CredentialSecret({"api_key": "valor-corrompido"}), auth_method=auth_method
        )

    def get(self, ref):
        return self._delegate.get(ref)

    def list(self, provider=None):
        return self._delegate.list(provider)

    def delete(self, ref):
        return self._delegate.delete(ref)


class LegacyCredentialMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.auth_path = self.root / "auth.json"
        self.auth_path.write_text(
            json.dumps(
                {
                    "credential_pool": {
                        "openai": [{"api_key": "sk-legado", "label": "principal"}],
                        "anthropic": [{"token": "ant-legado"}],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.vault = EncryptedFileVault(self.root / "credentials.vault", scrypt_n=2**10)
        self.vault.initialize("senha-mestra")
        self.migration = LegacyCredentialMigration(self.auth_path, self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_nao_muda_arquivo_nem_cofre(self):
        before = self.auth_path.read_bytes()

        report = self.migration.scan()

        self.assertEqual(report.credentials_found, 2)
        self.assertEqual(self.auth_path.read_bytes(), before)
        self.assertEqual(self.vault.list(), [])

    def test_importa_verifica_e_so_remove_com_confirmacao(self):
        report = self.migration.import_and_verify()

        self.assertEqual(report.credentials_verified, 2)
        self.assertIn("sk-legado", self.auth_path.read_text(encoding="utf-8"))
        with self.assertRaises(MigrationConfirmationRequired):
            self.migration.finalize(confirm=False)

        final = self.migration.finalize(confirm=True)

        self.assertEqual(final.credentials_finalized, 2)
        text = self.auth_path.read_text(encoding="utf-8")
        self.assertNotIn("sk-legado", text)
        entry = json.loads(text)["credential_pool"]["openai"][0]
        self.assertEqual(entry["credential_id"], "legacy-1")
        self.assertEqual(entry["auth_method"], "api_key")
        self.assertNotIn("api_key", entry)

    def test_falha_de_verificacao_preserva_plaintext(self):
        migration = LegacyCredentialMigration(self.auth_path, CorruptingVault(self.vault))

        with self.assertRaises(MigrationVerificationError):
            migration.import_and_verify()

        self.assertIn("sk-legado", self.auth_path.read_text(encoding="utf-8"))

    def test_reexecucao_ignora_referencias_ja_migradas(self):
        self.migration.import_and_verify()
        self.migration.finalize(confirm=True)

        report = self.migration.scan()

        self.assertEqual(report.credentials_found, 0)
        self.assertEqual(
            self.vault.get(CredentialRef("openai", "legacy-1")).reveal()["api_key"],
            "sk-legado",
        )


if __name__ == "__main__":
    unittest.main()
