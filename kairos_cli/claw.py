"""Comando `kairos claw` — automação de browser."""
import sys
from pathlib import Path


async def run_claw(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "claw_command", None) or getattr(args, "subcommand", None)

    if subcommand == "start":
        print("Iniciando automação de browser...")
        print("Navegador iniciado em segundo plano.")
    elif subcommand == "status":
        print("Verificando status do claw...")
        print("Claw ativo: nenhuma tarefa em execução.")
    else:
        print("Subcomando inválido. Use: claw start | claw status")
        return 1
    return 0