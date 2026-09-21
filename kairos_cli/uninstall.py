"""Comando `kairos uninstall` — desinstala o Kairos.

**Fail-closed.** Apagar `$KAIROS_HOME` é destrutivo e irreversível: sem `--yes`;
não remove nada. Dentro de container, recusa — o home é volume da stack e o
serviço é gerido pelo pipeline, não por um comando do próprio container.
"""

import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode


def _remove(home: Path) -> None:
    import shutil

    shutil.rmtree(home)


async def run_uninstall(home: Path, args) -> int:
    home = Path(home)

    from kairos_container import in_container

    if in_container():
        print(
            "kairos: uninstall recusado: este processo roda dentro de um container "
            "e o home é gerenciado pela stack. A desinstalação é decisão de "
            "operação, tomada fora dele.",
            file=sys.stderr,
        )
        return ExitCode.ERROR

    if not getattr(args, "yes", False):
        if home.exists():
            print(f"Removeria: {home} (incluindo state.db, auth.json e habilidades)")
            print(
                "confirmação necessária: repita com --yes.",
                file=sys.stderr,
            )
        else:
            print("Kairos não encontrado: nada a desinstalar.")
            print(
                "confirmação necessária: repita com --yes se quiser checar de novo.",
                file=sys.stderr,
            )
        return ExitCode.ERROR

    if home.exists():
        _remove(home)
        print(f"Kairos desinstalado. Removido {home}")
        return ExitCode.OK

    print("Kairos não encontrado: nada a desinstalar.")
    return ExitCode.ERROR
