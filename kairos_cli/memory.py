"""Comando `kairos memory` — memória de longo prazo."""
import json
import sys
from pathlib import Path


async def run_memory(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "memory_command", None) or getattr(args, "subcommand", None)

    if subcommand == "off":
        # Desativa a memória (por exemplo, limpa estado persistente)
        memory_path = home / "memory.json"
        if memory_path.exists():
            memory_path.unlink()
        print("Memória de longo prazo desativada.")
    elif subcommand == "status":
        memory_path = home / "memory.json"
        if memory_path.exists():
            data = json.loads(memory_path.read_text(encoding="utf-8"))
            print(f"Memória ativa: {data}")
        else:
            print("Memória de longo prazo desativada (nenhum arquivo de memória).")
    else:
        print("Subcomando inválido. Use: memory off | memory status")
        return 1
    return 0