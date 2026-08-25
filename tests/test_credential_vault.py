"""Contratos e backends seguros do cofre de credenciais."""

from __future__ import annotations

import json
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kairos_security.credentials import (
    CredentialMetadata,
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    CredentialService,
    EncryptedFileVault,
    ExternalCredentialSource,
    InvalidMasterPasswordError,
    SystemKeyringVault,
    VaultError,
    VaultLockedError,
    VaultState,
    build_credential_service,
)


class MemoryKeyring:
    priority = 1

    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}
        self.fail = False

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("keyring indisponível")
        self.passwords[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail:
            raise RuntimeError("keyring indisponível")
        return self.passwords.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if self.fail:
            raise RuntimeError("keyring indisponível")
        del self.passwords[(service, username)]


class FailingKeyring(MemoryKeyring):
    priority = 0


class FailingIndexVault(SystemKeyringVault):
    fail_writes = False

    def _write_index(self, entries):
        if self.fail_writes:
            raise OSError("falha de índice")
        return super()._write_index(entries)


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

    def test_envelope_malformado_retorna_erro_estavel(self):
        self.path.write_text("não é json", encoding="utf-8")

        with self.assertRaises(InvalidMasterPasswordError):
            self.vault.unlock("senha")

    def test_parametros_kdf_nao_confiaveis_sao_rejeitados_antes_da_derivacao(self):
        self.vault.initialize("senha")
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        envelope["kdf"]["name"] = "algoritmo-injetado"
        self.path.write_text(json.dumps(envelope), encoding="utf-8")

        with self.assertRaises(InvalidMasterPasswordError):
            EncryptedFileVault(self.path, scrypt_n=2**10).unlock("senha")

    def test_duas_instancias_concorrentes_nao_perdem_atualizacao(self):
        self.vault.initialize("senha")
        other = EncryptedFileVault(self.path, scrypt_n=2**10)
        other.unlock("senha")
        barrier = threading.Barrier(2)

        def write(vault, provider):
            barrier.wait()
            vault.put(
                CredentialRef(provider, "primary"),
                CredentialSecret({"api_key": f"sk-{provider}"}),
            )

        threads = [
            threading.Thread(target=write, args=(self.vault, "openai")),
            threading.Thread(target=write, args=(other, "anthropic")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual({item.ref.provider for item in self.vault.list()}, {"openai", "anthropic"})


class KeyringVaultTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_keyring_disponivel_e_primeira_escolha(self):
        client = MemoryKeyring()
        encrypted = EncryptedFileVault(self.root / "credentials.vault", scrypt_n=2**10)
        service = CredentialService(
            keyring=SystemKeyringVault(client, index_path=self.root / "credential-index.json"),
            encrypted=encrypted,
        )
        ref = CredentialRef("openai", "primary")

        metadata = service.put(ref, CredentialSecret({"api_key": "sk-x"}))

        self.assertEqual(service.state, VaultState.KEYRING)
        self.assertEqual(service.get(ref).reveal(), {"api_key": "sk-x"})
        self.assertEqual(metadata.ref, ref)

    def test_indice_do_keyring_nao_contem_segredo(self):
        index_path = self.root / "credential-index.json"
        service = CredentialService(
            keyring=SystemKeyringVault(MemoryKeyring(), index_path=index_path),
            encrypted=EncryptedFileVault(self.root / "credentials.vault", scrypt_n=2**10),
        )

        service.put(
            CredentialRef("openai", "primary"),
            CredentialSecret({"api_key": "sk-nao-vazar"}),
        )

        content = index_path.read_text(encoding="utf-8")
        self.assertNotIn("sk-nao-vazar", content)
        self.assertEqual(json.loads(content)["entries"][0]["provider"], "openai")
        self.assertEqual(index_path.stat().st_mode & 0o777, 0o600)

    def test_keyring_indisponivel_cai_no_cofre_bloqueado(self):
        encrypted = EncryptedFileVault(self.root / "credentials.vault", scrypt_n=2**10)
        encrypted.initialize("senha-mestra")
        encrypted.lock()

        service = CredentialService(
            keyring=SystemKeyringVault(
                FailingKeyring(), index_path=self.root / "credential-index.json"
            ),
            encrypted=encrypted,
        )

        self.assertEqual(service.state, VaultState.LOCKED)

    def test_backend_nao_alterna_silenciosamente_depois_da_selecao(self):
        client = MemoryKeyring()
        encrypted = EncryptedFileVault(self.root / "credentials.vault", scrypt_n=2**10)
        encrypted.initialize("senha-mestra")
        service = CredentialService(
            keyring=SystemKeyringVault(client, index_path=self.root / "credential-index.json"),
            encrypted=encrypted,
        )
        client.fail = True

        with self.assertRaisesRegex(RuntimeError, "keyring indisponível"):
            service.put(
                CredentialRef("openai", "primary"),
                CredentialSecret({"api_key": "sk-x"}),
            )
        self.assertEqual(encrypted.list(), [])

    def test_fonte_externa_e_somente_leitura(self):
        ref = CredentialRef("anthropic", "environment")
        source = ExternalCredentialSource({ref: CredentialSecret({"api_key": "sk-external"})})

        self.assertEqual(source.state, VaultState.EXTERNAL)
        self.assertEqual(source.get(ref).reveal(), {"api_key": "sk-external"})
        self.assertEqual([item.ref for item in source.list("anthropic")], [ref])
        with self.assertRaisesRegex(VaultError, "somente leitura"):
            source.delete(ref)

    def test_falha_de_indice_ao_sobrescrever_restaura_segredo_anterior(self):
        client = MemoryKeyring()
        index_path = self.root / "credential-index.json"
        vault = FailingIndexVault(client, index_path=index_path)
        ref = CredentialRef("openai", "primary")
        vault.put(ref, CredentialSecret({"api_key": "sk-anterior"}))
        vault.fail_writes = True

        with self.assertRaises(OSError):
            vault.put(ref, CredentialSecret({"api_key": "sk-nova"}))

        self.assertEqual(vault.get(ref).reveal(), {"api_key": "sk-anterior"})


class CredentialServiceFactoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._environment = dict(os.environ)
        os.environ["KAIROS_DISABLE_KEYRING"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._environment)
        self._tmp.cleanup()

    def test_senha_direta_em_variavel_nao_e_aceita(self):
        os.environ["KAIROS_VAULT_PASSWORD"] = "senha-proibida"  # noqa: S105

        service = build_credential_service(self.root)

        self.assertEqual(service.state, VaultState.NOT_CONFIGURED)

    def test_arquivo_de_passphrase_0600_inicializa_o_fallback(self):
        passphrase = self.root / "vault-passphrase"
        passphrase.write_text("senha-administrada\n", encoding="utf-8")
        passphrase.chmod(0o600)
        os.environ["KAIROS_VAULT_PASSPHRASE_FILE"] = str(passphrase)

        service = build_credential_service(self.root)

        self.assertEqual(service.state, VaultState.UNLOCKED)
        self.assertNotIn(b"senha-administrada", (self.root / "credentials.vault").read_bytes())


if __name__ == "__main__":
    unittest.main()
