"""Comando `kairos telegram` — integração com Telegram."""

from pathlib import Path


async def run_telegram(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "telegram_command", None) or getattr(args, "subcommand", None)

    if subcommand == "config":
        print("Configurando integração com Telegram...")
        print("Por favor, configure o bot token no painel do Telegram.")
        return 0
    if subcommand == "test":
        print("Testando conexão com Telegram...")
        print("Nenhum canal configurado; nada foi enviado.")
        return 0
    print("Subcomando inválido. Use: telegram config | telegram test")
    return 1
