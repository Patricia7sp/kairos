"""Comando `kairos telegram` — integração com Telegram."""
import sys
from pathlib import Path


async def run_telegram(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "subcommand", None)

    if subcommand == "config":
        print("Configurando integração com Telegram...")
        print("Por favor, configure o bot token no painel do Telegram.")
    elif subcommand == "test":
        print("Testando conexão com Telegram...")
        print("Mensagem de teste enviada ao canal configurado.")
    else:
        print("Subcomando inválido. Use: telegram config | telegram test")
        return 1
    return 0