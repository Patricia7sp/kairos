"""Authenticated removal of the profile's primary vault credential."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from kairos_providers.composition import build_provider_gateway
from kairos_providers.provider_registry import UnknownProviderError
from kairos_security.credentials import (
    CredentialRef,
    VaultState,
    build_credential_service,
)
from kairos_security.credentials.io import credential_file_lock, secure_atomic_write_text

router = APIRouter()


def _remove_primary_metadata(auth_path: Path, ref: CredentialRef) -> tuple[dict, bool]:
    document = json.loads(auth_path.read_text()) if auth_path.exists() else {}
    if not isinstance(document, dict):
        raise ValueError("auth inválido")
    pool = document.get("credential_pool", {})
    if not isinstance(pool, dict):
        raise ValueError("credential_pool inválido")
    entries = pool.get(ref.provider, [])
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise ValueError("credenciais inválidas")
    remaining = [item for item in entries if item.get("credential_id") != ref.credential_id]
    if remaining != entries:
        if remaining:
            pool[ref.provider] = remaining
        else:
            pool.pop(ref.provider, None)
    return document, remaining != entries


@router.delete("/api/providers/{provider}/credentials")
async def delete_provider_credential(provider: str, request: Request):
    supplied = getattr(request.app.state, "kairos_home", None)
    home = Path(
        supplied if supplied is not None else os.environ.get("KAIROS_HOME", Path.home() / ".kairos")
    )
    gateway = build_provider_gateway(home)
    try:
        try:
            gateway.registry.describe(provider)
        except UnknownProviderError as exc:
            raise HTTPException(404, "provider desconhecido") from exc
    finally:
        await gateway.aclose()

    auth_path = home / "auth.json"
    ref = CredentialRef(provider, "primary")
    # Same cross-process and thread lock as POST: read only after acquiring it.
    with credential_file_lock(auth_path):
        try:
            vault = build_credential_service(home)
            if vault.state == VaultState.EXTERNAL:
                raise HTTPException(
                    409, "Credencial externa é somente leitura; remova-a na origem."
                )
            metadata = next((item for item in vault.list(provider) if item.ref == ref), None)
            if metadata is None:
                raise HTTPException(404, "Credencial principal não encontrada no cofre.")
            if metadata.origin == "external":
                raise HTTPException(
                    409, "Credencial externa é somente leitura; remova-a na origem."
                )
            previous = vault.get(ref)
            document, metadata_changed = _remove_primary_metadata(auth_path, ref)
            vault.delete(ref)
            try:
                if metadata_changed:
                    secure_atomic_write_text(
                        auth_path, json.dumps(document, ensure_ascii=False, indent=2)
                    )
            except Exception:
                vault.put(ref, previous, auth_method=metadata.auth_method)
                raise
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "Não foi possível remover a credencial do cofre.") from exc
    return {"provider": provider, "removed": True}
