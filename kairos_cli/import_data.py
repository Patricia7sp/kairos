"""Comando `kairos import` — importa sessões e dados."""

import json
import sys
from pathlib import Path


async def run_import(home: Path, args) -> int:
    home = Path(home)
    data_str = getattr(args, "data", None)
    if not data_str:
        print("É necessário passar os dados via --data JSON.", file=sys.stderr)
        return 1

    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        print("Dados inválidos: esperado um JSON.", file=sys.stderr)
        return 1

    if isinstance(data, dict) and "credentials" in data:
        from kairos_cli.auth import AuthStore, has_usable_secret

        creds = data["credentials"]
        if not isinstance(creds, dict):
            print("Formato inválido: 'credentials' deve ser um objeto.", file=sys.stderr)
            return 1
        provider = creds.pop("provider", "imported")
        key = creds.get("key") or creds.get("api_key")
        if not has_usable_secret(key or ""):
            print("Credencial rejeitada: sem segredo utilizável.", file=sys.stderr)
            return 1
        try:
            document = json.loads((home / "auth.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = {}
        if not isinstance(document, dict):
            document = {}
        store = AuthStore(profile=document.get("credential_pool", {}))
        store.add_credential(provider, {"key": key})
        store.write_atomically(home / "auth.json")
        print("Credenciais importadas com sucesso.")
    elif isinstance(data, dict) and "config" in data:
        from kairos_cli.config import load_config, save_config

        config = load_config() or {}
        config.update(data["config"])
        save_config(config)
        print("Configuração importada com sucesso.")
    else:
        print('Formato de dados não reconhecido. Esperado: {"credentials": ...} ou {"config": ...}')
        return 1
    return 0
