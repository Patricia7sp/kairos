"""Comando `kairos acp` — Agent Client Protocol para editores."""
from pathlib import Path


async def run_acp(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "acp_command", None) or getattr(args, "subcommand", None)

    if subcommand == "status":
        print("ACP: nenhum editor conectado.")
        return 0
    if subcommand == "help":
        print("Comandos disponíveis: status, help")
        return 0
    print("Subcomando inválido. Use: acp status | acp help")
    return 1
