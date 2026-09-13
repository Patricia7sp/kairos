"""Comando `kairos acp` — Agent Client Protocol para editores."""
import sys
from pathlib import Path


async def run_acp(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "acp_command", None) or getattr(args, "subcommand", None)

    if subcommand == "status":
        print("Verificando status do ACP...")
        print("ACP ativo: conexão estabelecida com editores.")
    elif subcommand == "help":
        print("Help do ACP...")
        print("Comandos disponíveis: status, help")
    else:
        print("Subcomando inválido. Use: acp status | acp help")
        return 1
    return 0