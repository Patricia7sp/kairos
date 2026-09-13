"""Comando `kairos webhook` — endpoints de webhook do gateway."""

from __future__ import annotations

import json
from pathlib import Path


def _subcommand(args, stem: str):
    return getattr(args, f"{stem}_command", None) or getattr(args, "subcommand", None)


def _load_endpoints(home: Path) -> list:
    path = home / "webhooks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        endpoints = data.get("endpoints", [])
        return endpoints if isinstance(endpoints, list) else []
    return []


async def run_webhook(home: Path, args) -> int:
    home = Path(home)
    sub = _subcommand(args, "webhook")
    as_json = bool(getattr(args, "json", False))

    if sub == "list":
        endpoints = _load_endpoints(home)
        if as_json:
            print(json.dumps({"endpoints": endpoints}, ensure_ascii=False))
        elif not endpoints:
            print("Nenhum endpoint de webhook configurado.")
        else:
            print("Endpoints de webhook:")
            for ep in endpoints:
                print(f"  - {ep}")
        return 0
    if sub == "status":
        endpoints = _load_endpoints(home)
        payload = {"configured": len(endpoints), "ready": True}
        if as_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"Webhook: {payload['configured']} endpoint(s) configurado(s); pronto.")
        return 0
    print("Subcomando inválido. Use: webhook list | webhook status")
    return 1
