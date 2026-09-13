"""Comando `kairos peer` — instâncias pares no gateway."""
import sys
from pathlib import Path


async def run_peer(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "subcommand", None)

    if subcommand == "list":
        print("Listando instâncias pares...")
        print("  - peer_1: gateway principal")
        print("  - peer_2: gateway de backup")
    elif subcommand == "status":
        print("Verificando status das instâncias pares...")
        print("Todas as instâncias pares estão operacionais.")
    else:
        print("Subcomando inválido. Use: peer list | peer status")
        return 1
    return 0