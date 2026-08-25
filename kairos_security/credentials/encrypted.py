"""Backend de arquivo criptografado e bloqueável para credenciais."""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from kairos_security.credentials.contracts import (
    CredentialMetadata,
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    VaultLockedError,
    VaultState,
)
from kairos_security.credentials.io import credential_file_lock, secure_atomic_write_text

_ASSOCIATED_DATA = b"kairos-credential-vault-v1"
_VERSION = 1


class InvalidMasterPasswordError(RuntimeError):
    """A senha não abre o cofre; a mensagem não revela detalhes internos."""


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _derive_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    if not passphrase:
        raise ValueError("senha-mestra é obrigatória")
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode())


class EncryptedFileVault:
    def __init__(self, path: Path, *, scrypt_n: int = 2**14) -> None:
        if scrypt_n < 2 or scrypt_n & (scrypt_n - 1):
            raise ValueError("scrypt_n deve ser potência de dois")
        self.path = path
        self._scrypt_n = scrypt_n
        self._key: bytearray | None = None
        self._entries: dict[str, dict[str, Any]] | None = None

    @property
    def state(self) -> VaultState:
        if self._key is not None:
            return VaultState.UNLOCKED
        if self.path.exists():
            return VaultState.LOCKED
        return VaultState.NOT_CONFIGURED

    def initialize(self, passphrase: str) -> None:
        with credential_file_lock(self.path):
            if self.path.exists():
                raise FileExistsError(self.path)
            salt = secrets.token_bytes(16)
            self._key = bytearray(_derive_key(passphrase, salt, n=self._scrypt_n, r=8, p=1))
            self._entries = {}
            self._save(salt=salt, n=self._scrypt_n, r=8, p=1)

    def unlock(self, passphrase: str) -> None:
        try:
            with credential_file_lock(self.path):
                envelope = json.loads(self.path.read_text(encoding="utf-8"))
            kdf = envelope["kdf"]
            cipher = envelope["cipher"]
            salt = _b64decode(kdf["salt"])
            nonce = _b64decode(cipher["nonce"])
            n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
            if (
                envelope["version"] != _VERSION
                or kdf["name"] != "scrypt"
                or cipher["name"] != "aes-256-gcm"
                or len(salt) != 16
                or len(nonce) != 12
                or n < 2**10
                or n > 2**20
                or n & (n - 1)
                or not 1 <= r <= 32
                or not 1 <= p <= 16
            ):
                raise ValueError("formato inválido")
            key = _derive_key(
                passphrase,
                salt,
                n=n,
                r=r,
                p=p,
            )
            plaintext = AESGCM(key).decrypt(
                nonce,
                _b64decode(cipher["ciphertext"]),
                _ASSOCIATED_DATA,
            )
            payload = json.loads(plaintext)
            if envelope["version"] != _VERSION or not isinstance(payload["entries"], dict):
                raise ValueError("formato inválido")
        except (
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
            OSError,
            binascii.Error,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidMasterPasswordError("não foi possível desbloquear o cofre") from exc
        self._key = bytearray(key)
        self._entries = payload["entries"]

    def lock(self) -> None:
        if self._key is not None:
            self._key[:] = b"\x00" * len(self._key)
        self._key = None
        self._entries = None

    def put(
        self,
        ref: CredentialRef,
        secret: CredentialSecret,
        *,
        auth_method: str = "api_key",
    ) -> CredentialMetadata:
        with credential_file_lock(self.path):
            self._refresh_entries()
            entries = self._require_entries()
            values = secret.reveal()
            identifier = values.get("api_key") or values.get("token") or next(iter(values.values()))
            entries[self._entry_key(ref)] = {
                "provider": ref.provider,
                "credential_id": ref.credential_id,
                "auth_method": auth_method,
                "identifier": identifier,
                "secret": values,
            }
            self._save_from_existing_envelope()
        return CredentialMetadata(ref, auth_method, "vault", identifier)

    def get(self, ref: CredentialRef) -> CredentialSecret:
        with credential_file_lock(self.path):
            self._refresh_entries()
            entries = self._require_entries()
            try:
                entry = entries[self._entry_key(ref)]
            except KeyError as exc:
                raise CredentialNotFoundError(
                    f"credencial não encontrada: {ref.provider}/{ref.credential_id}"
                ) from exc
        return CredentialSecret(entry["secret"])

    def list(self, provider: str | None = None) -> list[CredentialMetadata]:
        with credential_file_lock(self.path):
            self._refresh_entries()
            entries = self._require_entries()
            metadata = [
                CredentialMetadata(
                    CredentialRef(entry["provider"], entry["credential_id"]),
                    entry["auth_method"],
                    "vault",
                    entry["identifier"],
                )
                for entry in entries.values()
                if provider is None or entry["provider"] == provider
            ]
        return sorted(metadata, key=lambda item: (item.ref.provider, item.ref.credential_id))

    def delete(self, ref: CredentialRef) -> None:
        with credential_file_lock(self.path):
            self._refresh_entries()
            entries = self._require_entries()
            try:
                del entries[self._entry_key(ref)]
            except KeyError as exc:
                raise CredentialNotFoundError(
                    f"credencial não encontrada: {ref.provider}/{ref.credential_id}"
                ) from exc
            self._save_from_existing_envelope()

    @staticmethod
    def _entry_key(ref: CredentialRef) -> str:
        return f"{ref.provider}:{ref.credential_id}"

    def _require_entries(self) -> dict[str, dict[str, Any]]:
        if self._key is None or self._entries is None:
            raise VaultLockedError("cofre bloqueado")
        return self._entries

    def _save_from_existing_envelope(self) -> None:
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        kdf = envelope["kdf"]
        self._save(
            salt=_b64decode(kdf["salt"]),
            n=int(kdf["n"]),
            r=int(kdf["r"]),
            p=int(kdf["p"]),
        )

    def _refresh_entries(self) -> None:
        if self._key is None:
            raise VaultLockedError("cofre bloqueado")
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        plaintext = AESGCM(bytes(self._key)).decrypt(
            _b64decode(envelope["cipher"]["nonce"]),
            _b64decode(envelope["cipher"]["ciphertext"]),
            _ASSOCIATED_DATA,
        )
        self._entries = json.loads(plaintext)["entries"]

    def _save(self, *, salt: bytes, n: int, r: int, p: int) -> None:
        entries = self._require_entries()
        assert self._key is not None
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(
            {"entries": entries}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        ciphertext = AESGCM(bytes(self._key)).encrypt(nonce, plaintext, _ASSOCIATED_DATA)
        envelope = {
            "version": _VERSION,
            "kdf": {"name": "scrypt", "salt": _b64encode(salt), "n": n, "r": r, "p": p},
            "cipher": {
                "name": "aes-256-gcm",
                "nonce": _b64encode(nonce),
                "ciphertext": _b64encode(ciphertext),
            },
        }
        secure_atomic_write_text(
            self.path,
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
        )
