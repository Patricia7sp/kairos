"""Comando `kairos update` — atualiza o Kairos.

**Recusa barulhenta.** Não há mecanismo de auto-atualização neste build. Na
imagem, o deploy vem do pipeline do Komodo (docs/ci-cd-komodo.md); em dev, a
atualização é pelo git refazendo o venv.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


async def run_update(home: Path, args) -> int:
    home = Path(home)

    from kairos_cli.startup_fast import fast_version_line

    print(f"Kairos versão atual: {fast_version_line()}")
    print(
        "kairos: update não implementado para auto-atualização. Na imagem de "
        "produção o deploy vem do pipeline do Komodo (docs/ci-cd-komodo.md); em "
        "dev, atualize pelo git e refaça o venv.",
        file=sys.stderr,
    )
    return ExitCode.NOT_IMPLEMENTED
