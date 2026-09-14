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

    if not isinstance(data, dict) or "agent" not in data:
        print('Formato inválido. Esperado: {"agent": {config}}')
        return 1

    agent = data["agent"]
    if not isinstance(agent, dict):
        print("Formato inválido. Em 'agent', esperado um objeto de configuração.")
        return 1

    agent_path = home / "agent.json"
    agent_path.write_text(json.dumps(agent, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Configuração do agente importada com sucesso em {agent_path}.")
    return 0
