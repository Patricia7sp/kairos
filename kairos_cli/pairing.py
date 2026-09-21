"""Comando `kairos pairing` — pareamento de usuário no gateway.

**Recusa barulhenta.** Não há fluxo de pareamento no gateway deste build;
`pairings.json` não é lido por nada. Escrita sem consumidor é efeito fictício.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_pairing(home: Path, args) -> int:
    home = Path(home)
    sub = getattr(args, "pairing_command", None) or getattr(args, "subcommand", None)

    if sub in ("list", "clear-pending", "revoke"):
        print(
            "kairos: pairing não implementado: o gateway deste build não tem fluxo "
            "de pareamento, e pairings.json não é lido por nada.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print(
        "Subcomando inválido. Use: pairing list | pairing clear-pending | pairing revoke --target ID"
    )
    return 1
