"""Comando `kairos logout` — encerra a autenticação do provedor."""

from __future__ import annotations

import sys
from pathlib import Path


async def run_logout(home: Path, args) -> int:
    home = Path(home)
    provider = getattr(args, "provider", None)

    from kairos_security.credentials import PassphraseFileError, VaultError
    from kairos_security.credentials.profile import ProfileCredentialService

    try:
        removed = ProfileCredentialService(home).logout(provider)
    except (PassphraseFileError, VaultError, OSError, ValueError):
        print("Logout não concluído: verifique os metadados e o estado do cofre.", file=sys.stderr)
        return 1
    except Exception as exc:
        raise VaultError("Logout não concluído: falha do backend de credenciais.") from exc
    if removed == 0:
        print("Nenhuma credencial local permanecia no cofre para esse escopo.")
    elif provider:
        print(f"Logout do provedor '{provider}'.")
    else:
        print("Logout de todos os provedores.")

    return 0
