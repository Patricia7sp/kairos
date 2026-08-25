"""Composição única dos backends de credenciais para CLI, Web e runtime."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from kairos_security.credentials.encrypted import EncryptedFileVault
from kairos_security.credentials.keyring_backend import SystemKeyringVault
from kairos_security.credentials.service import CredentialService


class PassphraseFileError(RuntimeError):
    """O arquivo administrado de passphrase não atende às garantias mínimas."""


class _DisabledKeyring:
    priority = 0

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("keyring desabilitado")

    def get_password(self, service: str, username: str) -> str | None:
        return None

    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("keyring desabilitado")


def read_managed_passphrase() -> str | None:
    configured = os.environ.get("KAIROS_VAULT_PASSPHRASE_FILE")
    if not configured:
        return None
    path = Path(configured)
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise PassphraseFileError("arquivo de passphrase deve ser regular e não simbólico")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PassphraseFileError("arquivo de passphrase exige proprietário atual e modo 0600")
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PassphraseFileError("arquivo de passphrase não pôde ser lido") from exc
    if not value:
        raise PassphraseFileError("arquivo de passphrase está vazio")
    return value


def build_credential_service(home: Path) -> CredentialService:
    if os.environ.get("KAIROS_DISABLE_KEYRING") == "1":
        client = _DisabledKeyring()
    else:
        import keyring

        client = keyring.get_keyring()
    keyring_vault = SystemKeyringVault(client, index_path=home / "credential-index.json")
    encrypted = EncryptedFileVault(home / "credentials.vault")
    service = CredentialService(keyring=keyring_vault, encrypted=encrypted)
    if not keyring_vault.available and (passphrase := read_managed_passphrase()):
        if encrypted.path.exists():
            service.unlock(passphrase)
        else:
            service.initialize(passphrase)
    return service
