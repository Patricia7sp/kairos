"""Comando `kairos logout` — encerra a autenticação do provedor."""
import sys
from pathlib import Path


async def run_logout(home: Path, args) -> int:
    home = Path(home)
    provider = getattr(args, "provider", None)

    from kairos_cli.auth import AuthStore

    store = AuthStore()

    if provider:
        if provider in store.profile:
            store.profile.pop(provider, None)
            print(f"Logout do provedor '{provider}'.")
        else:
            print(f"Provedor '{provider}' não estava logado.")
    else:
        store.profile.clear()
        print("Logout de todos os provedores.")

    return 0