"""Comando `kairos verify` — verificação pós-edição de código."""

import json
from pathlib import Path


async def run_verify(home: Path, args) -> int:
    home = Path(home)

    # Verifica integridade do auth.json e configuração
    auth_path = home / "auth.json"
    config_path = Path.home() / ".config" / "kairos" / "config.yaml"

    errors = []

    if not auth_path.exists():
        errors.append("auth.json não encontrado")
    else:
        try:
            data = json.loads(auth_path.read_text(encoding="utf-8"))
            if "credential_pool" not in data:
                errors.append("auth.json com estrutura inválida")
        except (json.JSONDecodeError, ValueError):
            errors.append("auth.json inválido (JSON corrompido)")

    if not config_path.exists():
        errors.append("config.yaml não encontrado")

    if errors:
        print("Erros de verificação encontrados:")
        for e in errors:
            print(f"  - {e}")
        return 1
    else:
        print("Verificação concluída: não foram encontrados problemas.")
        return 0
