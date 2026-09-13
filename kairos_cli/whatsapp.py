"""Comando `kairos whatsapp` — integração com WhatsApp."""

from __future__ import annotations

import json
from pathlib import Path


def _subcommand(args, stem: str):
    return getattr(args, f"{stem}_command", None) or getattr(args, "subcommand", None)


async def run_whatsapp(home: Path, args) -> int:
    home = Path(home)
    sub = _subcommand(args, "whatsapp")
    as_json = bool(getattr(args, "json", False))

    if sub == "config":
        if as_json:
            print(
                json.dumps(
                    {"configured": False, "hint": "defina o token no painel do provedor"},
                    ensure_ascii=False,
                )
            )
        else:
            print("Configurando integração com WhatsApp...")
            print("Por favor, configure o token no painel do provedor.")
        return 0
    if sub == "test":
        if as_json:
            print(json.dumps({"sent": False, "reason": "not-configured"}, ensure_ascii=False))
        else:
            print("Testando conexão com WhatsApp...")
            print("Nenhum canal configurado; nada foi enviado.")
        return 0
    print("Subcomando inválido. Use: whatsapp config | whatsapp test")
    return 1
