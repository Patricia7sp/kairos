"""Seleção explícita e catálogo canônico pela CLI."""

from __future__ import annotations

import sys
from pathlib import Path

from kairos_providers import CatalogOrigin, ProviderError, ProviderModelRef
from kairos_providers.catalog import UnknownModelError
from kairos_providers.composition import build_provider_gateway
from kairos_providers.provider_registry import UnknownProviderError
from kairos_providers.settings import load_config_document, update_config_document
from kairos_security.credentials import VaultError


def _selection(config):
    return {
        key: value if isinstance(value := config.get(key), str) and value.strip() else None
        for key in ("provider", "model")
    }


async def run_model_command(home: Path, args) -> int:
    from kairos_cli.handlers import _emit

    gateway = None
    try:
        command = args.model_command or "show"
        if command == "show":
            _emit(_selection(load_config_document(home)), as_json=args.json)
            return 0
        gateway = build_provider_gateway(home)
        provider = getattr(args, "provider", None)
        if provider:
            gateway.registry.describe(provider)
        if command == "list":
            models = [
                {
                    "provider": m.ref.provider,
                    "model": m.ref.model,
                    "name": m.display_name,
                    "free": m.is_free,
                    "tools": m.capabilities.tools,
                    "vision": m.capabilities.vision,
                    "streaming": m.capabilities.streaming,
                }
                for m in gateway.catalog.list_models(provider=provider)
                if not args.free or m.is_free
            ]
            _emit({"models": models, "source": "local_catalog"}, as_json=args.json)
        elif command == "refresh":
            models = await gateway.refresh(provider)
            _emit(
                {"provider": provider, "models": len(models.models), "source": models.source},
                as_json=args.json,
            )
            return 0 if models.source == CatalogOrigin.DYNAMIC else 1
        elif command == "test":
            status = await gateway.test_connection(provider)
            _emit(
                {
                    "provider": provider,
                    "ok": status.ok,
                    "message": status.message,
                    "scope": "connection_only",
                },
                as_json=args.json,
            )
            return 0 if status.ok else 1
        elif command == "set":
            model = gateway.catalog.find(ProviderModelRef(provider, args.model))
            if not model.is_selectable():
                raise UnknownModelError(args.model)

            def select(config):
                config.update(provider=provider, model=args.model)

            saved = update_config_document(home, select)
            _emit(_selection(saved), as_json=args.json)
        return 0
    except (UnknownModelError, UnknownProviderError):
        print(
            "kairos: provedor ou modelo ausente do catálogo; use model refresh PROVEDOR.",
            file=sys.stderr,
        )
        return 1
    except ProviderError as exc:
        print(f"kairos: {exc.message}", file=sys.stderr)
        return 1
    except (OSError, ValueError, VaultError):
        print("kairos: catálogo, configuração ou credenciais indisponíveis.", file=sys.stderr)
        return 1
    finally:
        if gateway is not None:
            await gateway.aclose()
