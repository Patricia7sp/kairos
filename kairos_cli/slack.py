"""Comando `kairos slack` — integração com Slack."""
from pathlib import Path


async def run_slack(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "slack_command", None) or getattr(args, "subcommand", None)

    if subcommand == "config":
        print("Configurando integração com Slack...")
        print("Por favor, configure o webhook URL no painel do Slack.")
        return 0
    if subcommand == "test":
        print("Testando conexão com Slack...")
        print("Nenhum canal configurado; nada foi enviado.")
        return 0
    print("Subcomando inválido. Use: slack config | slack test")
    return 1
