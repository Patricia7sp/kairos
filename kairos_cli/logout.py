"""Comando `kairos logout` — encerra a autenticação do provedor."""

from __future__ import annotations

import json
import sys
from pathlib import Path


async def run_logout(home: Path, args) -> int:
    home = Path(home)
    provider = getattr(args, "provider", None)

    from kairos_cli.auth import AuthStore

    path = home / "auth.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        print("auth.json existente está corrompido; abortando.", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print("auth.json existente tem estrutura inválida; abortando.", file=sys.stderr)
        return 1

    store = AuthStore(profile=document.get("credential_pool", {}))

    if provider:
        if provider in store.profile:
            store.profile.pop(provider, None)
            store.write_atomically(path)
            print(f"Logout do provedor '{provider}'.")
        else:
            print(f"Provedor '{provider}' não estava logado.")
    else:
        store.profile.clear()
        store.write_atomically(path)
        print("Logout de todos os provedores.")

    return 0
