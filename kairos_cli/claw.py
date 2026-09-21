"""Comando `kairos claw` — automação de browser.

**Recusa barulhenta.** Não há run-time de browser neste build — não existe
"nenhuma tarefa em execução" verdadeiro, e não há o que iniciar.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_claw(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "claw_command", None) or getattr(args, "subcommand", None)

    if subcommand in ("start", "status"):
        print(
            "kairos: claw (automação de browser) não implementado: não há run-time "
            "de browser neste build para iniciar ou consultar.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: claw start | claw status")
    return 1
