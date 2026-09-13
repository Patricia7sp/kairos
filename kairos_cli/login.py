"""Comando `kairos login` — autentica num provedor e guarda credenciais."""

from __future__ import annotations

import json
import sys
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

    from kairos_cli.auth import AuthStore, has_usable_secret

    if not has_usable_secret(api_key):
        print("API key rejeitada: valor placeholder não é segredo utilizável.", file=sys.stderr)
        return 1

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
    store.add_credential(provider, {"key": api_key})
    store.write_atomically(path)
    print(f"Login bem-sucedido para o provedor '{provider}'.")
    return 0
