"""Validated generation defaults, applied by the canonical next-turn composition."""

import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from kairos_providers.settings import load_config_document, update_config_document
from kairos_web.provider_api import public_parameters

router = APIRouter()
_FIELDS = {"temperature", "max_tokens"}


def _home(request):
    from kairos_web.server import _application_home

    return Path(_application_home(request.app))


def _payload(document):
    parameters = public_parameters(document.get("parameters"))
    return {
        "generation": {key: value for key, value in parameters.items() if key in _FIELDS},
        "default_provider": document.get("provider"),
        "default_model": document.get("model"),
    }


@router.get("/api/settings")
async def get_settings(request: Request):
    return _payload(load_config_document(_home(request)))


@router.put("/api/settings")
async def save_settings(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"generation"}:
            raise ValueError
        fields = body["generation"]
        if not isinstance(fields, dict) or set(fields) - _FIELDS:
            raise ValueError
        for name, value in fields.items():
            if value is None:
                continue
            if name == "temperature":
                if (
                    type(value) not in (int, float)
                    or not 0 <= value <= 2
                    or not math.isfinite(value)
                ):
                    raise ValueError
            elif type(value) is not int or not 1 <= value <= 2**31 - 1:
                raise ValueError

        def mutate(document):
            parameters = document.setdefault("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError
            for name, value in fields.items():
                if value is None:
                    parameters.pop(name, None)
                else:
                    parameters[name] = value

        saved = update_config_document(_home(request), mutate)
    except (ValueError, TypeError, OSError) as exc:
        raise HTTPException(422, "Preferências de geração inválidas.") from exc
    return _payload(saved)
