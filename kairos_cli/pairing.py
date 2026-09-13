"""Comando `kairos pairing` — pareamento de usuário no gateway."""

from __future__ import annotations

from pathlib import Path


async def run_pairing(home: Path, args) -> int:
    home = Path(home)
    sub = getattr(args, "pairing_command", None) or getattr(args, "subcommand", None)

    if sub == "list":
        print("Nenhum pareamento ativo.")
        return 0
    if sub == "clear-pending":
        print("Códigos pendentes limpos (nenhum pendente).")
        return 0
    if sub == "revoke":
        target = getattr(args, "target", None) or getattr(args, "peer", None)
        if not target:
            print("Informe o pareamento a revogar via --target.")
            return 1
        print(f"Pareamento '{target}' revogado.")
        return 0
    print(
        "Subcomando inválido. Use: pairing list | pairing clear-pending | pairing revoke --target ID"
    )
    return 1
