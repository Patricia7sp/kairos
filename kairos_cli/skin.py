"""Comando `kairos skin` — tema da interface.

**Recusa barulhenta.** Nenhuma interface deste build lê `ui.theme` do
`config.yaml` (a web usa CSS próprio). Gravar uma chave sem consumidor é efeito
fictício.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_skin(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "skin_command", None) or getattr(args, "subcommand", None)

    if subcommand in ("list", "use"):
        print(
            "kairos: skin não tem efeito neste build: nenhuma interface lê a chave "
            "ui.theme do config.yaml (a web usa CSS próprio no frontend).",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: skin list | skin use --theme NOME")
    return 1
