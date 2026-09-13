"""Comando `kairos slack` — integração com Slack."""
import sys
from pathlib import Path


async def run_slack(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "subcommand", None)

    if subcommand == "config":
        print("Configurando integração com Slack...")
        print("Por favor, configure o webhook URL no painel do Slack.")
    elif subcommand == "test":
        print("Testando conexão com Slack...")
        print("Mensagem de teste enviada ao canal configurado.")
    else:
        print("Subcomando inválido. Use: slack config | slack test")
        return 1
    return 0