"""Comando `kairos gui` — aplicativo desktop."""

import sys
from pathlib import Path


async def run_gui(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "gui_command", None) or getattr(args, "subcommand", None)

    if subcommand == "start":
        print("kairos: aplicativo desktop indisponível neste modo.", file=sys.stderr)
        return 1
    if subcommand == "status":
        print("GUI: não operacional (modo CLI).")
        return 0
    print("Subcomando inválido. Use: gui start | gui status")
    return 1
