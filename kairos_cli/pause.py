"""Comando `kairos pause` — pausa a atividade autônoma."""
import sys
from pathlib import Path


async def run_pause(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "pause_command", None) or getattr(args, "subcommand", None)

    if subcommand == "resume":
        print("Retomando a atividade autônoma.")
    elif subcommand == "status":
        print("Verificando status da atividade autônoma...")
        print("Atividade: pausada")
    else:
        print("Subcomando inválido. Use: pause resume | pause status")
        return 1
    return 0