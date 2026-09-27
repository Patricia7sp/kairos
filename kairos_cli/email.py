"""Comando `kairos email` — configuração não-secreta, estado e teste honesto.

``config``/``status`` operam os campos não-secretos em ``messaging.json``
(`kairos_gateway.adapters.config`); a senha SMTP mora no cofre e é gravada
pela tela de Integrações (ou ``POST /api/messaging/email/credential``) — a CLI
nunca lê nem imprime o segredo. ``test`` conecta de verdade (EHLO) e, com um
destino (ou o alvo home ``address_default``), envia uma mensagem de teste —
provar a autenticação exige enviar, não só conectar.
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

    if platform_has_secret(home, "email"):
        return "salvo no cofre"
    return "ausente — defina em Integrações (web) ou POST /api/messaging/email/credential"


def _apply_config_edits(home: Path, args) -> list[str]:
    from kairos_gateway.adapters.config import load_config, save_config

    changes: list[str] = []
    doc = load_config(home)
    email = doc["email"]
    if getattr(args, "enabled", None) is not None:
        email["enabled"] = args.enabled == "true"
        changes.append("enabled")
    if getattr(args, "smtp_host", None):
        email["smtp_host"] = args.smtp_host
        changes.append("smtp_host")
    if getattr(args, "smtp_port", None) is not None:
        email["smtp_port"] = args.smtp_port
        changes.append("smtp_port")
    if getattr(args, "tls", None) is not None:
        email["tls"] = args.tls == "true"
        changes.append("tls")
    if getattr(args, "from_addr", None):
        email["from_addr"] = args.from_addr
        changes.append("from_addr")
    if getattr(args, "address_default", None):
        email["address_default"] = args.address_default
        changes.append("address_default")
    if getattr(args, "subject_default", None):
        email["subject_default"] = args.subject_default
        changes.append("subject_default")
    if changes:
        save_config(home, doc)
    return changes


def _cmd_config(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    _apply_config_edits(home, args)
    email = load_config(home)["email"]
    _emit(
        {
            "enabled": email["enabled"],
            "smtp_host": email["smtp_host"] or "(vazio)",
            "smtp_port": email["smtp_port"],
            "tls": email["tls"],
            "from_addr": email["from_addr"] or "(vazio)",
            "address_default": email["address_default"] or "(nenhum)",
            "subject_default": email["subject_default"],
            "smtp_password": _secret_summary(home),
        },
        as_json=as_json,
    )
    return 0


def _cmd_status(home: Path, args) -> int:
    from kairos_gateway.adapters import build_platform_adapters
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    email = load_config(home)["email"]
    entregavel = build_platform_adapters(home).get("email") is not None
    _emit(
        {
            "enabled": email["enabled"],
            "entregavel": entregavel,
            "smtp_host": email["smtp_host"] or "(vazio)",
            "smtp_port": email["smtp_port"],
            "tls": email["tls"],
            "from_addr": email["from_addr"] or "(vazio)",
            "address_default": email["address_default"] or "(nenhum)",
            "subject_default": email["subject_default"],
            "smtp_password": _secret_summary(home),
        },
        as_json=as_json,
    )
    return 0


def _cmd_test(home: Path, args) -> int:
    from kairos_gateway.adapters import build_platform_adapters
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    adapter = build_platform_adapters(home).get("email")
    if adapter is None:
        _emit({"ok": False, "motivo": "não habilitado ou sem segredo no cofre"}, as_json=as_json)
        print("nada foi enviado")
        return 1
    destinatario = getattr(args, "destinatario", None)
    if not destinatario:
        destinatario = load_config(home)["email"].get("address_default", "")
    if destinatario:
        resultado = adapter.send(f"email:{destinatario}", TEST_TEXT)
        _emit(
            {
                "ok": resultado.ok,
                "sent": True,
                "message": "teste enviado por SMTP" if resultado.ok else "envio de teste falhou",
                "error_kind": resultado.error_kind,
                "destinatario": destinatario,
            },
            as_json=as_json,
        )
        print("mensagem de teste enviada" if resultado.ok else "envio de teste falhou")
        return 0 if resultado.ok else 1
    resultado = adapter.verify()
    _emit(resultado, as_json=as_json)
    print("nada foi enviado")
    return 0 if resultado.get("ok") else 1


TEST_TEXT = "Kairos — teste de conexão do canal de e-mail."


def run_email(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "email_command", None) or getattr(args, "subcommand", None)

    if subcommand == "config":
        return _cmd_config(home, args)
    if subcommand == "status":
        return _cmd_status(home, args)
    if subcommand == "test":
        return _cmd_test(home, args)
    print(
        "Subcomando inválido. Use: email config | email status | email test [destinatario]",
        file=sys.stderr,
    )
    return 1
