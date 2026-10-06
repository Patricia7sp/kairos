"""Operações de perfil compartilhadas pela CLI e Web, sob o lock do cofre."""

from __future__ import annotations

import json
from pathlib import Path

from kairos_domain.credentials import contains_secret_fields
from kairos_security.credentials import factory, io
from kairos_security.credentials.contracts import (
    CredentialMetadata,
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    CredentialVault,
    VaultError,
    VaultState,
)


class ReadOnlyCredentialError(VaultError):
    """A origem da credencial não permite alteração pelo perfil."""


def active_credential_selections(
    document: object, *, backend: str | None = None, provider: str | None = None
) -> dict[str, dict[str, str]]:
    if not isinstance(document, dict):
        raise VaultError("auth.json deve ser um objeto")
    selected = document.get("active_credentials", {})
    if not isinstance(selected, dict) or any(
        not isinstance(provider, str)
        or not provider.strip()
        or not isinstance(selection, dict)
        or set(selection) != {"credential_id", "backend"}
        or not isinstance(selection["credential_id"], str)
        or not selection["credential_id"].strip()
        or selection["backend"] not in ("keyring", "encrypted")
        for provider, selection in selected.items()
    ):
        raise VaultError("seleção de credenciais inválida; faça novo login")
    if backend is not None and any(
        selection["backend"] != backend
        for selected_provider, selection in selected.items()
        if provider is None or selected_provider == provider
    ):
        raise VaultError(
            "Cofre da credencial ativa indisponível; restaure o backend ou faça novo login."
        )
    return selected


class ProfileCredentialService:
    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.path = self.home / "auth.json"

    def save_primary(
        self, provider: str, secret: str, *, auth_method: str = "api_key"
    ) -> CredentialMetadata:
        ref = CredentialRef(provider, "primary")
        with io.credential_file_lock(self.path):
            document, pool, original = self._document()
            entries = pool.get(provider, [])
            if any(not isinstance(entry.get("credential_id"), str) for entry in entries):
                raise VaultError("credenciais legadas exigem kairos auth migrate antes do login")
            vault = factory.build_credential_service(self.home)
            self._writable(vault)
            metadata = next((item for item in vault.list(provider) if item.ref == ref), None)
            if metadata is not None and metadata.origin == "external":
                raise ReadOnlyCredentialError(
                    "Credencial externa é somente leitura; altere-a na origem."
                )
            try:
                previous = vault.get(ref)
            except CredentialNotFoundError:
                previous = None
            try:
                saved = vault.put(
                    ref, CredentialSecret({"api_key": secret}), auth_method=auth_method
                )
                pool[provider] = [
                    {"credential_id": ref.credential_id, "auth_method": saved.auth_method},
                    *(
                        entry
                        for entry in entries
                        if entry.get("credential_id") != ref.credential_id
                    ),
                ]
                document.setdefault("active_credentials", {})[provider] = {
                    "credential_id": ref.credential_id,
                    "backend": "keyring" if saved.origin == "keyring" else "encrypted",
                }
                self._write(document)
            except Exception:
                try:
                    if previous is None:
                        try:
                            vault.delete(ref)
                        except CredentialNotFoundError:
                            pass
                    else:
                        vault.put(
                            ref,
                            previous,
                            auth_method=metadata.auth_method if metadata else auth_method,
                        )
                finally:
                    self._restore_document(original)
                raise
            return saved

    def remove_primary(self, provider: str) -> None:
        self._remove(provider, primary_only=True)

    def logout(self, provider: str | None = None) -> int:
        return self._remove(provider, primary_only=False)

    def _remove(self, provider: str | None, *, primary_only: bool) -> int:
        with io.credential_file_lock(self.path):
            document, pool, original = self._document()
            vault = factory.build_credential_service(self.home)
            self._writable(vault)
            backend = "keyring" if vault.state == VaultState.KEYRING else "encrypted"
            active_credential_selections(document, backend=backend, provider=provider)
            metadata = [
                item
                for item in vault.list(provider)
                if not primary_only or item.ref.credential_id == "primary"
            ]
            if primary_only and not metadata:
                raise CredentialNotFoundError("Credencial principal não encontrada no cofre.")
            if any(item.origin == "external" for item in metadata):
                raise ReadOnlyCredentialError(
                    "Credencial externa é somente leitura; remova-a na origem."
                )
            previous = [(item, vault.get(item.ref)) for item in metadata]
            original_document = json.dumps(document)
            if primary_only:
                remaining = [
                    entry
                    for entry in pool.get(provider, [])
                    if entry.get("credential_id") != "primary"
                ]
                if remaining:
                    pool[provider] = remaining
                else:
                    pool.pop(provider, None)
            elif provider is None:
                pool.clear()
                document.pop("active_credentials", None)
            else:
                pool.pop(provider, None)
                active_credential_selections(document).pop(provider, None)
            removed = []
            try:
                for item, secret in previous:
                    removed.append((item, secret))
                    vault.delete(item.ref)
                if json.dumps(document) != original_document:
                    self._write(document)
            except Exception:
                try:
                    for item, secret in removed:
                        vault.put(item.ref, secret, auth_method=item.auth_method)
                finally:
                    self._restore_document(original)
                raise
            return len(removed)

    def _document(self) -> tuple[dict, dict, str | None]:
        original = self.path.read_text(encoding="utf-8") if self.path.exists() else None
        document = json.loads(original) if original is not None else {}
        if not isinstance(document, dict):
            raise VaultError("auth.json deve ser um objeto")
        active_credential_selections(document)
        pool = document.setdefault("credential_pool", {})
        if not isinstance(pool, dict) or any(
            not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries)
            for entries in pool.values()
        ):
            raise VaultError("metadados de credenciais indisponíveis")
        if contains_secret_fields(pool):
            raise VaultError(
                "credenciais legadas exigem kairos auth migrate antes de alterar o perfil"
            )
        return document, pool, original

    def _restore_document(self, original: str | None) -> None:
        current = self.path.read_text(encoding="utf-8") if self.path.exists() else None
        if current == original:
            return
        if original is None:
            self.path.unlink()
        else:
            io.secure_atomic_write_text(self.path, original)

    def _write(self, document: dict) -> None:
        io.secure_atomic_write_text(self.path, json.dumps(document, ensure_ascii=False, indent=2))

    @staticmethod
    def _writable(vault: CredentialVault) -> None:
        if vault.state == VaultState.EXTERNAL:
            raise ReadOnlyCredentialError(
                "Credencial externa é somente leitura; altere-a na origem."
            )
