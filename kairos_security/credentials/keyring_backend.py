"""Backend que mantém segredos no keyring e somente um índice local não secreto."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Protocol

from kairos_security.credentials.contracts import (
    CredentialMetadata,
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    VaultState,
)
from kairos_security.credentials.io import credential_file_lock, secure_atomic_write_text


class KeyringClient(Protocol):
    priority: int | float

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def get_password(self, service: str, username: str) -> str | None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class SystemKeyringVault:
    def __init__(self, client: KeyringClient, *, index_path: Path) -> None:
        self._client = client
        self.index_path = index_path
        self._available = self._probe()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def state(self) -> VaultState:
        return VaultState.KEYRING if self.available else VaultState.NOT_CONFIGURED

    def put(
        self,
        ref: CredentialRef,
        secret: CredentialSecret,
        *,
        auth_method: str = "api_key",
    ) -> CredentialMetadata:
        with credential_file_lock(self.index_path):
            values = secret.reveal()
            identifier = values.get("api_key") or values.get("token") or next(iter(values.values()))
            payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            previous = self._client.get_password(self._service(ref), ref.credential_id)
            self._client.set_password(self._service(ref), ref.credential_id, payload)
            entries = self._read_index()
            entry = {
                "provider": ref.provider,
                "credential_id": ref.credential_id,
                "auth_method": auth_method,
            }
            entries[self._entry_key(ref)] = entry
            try:
                self._write_index(entries)
            except Exception:
                if previous is None:
                    self._client.delete_password(self._service(ref), ref.credential_id)
                else:
                    self._client.set_password(self._service(ref), ref.credential_id, previous)
                raise
        return CredentialMetadata(ref, auth_method, "keyring", identifier)

    def get(self, ref: CredentialRef) -> CredentialSecret:
        payload = self._client.get_password(self._service(ref), ref.credential_id)
        if payload is None:
            raise CredentialNotFoundError(
                f"credencial não encontrada: {ref.provider}/{ref.credential_id}"
            )
        return CredentialSecret(json.loads(payload))

    def list(self, provider: str | None = None) -> list[CredentialMetadata]:
        with credential_file_lock(self.index_path):
            metadata = [
                CredentialMetadata(
                    CredentialRef(entry["provider"], entry["credential_id"]),
                    entry["auth_method"],
                    "keyring",
                )
                for entry in self._read_index().values()
                if provider is None or entry["provider"] == provider
            ]
        return sorted(metadata, key=lambda item: (item.ref.provider, item.ref.credential_id))

    def delete(self, ref: CredentialRef) -> None:
        with credential_file_lock(self.index_path):
            entries = self._read_index()
            key = self._entry_key(ref)
            if key not in entries:
                raise CredentialNotFoundError(
                    f"credencial não encontrada: {ref.provider}/{ref.credential_id}"
                )
            self._client.delete_password(self._service(ref), ref.credential_id)
            del entries[key]
            self._write_index(entries)

    def _probe(self) -> bool:
        try:
            if self._client.priority <= 0:
                return False
            username = f"probe-{uuid.uuid4().hex}"
            service = "kairos/_probe"
            value = uuid.uuid4().hex
            self._client.set_password(service, username, value)
            matches = self._client.get_password(service, username) == value
            self._client.delete_password(service, username)
            if not matches:
                return False
        except Exception:  # noqa: BLE001 - backends de terceiros não têm erro comum
            return False
        return True

    def _read_index(self) -> dict[str, dict[str, str]]:
        if not self.index_path.exists():
            return {}
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        return {
            self._entry_key(CredentialRef(item["provider"], item["credential_id"])): item
            for item in payload.get("entries", [])
        }

    def _write_index(self, entries: dict[str, dict[str, str]]) -> None:
        content = json.dumps(
            {"version": 1, "entries": list(entries.values())},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        secure_atomic_write_text(self.index_path, content)

    @staticmethod
    def _service(ref: CredentialRef) -> str:
        return f"kairos/{ref.provider}"

    @staticmethod
    def _entry_key(ref: CredentialRef) -> str:
        return f"{ref.provider}:{ref.credential_id}"
