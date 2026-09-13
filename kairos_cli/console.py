"""Comando `kairos console` — console interativo."""
import sys
from pathlib import Path


async def run_console(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "subcommand", None)

    if subcommand == "start":
        print("Iniciando console interativo...")
        print("Console aberto. Digite 'help' para comandos.")
    elif subcommand == "eval":
        expr = getattr(args, "expression", None)
        if not expr:
            print("É necessário passar uma expressão via --expression.")
            return 1
        print(f"Resultado: {expr}")
    else:
        print("Subcomando inválido. Use: console start | console eval --expression <expr>")
        return 1
    return 0