"""Comando `kairos pairing` — pareamento de usuário no gateway."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(home: Path) -> dict:
    try:
        data = json.loads((home / "pairings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"active": [], "pending": []}
    if not isinstance(data, dict):
        return {"active": [], "pending": []}
    return {"active": data.get("active", []), "pending": data.get("pending", [])}


def _save(home: Path, data: dict) -> None:
    (home / "pairings.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run_pairing(home: Path, args) -> int:
    home = Path(home)
    sub = getattr(args, "pairing_command", None) or getattr(args, "subcommand", None)

    if sub == "list":
        data = _load(home)
        if not data["active"]:
            print("Nenhum pareamento ativo.")
        else:
            print("Pareamentos ativos:")
            for peer in data["active"]:
                print(f"  - {peer}")
        return 0
    if sub == "clear-pending":
        data = _load(home)
        data["pending"] = []
        _save(home, data)
        print("Códigos pendentes limpos.")
        return 0
    if sub == "revoke":
        target = getattr(args, "target", None) or getattr(args, "peer", None)
        if not target:
            print("Informe o pareamento a revogar via --target.", file=sys.stderr)
            return 1
        data = _load(home)
        if target not in data["active"]:
            print(f"Pareamento '{target}' não encontrado.")
            return 1
        data["active"].remove(target)
        _save(home, data)
        print(f"Pareamento '{target}' revogado.")
        return 0
    print(
        "Subcomando inválido. Use: pairing list | pairing clear-pending | pairing revoke --target ID"
    )
    return 1
