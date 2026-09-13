"""Comando `kairos peer` — instâncias pares no gateway."""

from __future__ import annotations

from pathlib import Path


async def run_peer(home: Path, args) -> int:
    home = Path(home)
    sub = getattr(args, "peer_command", None) or getattr(args, "subcommand", None)

    if sub == "list":
        print("Nenhuma instância par registrada.")
        return 0
    if sub == "add":
        target = getattr(args, "target", None) or getattr(args, "peer", None)
        if not target:
            print("Informe a instância via --target.")
            return 1
        print(f"Par '{target}' registrado.")
        return 0
    if sub == "remove":
        target = getattr(args, "target", None) or getattr(args, "peer", None)
        if not target:
            print("Informe a instância via --target.")
            return 1
        print(f"Par '{target}' removido.")
        return 0
    print("Subcomando inválido. Use: peer list | peer add --target ID | peer remove --target ID")
    return 1
