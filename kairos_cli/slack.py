"""Comando `kairos slack` — configuração não-secreta, estado e teste honesto.

``config``/``status`` operam os campos não-secretos em ``messaging.json``
(`kairos_gateway.adapters.config`); a URL do incoming webhook mora no cofre e é
gravada pela tela de Integrações (ou ``POST /api/messaging/slack/credential``) —
a CLI nunca lê nem imprime o segredo. ``test`` verifica a **forma** da URL (a
API entrante do Slack não tem verificação sem envio) e nunca alega envio.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _emit(data, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif isinstance(data, dict):
        largura = max((len(k) for k in data), default=0)
        for k, v in data.items():
            print(f"{k:<{largura}}  {v}")
    else:
        print(data)


def _secret_summary(home: Path) -> str:
    from kairos_gateway.adapters.config import platform_has_secret

    if platform_has_secret(home, "slack"):
        return "salvo no cofre"
    return "ausente — defina em Integrações (web) ou POST /api/messaging/slack/credential"


def _apply_config_edits(home: Path, args) -> list[str]:
    from kairos_gateway.adapters.config import load_config, save_config

    changes: list[str] = []
    doc = load_config(home)
    slack = doc["slack"]
    if getattr(args, "enabled", None) is not None:
        slack["enabled"] = args.enabled == "true"
        changes.append("enabled")
    if getattr(args, "channel_default", None) is not None:
        slack["channel_default"] = args.channel_default
        changes.append("channel_default")
    if changes:
        save_config(home, doc)
    return changes


def _cmd_config(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    _apply_config_edits(home, args)
    slack = load_config(home)["slack"]
    _emit(
        {
            "enabled": slack["enabled"],
            "channel_default": slack["channel_default"] or "(nenhum)",
            "webhook_url": _secret_summary(home),
        },
        as_json=as_json,
    )
    return 0


def _cmd_status(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    slack = load_config(home)["slack"]
    _emit(
        {
            "enabled": slack["enabled"],
            "channel_default": slack["channel_default"] or "(nenhum)",
            "webhook_url": _secret_summary(home),
        },
        as_json=as_json,
    )
    return 0


def _cmd_test(home: Path, args) -> int:
    from kairos_gateway.adapters import build_platform_adapters

    as_json = bool(getattr(args, "json", False))
    adapter = build_platform_adapters(home).get("slack")
    if adapter is None:
        _emit({"ok": False, "motivo": "não habilitado ou sem segredo no cofre"}, as_json=as_json)
        print("nada foi enviado")
        return 0
    resultado = adapter.verify()
    _emit(resultado, as_json=as_json)
    print("nada foi enviado")
    return 0 if resultado.get("ok") else 1


async def run_slack(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "slack_command", None) or getattr(args, "subcommand", None)

    if subcommand == "config":
        return _cmd_config(home, args)
    if subcommand == "status":
        return _cmd_status(home, args)
    if subcommand == "test":
        return _cmd_test(home, args)
    print("Subcomando inválido. Use: slack config | slack status | slack test", file=sys.stderr)
    return 1
