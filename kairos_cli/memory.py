"""Comando `kairos memory` — memória de longo prazo (toolset `memory`)."""

from pathlib import Path

from kairos_tools.memory import MemoryStore


async def run_memory(home: Path, args) -> int:
    """Executor real do toolset `memory`.

    `status` lê os arquivos do store (MEMORY.md/USER.md); `off` limpa o estado
    persistente (remove os dois arquivos). O comando deixa de ser placeholder:
    ele fala com o mesmo store que o restante do Kairos usa.
    """
    store = MemoryStore(home=home)

    subcommand = getattr(args, "memory_command", None) or getattr(args, "subcommand", None)

    if subcommand == "off":
        store.clear()
        print(
            "Memória de longo prazo limpa "
            f"(removidos {store.mem_dir / 'MEMORY.md'} e {store.mem_dir / 'USER.md'})."
        )
    elif subcommand == "status":
        for target in ("memory", "user"):
            result = store.list(target)
            if not result.get("success"):
                print(f"[{target}] {result.get('error', 'erro de leitura')}")
                return 1
            entries = result.get("entries", [])
            print(f"{target.upper()}: {result.get('usage')} — {len(entries)} entrada(s)")
            if entries:
                for i, entry in enumerate(entries, 1):
                    print(f"  {i}. {entry}")
    else:
        print("Subcomando inválido. Use: memory off | memory status")
        return 1
    return 0
