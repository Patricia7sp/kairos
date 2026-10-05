"""Authenticated removal of the profile's primary vault credential."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from kairos_providers.composition import build_provider_gateway
from kairos_providers.provider_registry import UnknownProviderError
from kairos_security.credentials import CredentialNotFoundError
from kairos_security.credentials.profile import ProfileCredentialService, ReadOnlyCredentialError

router = APIRouter()


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

    try:
        ProfileCredentialService(home).remove_primary(provider)
    except ReadOnlyCredentialError as exc:
        raise HTTPException(409, str(exc)) from exc
    except CredentialNotFoundError as exc:
        raise HTTPException(404, "Credencial principal não encontrada no cofre.") from exc
    except Exception as exc:
        raise HTTPException(503, "Não foi possível remover a credencial do cofre.") from exc
    return {"provider": provider, "removed": True}
