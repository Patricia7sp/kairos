"""Comando `kairos skin` — tema da interface."""

from __future__ import annotations

import sys
from pathlib import Path

TEMAS = ("default", "dark", "light")


async def run_skin(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "skin_command", None) or getattr(args, "subcommand", None)

    from kairos_cli.config import load_config, save_config

    if subcommand == "list":
        current = (load_config() or {}).get("ui", {}).get("theme", "system")
        print("Temas disponíveis:")
        for tema in TEMAS:
            marca = " (em uso)" if tema == current else ""
            print(f"  - {tema}{marca}")
        return 0
    if subcommand == "use":
        theme = getattr(args, "theme", None)
        if not theme:
            print("É necessário especificar o tema via --theme.", file=sys.stderr)
            return 1
        if theme not in TEMAS:
            print(f"Tema desconhecido: {theme}. Disponíveis: {', '.join(TEMAS)}", file=sys.stderr)
            return 1
        config = load_config() or {}
        ui = config.get("ui", {})
        ui["theme"] = theme
        config["ui"] = ui
        save_config(config)
        print(f"Tema '{theme}' aplicado.")
        return 0
    print("Subcomando inválido. Use: skin list | skin use --theme NOME")
    return 1
