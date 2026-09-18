"""Comando `kairos telegram` — configuração, teste e canal de entrada.

O canal de entrada (``inbound``) recebe mensagens do bot e leva ao agente pelo
mesmo ``InteractionRouter`` das outras superfícies (web/terminal). ``config``
gerencia apenas campos não-secretos em ``messaging.json``; o bot token vive no
cofre e é gravado pela tela de Integrações (ou por
``POST /api/messaging/telegram/credential``).
"""

from __future__ import annotations

import asyncio
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

    if platform_has_secret(home, "telegram"):
        return "salvo no cofre"
    return "ausente — defina em Integrações (web) ou POST /api/messaging/telegram/credential"


def _apply_config_edits(home: Path, args) -> list[str]:
    from kairos_gateway.adapters.config import load_config, save_config

    changes: list[str] = []
    doc = load_config(home)
    tg = doc["telegram"]

    if getattr(args, "enabled", None) is not None:
        tg["enabled"] = args.enabled == "true"
        changes.append("enabled")
    if getattr(args, "inbound_enabled", None) is not None:
        tg["inbound"]["enabled"] = args.inbound_enabled == "true"
        changes.append("inbound.enabled")
    if getattr(args, "allowed_ids", None):
        ids_raw: str = args.allowed_ids
        ids = [int(p.strip()) for p in ids_raw.split(",") if p.strip()]
        tg["inbound"]["allowed_user_ids"] = ids
        changes.append("inbound.allowed_user_ids")
    if getattr(args, "poll_interval", None) is not None:
        tg["inbound"]["poll_interval_seconds"] = float(args.poll_interval)
        changes.append("inbound.poll_interval_seconds")
    if getattr(args, "mode", None) is not None:
        if args.mode not in ("poll", "webhook"):
            raise ValueError("--mode deve ser 'poll' ou 'webhook'")
        tg["inbound"]["mode"] = args.mode
        changes.append("inbound.mode")
    if getattr(args, "chat_default", None) is not None:
        tg["chat_id_default"] = args.chat_default
        changes.append("chat_id_default")
    if changes:
        save_config(home, doc)
    return changes


# ---------------------------------------------------------------------------
# handlers por subcomando


