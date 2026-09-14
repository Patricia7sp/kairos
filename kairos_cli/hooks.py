"""Comando `kairos hooks` — hooks de plugin."""

from __future__ import annotations

import json
import sys
from pathlib import Path

CONHECIDOS = ("pre-turn", "post-turn")


def _active(home: Path) -> str | None:
    try:
        data = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("active") if isinstance(data, dict) else None


async def run_hooks(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "hooks_command", None) or getattr(args, "subcommand", None)

    if subcommand == "list":
        active = _active(home)
        print("Hooks de plugin:")
        for hook in CONHECIDOS:
            marca = " (em uso)" if hook == active else ""
            print(f"  - {hook}{marca}")
        return 0
    if subcommand == "use":
        hook = getattr(args, "hook", None)
        if not hook:
            print("É necessário especificar o hook via --hook.", file=sys.stderr)
            return 1
        if hook not in CONHECIDOS:
            print(
                f"Hook desconhecido: {hook}. Conhecidos: {', '.join(CONHECIDOS)}", file=sys.stderr
            )
            return 1
        (home / "hooks.json").write_text(json.dumps({"active": hook}), encoding="utf-8")
        print(f"Hook '{hook}' em uso.")
        return 0
    print("Subcomando inválido. Use: hooks list | hooks use --hook NOME")
    return 1
