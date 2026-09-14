"""Comando `kairos uninstall` — desinstala o Kairos."""

from pathlib import Path


async def run_uninstall(home: Path, args) -> int:
    home = Path(home)

    if home.exists():
        import shutil

        shutil.rmtree(home)
        print(f"Kairos desinstalado. Removido {home}")
        return 0

    print("Kairos não encontrado: nada a desinstalar.")
    return 1
