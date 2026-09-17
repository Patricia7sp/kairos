"""Comando `kairos webhook` — gestão e estado dos endpoints de webhook.

Os endpoints vivem em `messaging.json` (`kairos_gateway.adapters.config`), a
mesma fonte que o gateway usa para entregar e `GET /api/messaging/webhook/
endpoints` expõe no painel. Webhook não tem segredo: o endpoint é a entrega.
"""

from __future__ import annotations

import json
from pathlib import Path


def _subcommand(args, stem: str):
    return getattr(args, f"{stem}_command", None) or getattr(args, "subcommand", None)


def _emit(data, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif isinstance(data, dict):
        largura = max((len(k) for k in data), default=0)
        for k, v in data.items():
            print(f"{k:<{largura}}  {v}")
    else:
        print(data)


async def run_webhook(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config, webhook_endpoints

    home = Path(home)
    sub = _subcommand(args, "webhook")
    as_json = bool(getattr(args, "json", False))
    doc = load_config(home)["webhook"]
    endpoints = webhook_endpoints(home)

    if sub == "list":
        _emit({"endpoints": endpoints}, as_json=as_json)
        return 0
    if sub == "status":
        _emit(
            {"enabled": bool(doc.get("enabled")), "configured": len(endpoints), "ready": True},
            as_json=as_json,
        )
        return 0
    print("Subcomando inválido. Use: webhook list | webhook status")
    return 1
