"""Comando `kairos gui` — aplicativo desktop.

**Recusa barulhenta.** Não há aplicativo desktop neste build — "não operacional
(modo CLI)" não é um estado real, é ausência de subsistema.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_gui(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "gui_command", None) or getattr(args, "subcommand", None)

    if subcommand in ("start", "status"):
        print(
            "kairos: gui (aplicativo desktop) não implementado: não há processo de "
            "interface desktop neste build para iniciar ou consultar.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: gui start | gui status")
    return 1
