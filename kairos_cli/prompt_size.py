"""Comando `kairos prompt-size` — tamanho do prompt de sistema."""

from __future__ import annotations

import sys
from pathlib import Path

PADRAO = 14


async def run_prompt_size(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "prompt_size_command", None) or getattr(args, "subcommand", None)

    from kairos_cli.config import load_config, save_config

    if subcommand == "get":
        size = (load_config() or {}).get("prompt_size", PADRAO)
        print(f"Tamanho atual do prompt: {size}")
        return 0
    if subcommand == "set":
        size = getattr(args, "size", None)
        if size is None:
            print("É necessário especificar o tamanho via --size.", file=sys.stderr)
            return 1
        if size <= 0:
            print("--size exige um inteiro positivo.", file=sys.stderr)
            return 1
        config = load_config() or {}
        config["prompt_size"] = size
        save_config(config)
        print(f"Tamanho do prompt definido para: {size}")
        return 0
    print("Subcomando inválido. Use: prompt-size set --size N | prompt-size get")
    return 1
