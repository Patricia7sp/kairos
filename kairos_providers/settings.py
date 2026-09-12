"""Validated nonsecret provider settings and serialized config updates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import yaml

from kairos_providers.catalog import CatalogSnapshot
from kairos_providers.catalog_store import CatalogSnapshotStore
from kairos_providers.provider_profiles import custom_profile
from kairos_security.credentials.io import credential_file_lock, secure_atomic_write_text


class CredentialTransferConfirmationRequired(ValueError):
    """A different destination requires the user's explicit credential consent."""


def load_config_document(home: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        raise ValueError("config.yaml inválido") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("config.yaml inválido")
    return raw


def update_config_document(home: Path, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Atomically read/modify/write while sharing the config lock with all writers."""
    path = home / "config.yaml"
    with credential_file_lock(path):
        document = load_config_document(home)
        mutate(document)
        secure_atomic_write_text(path, yaml.safe_dump(document, sort_keys=False))
        return document


def _attribution(value: object, *, url: bool = False) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > (2048 if url else 128)
        or any(ord(char) < 32 or ord(char) > 126 for char in value)
    ):
        raise ValueError("atribuição inválida")
    if url:
        # Reuse the endpoint validator, including userinfo/query/fragment rejection.
        custom_profile(base_url=value, trusted_remote=True)
    return value


def validate_provider_settings(provider: str, raw: object) -> dict[str, Any]:
    if provider not in {"custom", "openrouter"}:
        raise LookupError("provider sem configurações avançadas")
    if not isinstance(raw, Mapping):
        raise ValueError("configurações inválidas")
    allowed = (
        {"base_url", "models_url", "trusted_remote", "headers"}
        if provider == "custom"
        else {"referer", "title"}
    )
    if set(raw) - allowed:
        raise ValueError("campo de configuração não permitido")
    if provider == "openrouter":
        return {
            "referer": _attribution(raw.get("referer"), url=True),
            "title": _attribution(raw.get("title")),
        }
    headers = raw.get("headers", {})
    if not isinstance(headers, Mapping):
        raise ValueError("headers inválidos")
    for name, value in headers.items():
        if not isinstance(name, str) or name.casefold() not in {"http-referer", "x-title"}:
            raise ValueError("header não permitido")
        if value is None:
            raise ValueError("atribuição inválida")
        _attribution(value, url=name.casefold() == "http-referer")
    profile = custom_profile(
        base_url=raw.get("base_url", "http://127.0.0.1:8000/v1"),
        models_url=raw.get("models_url"),
        trusted_remote=raw.get("trusted_remote", False),
        headers=headers,
        allowed_headers=frozenset(headers),
    )
    return {
        "base_url": profile.base_url,
        "models_url": profile.models_url,
        "trusted_remote": profile.trusted_remote,
        "headers": dict(profile.headers),
    }


def settings_from_document(document: Mapping[str, Any], provider: str) -> dict[str, Any]:
    raw = document.get("provider_settings", {})
    if not isinstance(raw, Mapping):
        raise ValueError("provider_settings inválido")
    return validate_provider_settings(provider, raw.get(provider, {}))


def get_provider_settings(home: Path, provider: str) -> dict[str, Any]:
    return settings_from_document(load_config_document(home), provider)


def _origin(value: str) -> tuple[str, str, int | None]:
    url = httpx.URL(value)
    return url.scheme, url.host, url.port


def save_provider_settings(
    home: Path, provider: str, raw: object, *, confirm_credential_transfer: bool = False
) -> dict[str, Any]:
    settings = validate_provider_settings(provider, raw)
    if type(confirm_credential_transfer) is not bool:
        raise ValueError("confirmação deve ser bool")

    def mutate(document: dict[str, Any]) -> None:
        previous = settings_from_document(document, provider)
        if (
            provider == "custom"
            and _origin(previous["base_url"]) != _origin(settings["base_url"])
            and not confirm_credential_transfer
        ):
            raise CredentialTransferConfirmationRequired(
                "Confirme o envio da credencial armazenada para a nova origem."
            )
        document.setdefault("provider_settings", {})[provider] = settings

    update_config_document(home, mutate)
    return settings


class EndpointCatalogSnapshotStore(CatalogSnapshotStore):
    """Custom discovery is cached by destination; existing providers keep their cache."""

    def __init__(self, home: Path, custom_settings: Mapping[str, Any]) -> None:
        super().__init__(home / "model-catalog.json")
        fingerprint = hashlib.sha256(
            json.dumps(dict(custom_settings), sort_keys=True).encode()
        ).hexdigest()
        self._custom = CatalogSnapshotStore(home / "catalogs" / f"custom-{fingerprint}.json")

    def load(self, provider: str) -> CatalogSnapshot | None:
        if provider == "custom":
            return self._custom.load(provider)
        return super().load(provider)

    def save(self, provider: str, snapshot: CatalogSnapshot) -> None:
        if provider == "custom":
            self._custom.save(provider, snapshot)
        else:
            super().save(provider, snapshot)
