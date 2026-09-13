"""Comando `kairos login` — autentica num provedor e guarda credenciais."""
import json
import sys
import secrets
from pathlib import Path


async def run_login(home: Path, args) -> int:
    home = Path(home)
    provider = getattr(args, "provider", None)
    if not provider:
        print("É necessário especificar um provedor via --provider.", file=sys.stderr)
        return 1

    api_key = getattr(args, "api_key", None)
    if not api_key:
        print("É necessário passar a API key via --api-key.", file=sys.stderr)
        return 1

    from kairos_cli.auth import AuthStore

    store = AuthStore()
    credential = {"key": api_key}
    store.add_credential(provider, credential)
    print(f"Login bem-sucedido para o provedor '{provider}'.")
    return 0