"""Comando `kairos whatsapp` — configuração não-secreta, estado e teste honesto.

``config``/``status`` operam os campos não-secretos em ``messaging.json``
(`kairos_gateway.adapters.config`); o token de acesso mora no cofre e é gravado
pela tela de Integrações (ou ``POST /api/messaging/whatsapp/credential``) — a
CLI nunca lê nem imprime o segredo. ``test`` confirma telefone e token pela API
Cloud **sem despachar mensagem** e nunca alega envio.

Entrada (polling/webhook de recebimento) ainda não existe.
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

    if platform_has_secret(home, "whatsapp"):
        return "salvo no cofre"
    return "ausente — defina em Integrações (web) ou POST /api/messaging/whatsapp/credential"


def _apply_config_edits(home: Path, args) -> list[str]:
    from kairos_gateway.adapters.config import load_config, save_config

    changes: list[str] = []
    doc = load_config(home)
    whatsapp = doc["whatsapp"]
    if getattr(args, "enabled", None) is not None:
        whatsapp["enabled"] = args.enabled == "true"
        changes.append("enabled")
    if getattr(args, "phone_number_id", None) is not None:
        whatsapp["phone_number_id"] = args.phone_number_id
        changes.append("phone_number_id")
    if getattr(args, "number_default", None) is not None:
        whatsapp["number_default"] = args.number_default
        changes.append("number_default")
    if changes:
        save_config(home, doc)
    return changes


def _cmd_config(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    _apply_config_edits(home, args)
    whatsapp = load_config(home)["whatsapp"]
    _emit(
        {
            "enabled": whatsapp["enabled"],
            "phone_number_id": whatsapp["phone_number_id"] or "(vazio)",
            "number_default": whatsapp["number_default"] or "(nenhum)",
            "token": _secret_summary(home),
        },
        as_json=as_json,
    )
    return 0


def _cmd_status(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    whatsapp = load_config(home)["whatsapp"]
    _emit(
        {
            "enabled": whatsapp["enabled"],
            "phone_number_id": whatsapp["phone_number_id"] or "(vazio)",
            "number_default": whatsapp["number_default"] or "(nenhum)",
            "token": _secret_summary(home),
        },
        as_json=as_json,
    )
    return 0


def _cmd_test(home: Path, args) -> int:
    from kairos_gateway.adapters import build_platform_adapters

    as_json = bool(getattr(args, "json", False))
    adapter = build_platform_adapters(home).get("whatsapp")
    if adapter is None:
        _emit({"ok": False, "motivo": "não habilitado ou sem segredo no cofre"}, as_json=as_json)
        print("nada foi enviado")
        return 0
    resultado = adapter.verify()
    _emit(resultado, as_json=as_json)
    print("nada foi enviado")
    return 0 if resultado.get("ok") else 1


async def run_whatsapp(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "whatsapp_command", None) or getattr(args, "subcommand", None)

    if subcommand == "config":
        return _cmd_config(home, args)
    if subcommand == "status":
        return _cmd_status(home, args)
    if subcommand == "test":
        return _cmd_test(home, args)
    print(
        "Subcomando inválido. Use: whatsapp config | whatsapp status | whatsapp test",
        file=sys.stderr,
    )
    return 1
