"""Migração em duas etapas do credential_pool legado para o cofre."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kairos_security.credentials.contracts import (
    CredentialMetadata,
    CredentialRef,
    CredentialSecret,
    CredentialVault,
)

_SECRET_FIELDS = ("api_key", "token", "secret", "password")


class MigrationConfirmationRequired(RuntimeError):
    """A remoção do texto aberto exige confirmação inequívoca."""


class MigrationVerificationError(RuntimeError):
    """O valor relido do cofre não corresponde à origem legada."""


@dataclass(frozen=True)
class MigrationReport:
    credentials_found: int = 0
    credentials_verified: int = 0
    credentials_finalized: int = 0


class LegacyCredentialMigration:
    def __init__(self, auth_path: Path, vault: CredentialVault) -> None:
        self.auth_path = auth_path
        self.vault = vault

    def scan(self) -> MigrationReport:
        return MigrationReport(credentials_found=len(self._legacy_entries(self._read())))

    def import_and_verify(self) -> MigrationReport:
        entries = self._legacy_entries(self._read())
        verified = 0
        for provider, _index, entry, ref in entries:
            secret = CredentialSecret(self._string_payload(entry))
            self.vault.put(
                ref,
                secret,
                auth_method=self._auth_method(entry),
            )
            if self.vault.get(ref).reveal() != secret.reveal():
                raise MigrationVerificationError(
                    f"credencial não pôde ser verificada: {provider}/{ref.credential_id}"
                )
            verified += 1
        return MigrationReport(credentials_found=len(entries), credentials_verified=verified)

    def finalize(self, *, confirm: bool = False) -> MigrationReport:
        if not confirm:
            raise MigrationConfirmationRequired(
                "confirmação necessária para remover credenciais em texto aberto"
            )
        document = self._read()
        entries = self._legacy_entries(document)
        replacements: list[tuple[str, int, dict[str, str]]] = []
        for provider, index, entry, ref in entries:
            expected = self._string_payload(entry)
            try:
                actual = self.vault.get(ref).reveal()
            except Exception as exc:
                raise MigrationVerificationError(
                    f"credencial não pôde ser verificada: {provider}/{ref.credential_id}"
                ) from exc
            if actual != expected:
                raise MigrationVerificationError(
                    f"credencial não pôde ser verificada: {provider}/{ref.credential_id}"
                )
            identifier = self._identifier(actual)
            metadata = CredentialMetadata(
                ref,
                self._auth_method(entry),
                "vault",
                identifier,
            )
            replacements.append(
                (
                    provider,
                    index,
                    {
                        "credential_id": ref.credential_id,
                        "auth_method": metadata.auth_method,
                        "masked_identifier": metadata.masked_identifier,
                    },
                )
            )

        pool = document["credential_pool"]
        for provider, index, replacement in replacements:
            pool[provider][index] = replacement
        self._write_atomically(document)
        return MigrationReport(credentials_finalized=len(replacements))

    def _read(self) -> dict[str, Any]:
        return json.loads(self.auth_path.read_text(encoding="utf-8"))

    @staticmethod
    def _legacy_entries(
        document: dict[str, Any],
    ) -> list[tuple[str, int, dict[str, Any], CredentialRef]]:
        found = []
        for provider, credentials in document.get("credential_pool", {}).items():
            for index, entry in enumerate(credentials):
                if "credential_id" in entry or not any(key in entry for key in _SECRET_FIELDS):
                    continue
                ref = CredentialRef(provider, f"legacy-{index + 1}")
                found.append((provider, index, entry, ref))
        return found

    @staticmethod
    def _string_payload(entry: dict[str, Any]) -> dict[str, str]:
        if any(not isinstance(value, str) for value in entry.values()):
            raise MigrationVerificationError(
                "credencial legada contém valores não textuais e não será alterada"
            )
        return dict(entry)

    @staticmethod
    def _auth_method(entry: dict[str, Any]) -> str:
        configured = entry.get("auth_method") or entry.get("auth_type")
        if isinstance(configured, str) and configured:
            return configured
        return "token" if "token" in entry and "api_key" not in entry else "api_key"

    @staticmethod
    def _identifier(values: dict[str, str]) -> str:
        for key in _SECRET_FIELDS:
            if value := values.get(key):
                return value
        return next(iter(values.values()))

    def _write_atomically(self, document: dict[str, Any]) -> None:
        tmp = self.auth_path.with_suffix(self.auth_path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.auth_path)
            os.chmod(self.auth_path, 0o600)
        finally:
            if tmp.exists():
                tmp.unlink()
