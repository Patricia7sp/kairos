"""Comando `kairos login` — autentica num provedor e guarda credenciais."""

from __future__ import annotations

import sys
from pathlib import Path


async def run_login(home: Path, args) -> int:
    home = Path(home)
    provider = getattr(args, "provider", None)
    if not provider:
        print("É necessário especificar um provedor via --provider.", file=sys.stderr)
        return 1

    api_key = getattr(args, "api_key", None)
    if not api_key:
        if not sys.stdin.isatty():
            print("Informe --api-key ou execute login em um terminal interativo.", file=sys.stderr)
            return 1
        import getpass

        try:
            api_key = getpass.getpass("Chave de API: ")
        except EOFError:
            print("Login cancelado: nenhuma chave informada.", file=sys.stderr)
            return 1

    from kairos_domain.credentials import has_usable_secret

    if not has_usable_secret(api_key):
        print("API key rejeitada: valor placeholder não é segredo utilizável.", file=sys.stderr)
        return 1

    from kairos_providers.composition import build_provider_gateway
    from kairos_providers.provider_registry import UnknownProviderError
    from kairos_security.credentials import PassphraseFileError, VaultError
    from kairos_security.credentials.profile import ProfileCredentialService

    gateway = build_provider_gateway(home)
    try:
        descriptor = gateway.registry.describe(provider)
        if "api_key" not in descriptor.auth_methods:
            print("O provedor não aceita autenticação por API key.", file=sys.stderr)
            return 1
        ProfileCredentialService(home).save_primary(provider, api_key.strip())
    except (UnknownProviderError, PassphraseFileError, VaultError, OSError, ValueError):
        print(
            "Login não concluído: verifique o provedor, os metadados e o estado do cofre.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        raise VaultError("Login não concluído: falha do backend de credenciais.") from exc
    finally:
        await gateway.aclose()
    print(f"Login bem-sucedido para o provedor '{provider}'.")
    return 0
