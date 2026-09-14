"""Comando `kairos pause` — pausa a atividade autônoma."""

from __future__ import annotations

import json
from pathlib import Path


def _state_path(home: Path) -> Path:
    return home / "autonomy.json"


def _paused(home: Path) -> bool:
    try:
        return bool(json.loads(_state_path(home).read_text(encoding="utf-8")).get("paused", False))
    except (OSError, ValueError, AttributeError):
        return False


async def run_pause(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "pause_command", None) or getattr(args, "subcommand", None)

    if subcommand == "resume":
        _state_path(home).write_text(json.dumps({"paused": False}), encoding="utf-8")
        print("Atividade autônoma retomada.")
        return 0
    if subcommand == "status":
        print(f"Atividade autônoma: {'pausada' if _paused(home) else 'ativa'}.")
        return 0
    if subcommand is None:
        _state_path(home).write_text(json.dumps({"paused": True}), encoding="utf-8")
        print("Atividade autônoma pausada.")
        return 0
    print("Subcomando inválido. Use: pause | pause resume | pause status")
    return 1
