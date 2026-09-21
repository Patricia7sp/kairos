"""Comando `kairos acp` — Agent Client Protocol para editores.

**Recusa barulhenta no `status`.** O adapter de servidor ACP do legado
(Tarefa 17) não foi portado: `kairos_acp` contém só as primitivas de protocolo
(approval/session). Não há servidor rodando para "nenhum editor conectado" ser
um estado verdadeiro.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_acp(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "acp_command", None) or getattr(args, "subcommand", None)

    if subcommand == "status":
        print(
            "kairos: acp status não implementado: não há servidor ACP neste build. "
            "kairos_acp só tem as primitivas de protocolo (approval/session); o "
            "adapter de servidor do legado não foi portado.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    if subcommand == "help":
        print("Protocolo Agent Client Protocol para editores.")
        print("Subcomandos: status (não implementado), help.")
        return 0
    print("Subcomando inválido. Use: acp status | acp help")
    return 1
