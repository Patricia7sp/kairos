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
    "telegram": {"enabled": bool, "chat_id_default": str},
    "whatsapp": {"enabled": bool, "phone_number_id": str, "number_default": str},
    "slack": {"enabled": bool, "channel_default": str},
    "webhook": {"enabled": bool, "endpoints": list},
}


@dataclass(frozen=True)
class WebhookEndpoint:
    name: str
    url: str


def default_config() -> dict[str, dict[str, Any]]:
    return {
        "telegram": {"enabled": False, "chat_id_default": ""},
        "whatsapp": {"enabled": False, "phone_number_id": "", "number_default": ""},
        "slack": {"enabled": False, "channel_default": ""},
        "webhook": {"enabled": False, "endpoints": []},
    }


def _validate_platform_config(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"configuração de {name} deve ser um objeto")
    for campo, tipo in _FIELDS[name].items():
        if campo not in value:
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
    extra = set(value) - set(_FIELDS[name])
    if extra:
        raise ValueError(f"chaves desconhecidas em {name}: {', '.join(sorted(extra))}")
    return {campo: value[campo] for campo in _FIELDS[name]}


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
            doc[nome] = _validate_platform_config(nome, valor)
    return doc


def save_config(home: Path, doc: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Valida o documento inteiro antes de tocar no disco; o arquivo nasce atômico."""
    normalizado: dict[str, dict[str, Any]] = {}
    for nome in _PLATFORMS:
        normalizado[nome] = _validate_platform_config(nome, doc.get(nome, default_config()[nome]))
    home.mkdir(parents=True, exist_ok=True)
    secure_atomic_write_text(
        home / MESSAGING_FILE, json.dumps(normalizado, ensure_ascii=False, indent=2)
    )
    return normalizado


def endpoints(home: Path) -> dict[str, str]:
    """Endpoints de webhook configurados, por nome."""
    raw = load_config(home).get("webhook", {}).get("endpoints", [])
    return {entry["name"]: entry["url"] for entry in raw}


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
