"""Configuração da mensageria e resolução de segredos no cofre.

Os segredos (bot token, access token, webhook URL do Slack) moram no cofre de
credenciais — nunca em `messaging.json`. O arquivo guarda apenas o que não é
secreto (endpoints, valores padrão), para que uma cópia dele não exponha
material que o cofre existe para proteger.

O documento aceito é estrito e `fail-closed`: plataforma desconhecida ou campo
fora do esquema é erro, não campo ignorado — um campo ignorado em silêncio é
uma configuração que o operador acredita valer e o sistema não aplica.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kairos_security.credentials import (
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    VaultError,
    VaultState,
    build_credential_service,
)
from kairos_security.credentials.io import secure_atomic_write_text

MESSAGING_FILE = "messaging.json"

#: Chave dentro do `CredentialSecret` que guarda o segredo de cada plataforma.
SECRET_KEYS: dict[str, str] = {
    "telegram": "api_key",
    "whatsapp": "access_token",
    "slack": "api_url",
    "webhook": "",
}

_PLATFORMS: tuple[str, ...] = ("telegram", "whatsapp", "slack", "webhook")

#: Campos não-secretos aceitos por plataforma. Chave -> tipo Python.
_FIELDS: dict[str, dict[str, type]] = {
    "telegram": {"enabled": bool, "chat_id_default": str, "inbound": dict},
    "whatsapp": {"enabled": bool, "phone_number_id": str, "number_default": str},
    "slack": {"enabled": bool, "channel_default": str},
    "webhook": {"enabled": bool, "endpoints": list},
}

#: Campos opcionais com padrão — ausência não é erro; o padrão entra no merge.
_OPTIONAL_FIELDS: dict[str, frozenset[str]] = {"telegram": frozenset({"inbound"})}

#: Esquema do canal de entrada (bloco ``inbound`` do Telegram).
#:
#: ``allowed_user_ids`` vazio significa **ninguém autorizado** (fail-closed):
#: o canal de entrada só responde a remetentes vistos aqui.
_INBOUND_FIELDS: dict[str, tuple[type | tuple[type, ...], bool]] = {
    "enabled": (bool, False),
    "allowed_user_ids": (list, False),
    "poll_interval_seconds": ((int, float), True),
    "experiences": (bool, False),
}

#: Campos do inbound cuja ausência é aceita — o padrão entra silenciosamente.
#: ``experiences`` liga a injeção de experiências ativas por turno no canal; a
#: ausência da chave herda o padrão ligado (desligue explicitamente com ``false``).
_INBOUND_OPTIONAL_DEFAULTS: dict[str, Any] = {"experiences": True}


def _valid_inbound(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("configuração de inbound deve ser um objeto")
    validated: dict[str, Any] = {}
    for campo, (tipo, rango) in _INBOUND_FIELDS.items():
        if campo not in value:
            if campo in _INBOUND_OPTIONAL_DEFAULTS:
                validated[campo] = _INBOUND_OPTIONAL_DEFAULTS[campo]
                continue
            raise ValueError(f"configuração de inbound exige a chave '{campo}'")
        valor = value[campo]
        if tipo is bool:
            if not isinstance(valor, bool):
                raise ValueError(f"'{campo}' de inbound deve ser booleano")
        elif tipo is list:
            if not (
                isinstance(valor, list)
                and all(isinstance(i, int) and not isinstance(i, bool) for i in valor)
            ):
                raise ValueError(f"'{campo}' de inbound deve ser uma lista de IDs numéricos")
        else:
            if not isinstance(valor, (int, float)) or isinstance(valor, bool):
                raise ValueError(f"'{campo}' de inbound deve ser um número")
            if rango and not 0.5 <= valor <= 300:
                raise ValueError(f"'{campo}' de inbound deve ser segundos entre 0.5 e 300")
            valor = float(valor)
        validated[campo] = valor
    extra = set(value) - set(_INBOUND_FIELDS)
    if extra:
        raise ValueError(f"chaves desconhecidas em inbound: {', '.join(sorted(extra))}")
    return validated


@dataclass(frozen=True)
class WebhookEndpoint:
    name: str
    url: str


def default_config() -> dict[str, dict[str, Any]]:
    return {
        "telegram": {
            "enabled": False,
            "chat_id_default": "",
            "inbound": {
                "enabled": False,
                "allowed_user_ids": [],
                "poll_interval_seconds": 2.0,
                "experiences": True,
            },
        },
        "whatsapp": {"enabled": False, "phone_number_id": "", "number_default": ""},
        "slack": {"enabled": False, "channel_default": ""},
        "webhook": {"enabled": False, "endpoints": []},
    }


def _validate_platform_config(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"configuração de {name} deve ser um objeto")
    normalized: dict[str, Any] = {}
    for campo, tipo in _FIELDS[name].items():
        if campo not in value:
            if campo in _OPTIONAL_FIELDS.get(name, frozenset()):
                continue
            raise ValueError(f"configuração de {name} exige a chave '{campo}'")
        valor = value[campo]
        if tipo is bool and not isinstance(valor, bool):
            raise ValueError(f"'{campo}' de {name} deve ser booleano")
        if tipo is str and not isinstance(valor, str):
            raise ValueError(f"'{campo}' de {name} deve ser textual")
        if tipo is list and (
            not isinstance(valor, list) or not all(_valid_endpoint(e) for e in valor)
        ):
            raise ValueError(f"'{campo}' de {name} deve ser uma lista de endpoints")
        if tipo is dict:
            if campo == "inbound":
                valor = _valid_inbound(valor)
            else:
                raise ValueError(f"'{campo}' de {name} tem esquema desconhecido")
        normalized[campo] = valor
    extra = set(value) - set(_FIELDS[name])
    if extra:
        raise ValueError(f"chaves desconhecidas em {name}: {', '.join(sorted(extra))}")
    return normalized


def _valid_endpoint(endpoint: Any) -> bool:
    if not isinstance(endpoint, dict):
        return False
    name = endpoint.get("name")
    url = endpoint.get("url")
    if not isinstance(name, str) or not name.strip():
        return False
    return isinstance(url, str) and (url.startswith("http://") or url.startswith("https://"))


def load_config(home: Path) -> dict[str, dict[str, Any]]:
    path = home / MESSAGING_FILE
    doc = default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return doc
    except (OSError, ValueError) as exc:
        raise ValueError(f"{MESSAGING_FILE} inválido: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{MESSAGING_FILE} deve ser um objeto")
    for nome in _PLATFORMS:
        valor = raw.get(nome)
        if valor is not None:
            doc[nome] = {**default_config()[nome], **_validate_platform_config(nome, valor)}
    return doc


def save_config(home: Path, doc: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Valida o documento inteiro antes de tocar no disco; o arquivo nasce atômico."""
    normalizado: dict[str, dict[str, Any]] = {}
    for nome in _PLATFORMS:
        base = {**default_config()[nome]}
        base.update(_validate_platform_config(nome, doc.get(nome, default_config()[nome])))
        normalizado[nome] = base
    home.mkdir(parents=True, exist_ok=True)
    secure_atomic_write_text(
        home / MESSAGING_FILE, json.dumps(normalizado, ensure_ascii=False, indent=2)
    )
    return normalizado


