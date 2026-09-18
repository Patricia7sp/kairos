"""Adapters de plataforma da mensageria: Telegram, WhatsApp, Slack e Webhook.

Cada adapter implementa o `PlatformAdapter` do gateway (`name` + `send`) e
expõe `verify()` para o painel distinguir "configurado" de "respondendo".

A fábrica `build_platform_adapters` é a única fonte do que está entregável: uma
plataforma só entra no resultado quando está habilitada e tem o que a entrega
pede (segredo no cofre). É deliberado — o gateway recusa em vez de fingir que
entrega — e é ela que o painel consulta para dizer o estado real.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kairos_gateway.adapters.config import (
    SECRET_KEYS,
    endpoints,
    load_config,
    platform_has_secret,
    platform_secret,
    platform_secret_fields,
    vault_state,
)
from kairos_gateway.adapters.slack import SlackAdapter
from kairos_gateway.adapters.telegram import TelegramAdapter
from kairos_gateway.adapters.webhook import WebhookAdapter
from kairos_gateway.adapters.whatsapp import WhatsAppAdapter
from kairos_gateway.service import PlatformAdapter

__all__ = [
    "PLATFORMS",
    "build_platform_adapters",
    "messaging_platforms",
    "messaging_status",
]


@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    secret: bool = False


@dataclass(frozen=True)
class Platform:
    name: str
    label: str
    needs_secret: bool
    credential_label: str
    credential_hint: str
    fields: tuple[ConfigField, ...]


PLATFORMS: tuple[Platform, ...] = (
    Platform(
        name="telegram",
        label="Telegram",
        needs_secret=True,
        credential_label="Token do bot",
        credential_hint="Token do BotFather (…:AAF…), para enviar pelo Bot API.",
        fields=(ConfigField("chat_id_default", "Chat ID padrão dele"),),
    ),
    Platform(
        name="whatsapp",
        label="WhatsApp",
        needs_secret=True,
        credential_label="Token de acesso (Meta)",
        credential_hint="Token permanente da Meta Cloud API com permissão de WhatsApp.",
        fields=(
            ConfigField("phone_number_id", "Phone Number ID"),
            ConfigField("number_default", "Número padrão (E.164)"),
        ),
    ),
    Platform(
        name="slack",
        label="Slack",
        needs_secret=True,
        credential_label="Webhook URL entrante",
        credential_hint="Incoming Webhook criado num canal do seu workspace.",
        fields=(ConfigField("channel_default", "Canal padrão (#canal)"),),
    ),
    Platform(
        name="webhook",
        label="Webhook genérico",
        needs_secret=False,
        credential_label="",
        credential_hint="",
        fields=(),
    ),
)


def build_platform_adapters(home: Path) -> dict[str, PlatformAdapter]:
    """Os adapters entregáveis agora — só plataforma habilitada e com segredo.

    Segredo do cofre bloqueado ou ausente não constrói o adapter: o painel
    informa o que falta, e o gateway nunca entrega em silêncio.
    """
    doc = load_config(home)
    adapters: dict[str, PlatformAdapter] = {}

    telegram = doc.get("telegram", {})
    if telegram.get("enabled") and (token := platform_secret(home, "telegram")):
        adapters["telegram"] = TelegramAdapter(
            token, chat_id_default=telegram.get("chat_id_default", "")
        )

    whatsapp = doc.get("whatsapp", {})
    if whatsapp.get("enabled") and (token := platform_secret(home, "whatsapp")):
        phone_id = whatsapp.get("phone_number_id", "")
        if phone_id:
            adapters["whatsapp"] = WhatsAppAdapter(token, phone_id)

    slack = doc.get("slack", {})
    if slack.get("enabled") and (webhook_url := platform_secret(home, "slack")):
        adapters["slack"] = SlackAdapter(
            webhook_url, channel_default=slack.get("channel_default", "")
        )

    web = doc.get("webhook", {})
    if web.get("enabled") and (mapa := endpoints(home)):
        adapters["webhook"] = WebhookAdapter(mapa)

    return adapters


def messaging_platforms(home: Path) -> list[dict[str, Any]]:
    """Estado por plataforma, sem segredo e sem chamada de rede.

    `configured` reflete a presença do segredo no cofre; a conexão real só a
    ação de teste confirma — a tela distingue "configurado" de "respondendo".
    """
    doc = load_config(home)
    vault = vault_state(home)
    result: list[dict[str, Any]] = []
    for definition in PLATFORMS:
        conf = doc.get(definition.name, {})
        enabled = bool(conf.get("enabled"))
        tem_segredo = platform_has_secret(home, definition.name)
        needs = definition.needs_secret
        deliverable = enabled and (tem_segredo or not needs)
        inbound = conf.get("inbound")
        result.append(
            {
                "platform": definition.name,
                "label": definition.label,
                "enabled": enabled,
                "configured": tem_segredo,
                "deliverable": deliverable,
                "secreto_campo": definition.credential_label,
                "secreto_dica": definition.credential_hint,
                "campos": [{"key": field.key, "label": field.label} for field in definition.fields],
                "valores": {field.key: conf.get(field.key) for field in definition.fields},
                "inbound": {
                    "enabled": bool(inbound.get("enabled", False)),
                    "experiences": bool(inbound.get("experiences", True)),
                    **({"mode": inbound["mode"]} if "mode" in inbound else {}),
                    "campo_secreto": {
                        campo: presente
                        for campo, presente in platform_secret_fields(home, definition.name).items()
                        if campo != SECRET_KEYS[definition.name]
                    },
                }
                if isinstance(inbound, dict)
                else None,
                "vault": vault,
            }
        )
    return result


def messaging_status(home: Path) -> dict[str, Any]:
    """Resumo para o painel e para o header: entregáveis + obrigações no ledger."""
    from kairos_state import CJKExtensionUnavailable, connect
    from kairos_state.migrations import migrate
    from kairos_state.repositories.ledger import LedgerRepository

    adapters = build_platform_adapters(home)
    counts = {"pending": 0, "claimed": 0, "delivered": 0, "abandoned": 0}
    try:
        db = connect(home / "state.db")
        try:
            migrate(db)
            counts = LedgerRepository(db).counts_by_state()
        finally:
            db.close()
    except (sqlite3.Error, CJKExtensionUnavailable, OSError):
        counts = {}
    return {
        "adapters": sorted(adapters),
        "obligations": counts,
        "platforms": messaging_platforms(home),
    }
