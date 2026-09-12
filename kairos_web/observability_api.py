"""Read-only status and accounting from observed runtime and canonical state."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from kairos_providers.catalog import UnknownModelError
from kairos_providers.composition import build_provider_gateway
from kairos_providers.contracts import ProviderModelRef
from kairos_providers.settings import load_config_document
from kairos_runtime import RuntimeErrorInfo
from kairos_state.usage_summary import read_usage_summary
from kairos_web.provider_api import public_profiles, serialize_model

router = APIRouter(prefix="/api")


def _home(request: Request) -> Path:
    # Imported at call time to share the application's isolation policy.
    from kairos_web.server import _application_home

    return _application_home(request.app)


def _configuration(request: Request) -> dict[str, Any]:
    try:
        return load_config_document(_home(request))
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "configuração indisponível") from exc


def _selection(config: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        key: value if isinstance(value := config.get(key), str) and value.strip() else None
        for key in ("provider", "model")
    }


async def _runtime_observation(request: Request) -> dict[str, Any]:
    client = getattr(request.app.state, "runtime_client", None)
    if client is None:
        return {"state": "unknown", "enabled": None}
    try:
        observed = await asyncio.wait_for(client.status(), timeout=1.0)
    except (RuntimeErrorInfo, TimeoutError, OSError):
        return {"state": "unavailable", "enabled": None}
    if not isinstance(observed, Mapping) or observed.get("state") not in {
        "ready",
        "disabled",
        "unavailable",
    }:
        return {"state": "unknown", "enabled": None}
    return {
        "state": observed["state"],
        "enabled": observed.get("enabled") if type(observed.get("enabled")) is bool else None,
    }


@router.get("/status")
async def get_status(request: Request):
    config = _configuration(request)
    return {
        "status": "ok",
        # Serving HTTP proves this API is up, not that a messaging gateway is running.
        "gateway": "unknown",
        "runtime": await _runtime_observation(request),
        "version": request.app.version,
        "app": "kairos",
        "authenticated": True,
        "active_profile": config.get("active_profile")
        if isinstance(config.get("active_profile"), str)
        else None,
        **_selection(config),
    }


@router.get("/model/info")
async def get_model_info(request: Request):
    selection = _selection(_configuration(request))
    capabilities = dict.fromkeys(
        ("supports_tools", "supports_vision", "supports_streaming", "supports_reasoning")
    )
    if selection["provider"] and selection["model"]:
        gateway = build_provider_gateway(_home(request))
        try:
            try:
                model = gateway.catalog.find(ProviderModelRef(**selection))
            except UnknownModelError:
                pass
            else:
                known = serialize_model(model)["capabilities"]
                capabilities = {key: known[key.removeprefix("supports_")] for key in capabilities}
        finally:
            await gateway.aclose()
    return {**selection, "capabilities": capabilities}


@router.get("/profiles")
def get_profiles(request: Request):
    # Routing profiles belong to conversations; there is no global activation.
    return {"profiles": public_profiles(_configuration(request))}


@router.get("/profiles/active")
def get_active_profile(request: Request):
    return {"name": None, **_selection(_configuration(request))}


@router.get("/analytics/usage")
def get_analytics_usage(request: Request, days: int | None = Query(default=None, ge=1, le=3650)):
    return read_usage_summary(_home(request), requested_days=days)
