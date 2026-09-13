"""Comando `kairos pairing` — pareamento de usuário no gateway."""
import sys
from pathlib import Path


async def run_pairing(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "subcommand", None)

    if subcommand == "start":
        print("Iniciando pareamento de usuário no gateway...")
        print("Um código de pareamento foi gerado. Use o gateway para completar.")
    elif subcommand == "status":
        print("Verificando status do pareamento...")
        print("Pareamento ativo: não (nenhum código pendente).")
    else:
        print("Subcomando inválido. Use: pairing start | pairing status")
        return 1
    return 0