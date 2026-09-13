"""Comando `kairos uninstall` — desinstala o Kairos."""
import sys
from pathlib import Path


async def run_uninstall(home: Path, args) -> int:
    home = Path(home)

    # Remove o home do Kairos (pasta oculta .kairos na home do usuário)
    kairos_home = Path.home() / ".kairos"
    if kairos_home.exists():
        import shutil
        shutil.rmtree(kairos_home)
        print(f"Kairos desinstalado. Removido {kairos_home}")
    else:
        print("Nenhum home do Kairos encontrado para remover.")

    # Remover token de auth se existir
    auth_token = Path.home() / ".config" / "kairos" / "web-token"
    if auth_token.exists():
        auth_token.unlink()
        print(f"Token removido {auth_token}")

    return 0