def endpoints(home: Path) -> dict[str, str]:
    """Endpoints de webhook configurados, por nome."""
    raw = load_config(home).get("webhook", {}).get("endpoints", [])
    return {entry["name"]: entry["url"] for entry in raw}


def webhook_endpoints(home: Path) -> list[dict[str, str]]:
    """Endpoints de webhook na ordem salva, sem depender de campo extra."""
    return list(load_config(home).get("webhook", {}).get("endpoints", []))


def _endpoint_name(name: str) -> str:
    nome = name.strip()
    if not nome:
        raise ValueError("nome do endpoint é obrigatório")
    return nome


def _endpoint_url(url: str) -> str:
    destino = url.strip()
    if not (destino.startswith("http://") or destino.startswith("https://")):
        raise ValueError("URL do endpoint deve começar com http(s)://")
    return destino


def add_webhook_endpoint(home: Path, name: str, url: str) -> dict[str, str]:
    """Adiciona um endpoint de webhook; valida antes de tocar no disco."""
    nome = _endpoint_name(name)
    destino = _endpoint_url(url)
    doc = load_config(home)
    atual = doc["webhook"]["endpoints"]
    if any(entry["name"] == nome for entry in atual):
        raise ValueError(f"endpoint '{nome}' já existe")
    doc["webhook"]["endpoints"] = [*atual, {"name": nome, "url": destino}]
    save_config(home, doc)
    return {"name": nome, "url": destino}


def remove_webhook_endpoint(home: Path, name: str) -> dict[str, str | bool]:
    """Remove um endpoint de webhook; falha fechado quando ele não existe."""
    nome = _endpoint_name(name)
    doc = load_config(home)
    restantes = [entry for entry in doc["webhook"]["endpoints"] if entry["name"] != nome]
    if len(restantes) == len(doc["webhook"]["endpoints"]):
        raise ValueError(f"endpoint '{nome}' não existe")
    doc["webhook"]["endpoints"] = restantes
    save_config(home, doc)
    return {"name": nome, "removed": True}


# --- cofre ------------------------------------------------------------------


def _secret_ref(platform: str) -> CredentialRef:
    return CredentialRef(platform, "primary")


def platform_secret(home: Path, platform: str) -> str | None:
    """Devolve o segredo salvo, ou `None` quando não há — nunca erros de cofre."""
    if not SECRET_KEYS.get(platform):
        return None
    vault = build_credential_service(home)
    if vault.state is not VaultState.UNLOCKED:
        return None
    try:
        secret = vault.get(_secret_ref(platform))
    except (VaultError, KeyError, ValueError, OSError):
        return None
    key = SECRET_KEYS[platform]
    values = secret.reveal()
    return values.get(key)


def platform_has_secret(home: Path, platform: str) -> bool:
    return platform_secret(home, platform) is not None


def save_platform_secret(home: Path, platform: str, secret: str) -> dict:
    if not SECRET_KEYS.get(platform):
        raise ValueError("plataforma não aceita credencial")
    if not secret.strip():
        raise ValueError("segredo é obrigatório")
    vault = build_credential_service(home)
    metadata = vault.put(
        _secret_ref(platform), CredentialSecret({SECRET_KEYS[platform]: secret.strip()})
    )
    return {"platform": platform, "saved": True, "masked": metadata.masked_identifier}


def drop_platform_secret(home: Path, platform: str) -> dict:
    if not SECRET_KEYS.get(platform):
        raise ValueError("plataforma não aceita credencial")
    vault = build_credential_service(home)
    try:
        vault.delete(_secret_ref(platform))
    except CredentialNotFoundError:
        return {"platform": platform, "removed": False}
    return {"platform": platform, "removed": True}


def vault_state(home: Path) -> str:
    try:
        return build_credential_service(home).state.value
    except (VaultError, OSError, ValueError):
        return "indisponivel"
