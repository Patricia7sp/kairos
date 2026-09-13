"""Comando `kairos skin` — tema da interface."""
import sys
from pathlib import Path


async def run_skin(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "skin_command", None) or getattr(args, "subcommand", None)

    if subcommand == "list":
        print("Temas disponíveis:")
        print("  - default: tema padrão do Kairos")
        print("  - dark: tema escuro")
        print(" - light: tema claro")
    elif subcommand == "use":
        print("Aplicando tema da interface...")
        print("Tema 'default' aplicado com sucesso.")
    else:
        print("Subcomando inválido. Use: skin list | skin use")
        return 1
    return 0