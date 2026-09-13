"""Comando `kairos import-agent` — importa configuração de outro agente."""
import json
import sys
from pathlib import Path


async def run_import_agent(home: Path, args) -> int:
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

    if isinstance(data, dict) and "agent" in data:
        print("Configuração do agente importada com sucesso.")
    else:
        print("Formato inválido. Esperado: {\"agent\": {config}}")
        return 1
    return 0