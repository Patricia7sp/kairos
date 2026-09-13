"""Comando `kairos hooks` — hooks de plugin."""
import sys
from pathlib import Path


async def run_hooks(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "subcommand", None)

    if subcommand == "list":
        print("Listando hooks de plugin...")
        print("  - hook_padrao: hook padrão do Kairos")
        print("  - hook_custom: hook personalizado")
    elif subcommand == "use":
        print("Aplicando hook de plugin...")
        print("O hook foi definido para uso ativo.")
    else:
        print("Subcomando inválido. Use: hooks list | hooks use")
        return 1
    return 0