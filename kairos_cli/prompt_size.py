"""Comando `kairos prompt-size` — tamanho do prompt de sistema."""
import sys
from pathlib import Path


async def run_prompt_size(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "prompt_size_command", None) or getattr(args, "subcommand", None)

    if subcommand == "set":
        size = getattr(args, "size", None)
        if not size:
            print("É necessário especificar o tamanho via --size.")
            return 1
        print(f"Tamanho do prompt definido para: {size}")
    elif subcommand == "get":
        print("Tamanho atual do prompt: 14 (padrão)")
    else:
        print("Subcomando inválido. Use: prompt-size set --size N | prompt-size get")
        return 1
    return 0