"""Comando `kairos peer` — instâncias pares no gateway.

**Recusa barulhenta.** Não há peer no gateway deste build; `peers.json` não é
lido por nada. Escrita sem consumidor é efeito fictício.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_peer(home: Path, args) -> int:
    home = Path(home)
    sub = getattr(args, "peer_command", None) or getattr(args, "subcommand", None)

    if sub in ("list", "add", "remove"):
        print(
            "kairos: peer não implementado: este gateway não tem instâncias pares, "
            "e peers.json não é lido por nada.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: peer list | peer add --target ID | peer remove --target ID")
    return 1
