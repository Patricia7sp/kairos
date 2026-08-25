"""Contrato público para armazenamento e consulta de credenciais."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class VaultState(StrEnum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    KEYRING = "keyring"
    EXTERNAL = "external"
    NOT_CONFIGURED = "not_configured"


class VaultError(RuntimeError):
    """Erro base que nunca carrega material secreto."""


class VaultLockedError(VaultError):
    """O backend existe, mas precisa ser desbloqueado."""


class CredentialNotFoundError(VaultError, LookupError):
    """A referência solicitada não existe no backend ativo."""


@dataclass(frozen=True)
class CredentialRef:
    provider: str
    credential_id: str

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider é obrigatório")
        if not self.credential_id.strip():
            raise ValueError("credential_id é obrigatório")


@dataclass(frozen=True)
class CredentialMetadata:
    ref: CredentialRef
    auth_method: str
    origin: str
    identifier: str = field(default="", repr=False)

    @property
    def masked_identifier(self) -> str:
        value = self.identifier
        return "••••" if len(value) < 6 else f"{value[:4]}…{value[-2:]}"


class CredentialSecret:
    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str]) -> None:
        if not values or any(not isinstance(key, str) or not key for key in values):
            raise ValueError("credential secret exige chaves nomeadas")
        if any(not isinstance(value, str) for value in values.values()):
            raise TypeError("credential secret aceita somente valores textuais")
        self._values = dict(values)

    def reveal(self) -> dict[str, str]:
        return dict(self._values)

    def __repr__(self) -> str:
        return "CredentialSecret(<redacted>)"


class CredentialVault(Protocol):
    @property
    def state(self) -> VaultState: ...

    def put(
        self,
        ref: CredentialRef,
        secret: CredentialSecret,
        *,
        auth_method: str = "api_key",
    ) -> CredentialMetadata: ...

    def get(self, ref: CredentialRef) -> CredentialSecret: ...

    def list(self, provider: str | None = None) -> list[CredentialMetadata]: ...

    def delete(self, ref: CredentialRef) -> None: ...
