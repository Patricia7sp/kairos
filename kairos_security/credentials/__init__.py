"""Cofre seguro e metadados de credenciais do Kairos."""

from kairos_security.credentials.contracts import (
    CredentialMetadata,
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    CredentialVault,
    VaultError,
    VaultLockedError,
    VaultState,
)
from kairos_security.credentials.encrypted import (
    EncryptedFileVault,
    InvalidMasterPasswordError,
)
from kairos_security.credentials.keyring_backend import KeyringClient, SystemKeyringVault
from kairos_security.credentials.service import CredentialService, ExternalCredentialSource

__all__ = [
    "CredentialMetadata",
    "CredentialNotFoundError",
    "CredentialRef",
    "CredentialSecret",
    "CredentialService",
    "CredentialVault",
    "EncryptedFileVault",
    "ExternalCredentialSource",
    "InvalidMasterPasswordError",
    "KeyringClient",
    "SystemKeyringVault",
    "VaultError",
    "VaultLockedError",
    "VaultState",
]
