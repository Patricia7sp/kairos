"""Comando `kairos peer` — instâncias pares no gateway."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(home: Path) -> list:
    try:
        data = json.loads((home / "peers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(home: Path, peers: list) -> None:
    (home / "peers.json").write_text(
        json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run_peer(home: Path, args) -> int:
    home = Path(home)
    sub = getattr(args, "peer_command", None) or getattr(args, "subcommand", None)

    if sub == "list":
        peers = _load(home)
        if not peers:
            print("Nenhuma instância par registrada.")
        else:
            print("Instâncias pares:")
            for peer in peers:
                print(f"  - {peer}")
        return 0
    if sub in ("add", "remove"):
        target = getattr(args, "target", None) or getattr(args, "peer", None)
        if not target:
            print("Informe a instância via --target.", file=sys.stderr)
            return 1
        peers = _load(home)
        if sub == "add":
            if target in peers:
                print(f"Par '{target}' já registrado.")
            else:
                peers.append(target)
                _save(home, peers)
                print(f"Par '{target}' registrado.")
        else:
            if target not in peers:
                print(f"Par '{target}' não encontrado.")
                return 1
            peers.remove(target)
            _save(home, peers)
            print(f"Par '{target}' removido.")
        return 0
    print("Subcomando inválido. Use: peer list | peer add --target ID | peer remove --target ID")
    return 1