def _cmd_config(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config

    as_json = bool(getattr(args, "json", False))
    _apply_config_edits(home, args)
    tg = load_config(home)["telegram"]
    _emit(
        {
            "enabled": tg["enabled"],
            "chat_id_default": tg["chat_id_default"],
            "webhook_url": tg["webhook_url"] or "(ausente)",
            "inbound.enabled": tg["inbound"]["enabled"],
            "inbound.allowed_user_ids": tg["inbound"]["allowed_user_ids"]
            or "(nenhum — fail-closed)",
            "inbound.poll_interval_seconds": tg["inbound"]["poll_interval_seconds"],
            "inbound.mode": tg["inbound"]["mode"],
            "token": _secret_summary(home),
            "modo": "entrada+saída" if tg["inbound"]["enabled"] else "somente saída",
        },
        as_json=as_json,
    )
    return 0


def _cmd_test(home: Path, args) -> int:
    from kairos_gateway.adapters.config import platform_secret
    from kairos_gateway.inbound import TelegramChannel, TelegramError

    as_json = bool(getattr(args, "json", False))
    token = platform_secret(home, "telegram")
    if not token:
        _emit({"ok": False, "motivo": "token ausente no cofre"}, as_json=as_json)
        print("nada foi enviado")
        return 0
    try:
        channel = TelegramChannel(token)
        info = asyncio.run(channel.verify())
    except TelegramError as exc:
        _emit({"ok": False, "erro": str(exc)}, as_json=as_json)
        print("nada foi enviado")
        return 1
    _emit({"ok": True, **info}, as_json=as_json)
    print("nada foi enviado")
    return 0


def _cmd_status(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config, platform_secret

    as_json = bool(getattr(args, "json", False))
    tg = load_config(home)["telegram"]
    inbound = tg.get("inbound", {})
    enabled = inbound.get("enabled", False)
    allowed = inbound.get("allowed_user_ids", [])

    state_file = home / "inbound-telegram.json"
    consumed = 0
    try:
        consumed = int(json.loads(state_file.read_text(encoding="utf-8")).get("consumed", 0))
    except (FileNotFoundError, OSError, ValueError, KeyError):
        pass

    drain = home / ".drain_request.json"
    segredo_webhook = (
        "salvo no cofre"
        if platform_secret(home, "telegram", field="webhook_secret_token")
        else "ausente — defina via POST /api/messaging/telegram/inbound-secret"
    )
    _emit(
        {
            "inbound.enabled": enabled,
            "autorizados": allowed or "nenhum (fail-closed)",
            "poll_interval_seconds": inbound.get("poll_interval_seconds", 2.0),
            "inbound.mode": inbound.get("mode", "poll"),
            "webhook_url": load_config(home)["telegram"].get("webhook_url") or "(ausente)",
            "webhook_secret_token": segredo_webhook,
            "drain_marker": "presente" if drain.exists() else "ausente",
            "ultimo_update_consumido": consumed,
            "token": _secret_summary(home),
        },
        as_json=as_json,
    )
    return 0


def _cmd_run(home: Path, args) -> int:
    import logging

    from kairos_gateway.inbound import TelegramError, build_telegram_inbound

    as_json = bool(getattr(args, "json", False))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        canal = build_telegram_inbound(home)
    except TelegramError as exc:
        _emit({"erro": str(exc)}, as_json=as_json)
        print(f"kairos telegram run: {exc}", file=sys.stderr)
        return 1
    try:
        asyncio.run(canal.run())
    except KeyboardInterrupt:
        print("\nCanal de entrada Telegram encerrado.")
    except TelegramError as exc:
        _emit({"erro": str(exc)}, as_json=as_json)
        print(f"kairos telegram run: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_stop(home: Path, args) -> int:
    from kairos_gateway.inbound import stop_telegram_inbound

    as_json = bool(getattr(args, "json", False))
    marker = stop_telegram_inbound(home, getattr(args, "reason", "manual"))
    _emit({"drenagem solicitada": str(marker)}, as_json=as_json)
    return 0


def _cmd_webhook(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config, platform_secret, save_config
    from kairos_gateway.inbound import TelegramChannel, TelegramError

    as_json = bool(getattr(args, "json", False))
    url = getattr(args, "url", "").strip()
    if not url.startswith("https://"):
        _emit({"ok": False, "motivo": "URL do webhook deve ser HTTPS público"}, as_json=as_json)
        return 1
    token = platform_secret(home, "telegram")
    if not token:
        _emit({"ok": False, "motivo": "token ausente no cofre"}, as_json=as_json)
        print("nada foi registrado")
        return 1
    secret = platform_secret(home, "telegram", field="webhook_secret_token")
    if not secret:
        _emit(
            {
                "ok": False,
                "motivo": "webhook_secret_token ausente — defina via "
                "POST /api/messaging/telegram/inbound-secret",
            },
            as_json=as_json,
        )
        print("nada foi registrado")
        return 1
    channel = TelegramChannel(token)
    try:
        asyncio.run(channel.set_webhook(url, secret_token=secret))
    except TelegramError as exc:
        _emit({"ok": False, "erro": str(exc)}, as_json=as_json)
        print("nada foi registrado")
        return 1
    doc = load_config(home)
    doc["telegram"]["webhook_url"] = url
    doc["telegram"]["inbound"]["mode"] = "webhook"
    save_config(home, doc)
    _emit(
        {
            "ok": True,
            "webhook": url,
            "inbound.mode": "webhook",
            "aviso": "deixe o servidor web com /api/inbound/telegram aberto (HTTPS) e "
            "long-polling desativado",
        },
        as_json=as_json,
    )
    print("webhook registrado no Telegram")
    return 0


def _cmd_webhook_off(home: Path, args) -> int:
    from kairos_gateway.adapters.config import load_config, platform_secret, save_config
    from kairos_gateway.inbound import TelegramChannel, TelegramError

    as_json = bool(getattr(args, "json", False))
    token = platform_secret(home, "telegram")
    if not token:
        _emit({"ok": False, "motivo": "token ausente no cofre"}, as_json=as_json)
        print("nada foi alterado")
        return 1
    channel = TelegramChannel(token)
    try:
        asyncio.run(channel.delete_webhook(drop_pending=bool(getattr(args, "drop_pending", False))))
    except TelegramError as exc:
        _emit({"ok": False, "erro": str(exc)}, as_json=as_json)
        print("nada foi alterado")
        return 1
    doc = load_config(home)
    doc["telegram"]["inbound"]["mode"] = "poll"
    doc["telegram"]["webhook_url"] = ""
    save_config(home, doc)
    _emit({"ok": True, "inbound.mode": "poll"}, as_json=as_json)
    print("webhook removido; canal volta ao long-poll")
    return 0


# ---------------------------------------------------------------------------
# despacho


async def run_telegram(home: Path, args) -> int:
    home = Path(home)
    subcommand = getattr(args, "telegram_command", None) or getattr(args, "subcommand", None)

    if subcommand == "config":
        return _cmd_config(home, args)
    if subcommand == "test":
        return _cmd_test(home, args)
    if subcommand == "status":
        return _cmd_status(home, args)
    if subcommand == "run":
        return _cmd_run(home, args)
    if subcommand == "stop":
        return _cmd_stop(home, args)
    if subcommand == "webhook":
        return _cmd_webhook(home, args)
    if subcommand == "webhook-off":
        return _cmd_webhook_off(home, args)
    print(
        "Subcomando inválido. Use: telegram config | test | status | run | stop "
        "| webhook <url> | webhook-off",
        file=sys.stderr,
    )
    return 1
