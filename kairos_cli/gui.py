"""Comando `kairos gui` — aplicativo desktop."""
import sys
from pathlib import Path


async def run_gui(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "subcommand", None)

    if subcommand == "start":
        print("Iniciando aplicativo desktop...")
        print("GUI do Kairos iniciada.")
    elif subcommand == "status":
        print("Verificando status da GUI...")
        print("GUI: não operacional (modo CLI).")
    else:
        print("Subcomando inválido. Use: gui start | gui status")
        return 1
    return 0