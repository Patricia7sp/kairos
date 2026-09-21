"""Comando `kairos prompt-size` — tamanho do prompt de sistema.

**Recusa barulhenta.** Nenhum componente lê `prompt_size`; o endpoint
`/api/ops/prompt-size` que o painel chama também não está registrado. Gravar a
chave sem consumidor é efeito fictício.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_prompt_size(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "prompt_size_command", None) or getattr(args, "subcommand", None)

    if subcommand in ("get", "set"):
        print(
            "kairos: prompt-size não tem efeito neste build: nenhum componente lê "
            "prompt_size no config.yaml, e o endpoint /api/ops/prompt-size do "
            "painel não está registrado.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED
    print("Subcomando inválido. Use: prompt-size set --size N | prompt-size get")
    return 1
