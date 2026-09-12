"""Authenticated application router for advanced nonsecret provider settings."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from kairos_providers.settings import (
    CredentialTransferConfirmationRequired,
    get_provider_settings,
    save_provider_settings,
)

router = APIRouter()


def _home(request: Request) -> Path:
    supplied = getattr(request.app.state, "kairos_home", None)
    return Path(
        supplied if supplied is not None else os.environ.get("KAIROS_HOME", Path.home() / ".kairos")
    )


@router.get("/api/providers/{provider}/settings")
def get_settings(provider: str, request: Request):
    try:
        return {"provider": provider, "settings": get_provider_settings(_home(request), provider)}
    except LookupError as exc:
        raise HTTPException(404, "provider sem configurações avançadas") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Configurações de provider inválidas.") from exc


@router.put("/api/providers/{provider}/settings")
async def put_settings(provider: str, request: Request):
    try:
        body = await request.json()
        if (
            not isinstance(body, dict)
            or set(body) - {"settings", "confirm_credential_transfer"}
            or "settings" not in body
        ):
            raise ValueError("requisição inválida")
        settings = save_provider_settings(
            _home(request),
            provider,
            body["settings"],
            confirm_credential_transfer=body.get("confirm_credential_transfer", False),
        )
        return {"provider": provider, "settings": settings}
    except CredentialTransferConfirmationRequired as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, "provider sem configurações avançadas") from exc
    except (TypeError, ValueError) as exc:
        # Framework validation includes rejected values; never echo submitted secrets.
        raise HTTPException(422, "Configurações de provider inválidas.") from exc
