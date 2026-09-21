"""Comando `kairos hooks` — hooks de plugin.

**Recusa barulhenta.** O sistema real de hooks vive em `kairos_plugins.hooks`
(37 hooks de ciclo de vida) e é alimentado pelo registro dos plugins no
runtime — não existe um `hooks.json` que ele leia. Gravar `{"active": ...}` num
arquivo que ninguém lê é efeito fictício.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_hooks(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "hooks_command", None) or getattr(args, "subcommand", None)

    if subcommand in ("list", "use"):
        print(
            "kairos: hooks não implementado na CLI. Os hooks reais vivem em "
            "kairos_plugins (37 hooks de ciclo de vida) e são registrados pelos "
            "plugins no runtime; não há hooks.json que este comando governe.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: hooks list | hooks use --hook NOME")
    return 1
