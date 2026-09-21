"""Comando `kairos pause` — pausa a atividade autônoma.

**Recusa barulhenta.** Não há consumidor de `autonomy.json` neste build — nada
lê ou respeita esse estado. "Pausar/retomar" sem uma fonte de atividade
autônoma é efeito fictício.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_pause(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "pause_command", None) or getattr(args, "subcommand", None)

    if subcommand in ("resume", "status") or subcommand is None:
        print(
            "kairos: pause não tem efeito neste build: não há consumidor de "
            "autonomy.json, e não há atividade autônoma a pausar ou retomar.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: pause | pause resume | pause status")
    return 1
