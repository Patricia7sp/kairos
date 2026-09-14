"""Comando `kairos claw` — automação de browser."""
import sys
from pathlib import Path


async def run_claw(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "claw_command", None) or getattr(args, "subcommand", None)

    if subcommand == "start":
        print("kairos: automação de browser indisponível neste modo.", file=sys.stderr)
        return 1
    if subcommand == "status":
        print("Claw: nenhuma tarefa em execução.")
        return 0
    print("Subcomando inválido. Use: claw start | claw status")
    return 1
