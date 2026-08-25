"""Seleção estável do backend de credenciais e fontes externas somente leitura."""

from __future__ import annotations

from collections.abc import Mapping

from kairos_security.credentials.contracts import (
    CredentialMetadata,
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    CredentialVault,
    VaultError,
    VaultState,
)
from kairos_security.credentials.keyring_backend import SystemKeyringVault


class CredentialService:
    def __init__(self, *, keyring: SystemKeyringVault, encrypted: CredentialVault) -> None:
        self._backend: CredentialVault = keyring if keyring.available else encrypted

    @property
    def state(self) -> VaultState:
        return self._backend.state

    def put(
        self,
        ref: CredentialRef,
        secret: CredentialSecret,
        *,
        auth_method: str = "api_key",
    ) -> CredentialMetadata:
        return self._backend.put(ref, secret, auth_method=auth_method)

    def get(self, ref: CredentialRef) -> CredentialSecret:
        return self._backend.get(ref)

    def list(self, provider: str | None = None) -> list[CredentialMetadata]:
        return self._backend.list(provider)

    def delete(self, ref: CredentialRef) -> None:
        self._backend.delete(ref)


class ExternalCredentialSource:
    """Fonte injetada pelo ambiente; não aceita mutações pelo Kairos."""

    def __init__(self, secrets: Mapping[CredentialRef, CredentialSecret]) -> None:
        self._secrets = dict(secrets)

    @property
    def state(self) -> VaultState:
        return VaultState.EXTERNAL

    def put(
        self,
        ref: CredentialRef,
        secret: CredentialSecret,
        *,
        auth_method: str = "api_key",
    ) -> CredentialMetadata:
        raise VaultError("fonte externa é somente leitura")

    def get(self, ref: CredentialRef) -> CredentialSecret:
        try:
            return CredentialSecret(self._secrets[ref].reveal())
        except KeyError as exc:
            raise CredentialNotFoundError(
                f"credencial não encontrada: {ref.provider}/{ref.credential_id}"
            ) from exc

    def list(self, provider: str | None = None) -> list[CredentialMetadata]:
        refs = [ref for ref in self._secrets if provider is None or ref.provider == provider]
        return [
            CredentialMetadata(ref, "external", "external")
            for ref in sorted(refs, key=lambda item: (item.provider, item.credential_id))
        ]

    def delete(self, ref: CredentialRef) -> None:
        raise VaultError("fonte externa é somente leitura")
