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

__all__ = [
    "CredentialMetadata",
    "CredentialNotFoundError",
    "CredentialRef",
    "CredentialSecret",
    "CredentialVault",
    "EncryptedFileVault",
    "InvalidMasterPasswordError",
    "VaultError",
    "VaultLockedError",
    "VaultState",
]
