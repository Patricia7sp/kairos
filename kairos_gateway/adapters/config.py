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

#: Segredos de entrada (além da credencial principal) exigidos por cada
#: plataforma com canal de entrada. O WhatsApp valida a assinatura do webhook
#: com o App Secret e o apertão de mão do subscribe com o Verify Token.
INBOUND_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "whatsapp": ("app_secret", "verify_token"),
}

_PLATFORMS: tuple[str, ...] = ("telegram", "whatsapp", "slack", "webhook")

#: Campos não-secretos aceitos por plataforma. Chave -> tipo Python.
_FIELDS: dict[str, dict[str, type]] = {
    "telegram": {"enabled": bool, "chat_id_default": str, "inbound": dict},
    "whatsapp": {
        "enabled": bool,
        "phone_number_id": str,
        "number_default": str,
        "inbound": dict,
    },
    "slack": {"enabled": bool, "channel_default": str},
    "webhook": {"enabled": bool, "endpoints": list},
}

#: Campos opcionais com padrão — ausência não é erro; o padrão entra no merge.
_OPTIONAL_FIELDS: dict[str, frozenset[str]] = {
    "telegram": frozenset({"inbound"}),
    "whatsapp": frozenset({"inbound"}),
}

#: Esquema do canal de entrada por plataforma. Cada campo -> (tipo aceito,
#: obrigatório). Plataformas de entrada diferentes têm regras de remetente
#: diferentes: o Telegram autoriza por IDs numéricos, o WhatsApp por telefone.
#:
#: Listas de autorização vazias significam **ninguém autorizado** (fail-closed):
#: o canal de entrada só responde a remetentes vistos na lista.
_INBOUND_SCHEMAS: dict[str, dict[str, tuple[type | tuple[type, ...], bool]]] = {
    "telegram": {
        "enabled": (bool, False),
        "allowed_user_ids": (list, False),
        "poll_interval_seconds": ((int, float), True),
        "experiences": (bool, False),
    },
    "whatsapp": {
        "enabled": (bool, False),
        "allowed_phone_numbers": (list, False),
        "experiences": (bool, False),
    },
}

#: Tipo dos itens da lista de autorização do remetente, por plataforma.
_INBOUND_ALLOWLIST_ITEM: dict[str, type] = {"telegram": int, "whatsapp": str}

#: Campos do inbound cuja ausência é aceita — o padrão entra silenciosamente.
#: ``experiences`` liga a injeção de experiências ativas por turno no canal; a
#: ausência da chave herda o padrão ligado (desligue explicitamente com ``false``).
_INBOUND_OPTIONAL_DEFAULTS: dict[str, Any] = {"experiences": True}


def _valid_allowlist(platform: str, campo: str, valor: Any) -> None:
    """Valida a lista de autorização do remetente (fail-closed: só ela manda)."""
    item_tipo = _INBOUND_ALLOWLIST_ITEM[platform]
    valido = isinstance(valor, list) and all(
        isinstance(i, item_tipo) and not (item_tipo is int and isinstance(i, bool)) for i in valor
    )
    if valido:
        return
    if platform == "telegram":
        raise ValueError(f"'{campo}' de inbound deve ser uma lista de IDs numéricos")
    raise ValueError(f"'{campo}' de inbound deve ser uma lista de telefones no formato E.164")


def _valid_inbound(platform: str, value: Any) -> dict[str, Any]:
    if platform not in _INBOUND_SCHEMAS:
        raise ValueError(f"plataforma '{platform}' não tem canal de entrada")
    campos = _INBOUND_SCHEMAS[platform]
    if not isinstance(value, dict):
        raise ValueError("configuração de inbound deve ser um objeto")
    validated: dict[str, Any] = {}
    for campo, (tipo, rango) in campos.items():
        if campo not in value:
            if campo in _INBOUND_OPTIONAL_DEFAULTS:
                validated[campo] = _INBOUND_OPTIONAL_DEFAULTS[campo]
                continue
            raise ValueError(f"configuração de inbound de {platform} exige a chave '{campo}'")
        valor = value[campo]
        if tipo is bool:
            if not isinstance(valor, bool):
                raise ValueError(f"'{campo}' de inbound deve ser booleano")
        elif tipo is list:
            _valid_allowlist(platform, campo, valor)
        else:
            if not isinstance(valor, (int, float)) or isinstance(valor, bool):
                raise ValueError(f"'{campo}' de inbound deve ser um número")
            if rango and not 0.5 <= valor <= 300:
                raise ValueError(f"'{campo}' de inbound deve ser segundos entre 0.5 e 300")
            valor = float(valor)
        validated[campo] = valor
    extra = set(value) - set(campos)
    if extra:
        raise ValueError(
            f"chaves desconhecidas em inbound de {platform}: {', '.join(sorted(extra))}"
        )
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
        "whatsapp": {
            "enabled": False,
            "phone_number_id": "",
            "number_default": "",
            "inbound": {"enabled": False, "allowed_phone_numbers": [], "experiences": True},
        },
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
                valor = _valid_inbound(name, valor)
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


def _existing_secret_values(vault, ref: CredentialRef) -> dict[str, str]:
    """Valores já salvos do segredo, ou vazio — nunca expõe erro de cofre."""
    try:
        return dict(vault.get(ref).reveal())
    except CredentialNotFoundError:
        return {}
    except (VaultError, KeyError, ValueError, OSError):
        return {}


def platform_secret(home: Path, platform: str, *, field: str | None = None) -> str | None:
    """Devolve o campo de segredo salvo, ou `None` quando não há — nunca erros."""
    key = field or SECRET_KEYS.get(platform)
    if not key:
        return None
    vault = build_credential_service(home)
    if vault.state is not VaultState.UNLOCKED:
        return None
    values = _existing_secret_values(vault, _secret_ref(platform))
    return values.get(key)


def platform_has_secret(home: Path, platform: str) -> bool:
    return platform_secret(home, platform) is not None


def save_platform_secret(home: Path, platform: str, secret: str) -> dict:
    if not SECRET_KEYS.get(platform):
        raise ValueError("plataforma não aceita credencial")
    if not secret.strip():
        raise ValueError("segredo é obrigatório")
    key = SECRET_KEYS[platform]
    vault = build_credential_service(home)
    # Merge: outros campos de segredo da mesma plataforma (ex.: app_secret de
    # entrada do WhatsApp) sobrevivem ao salvar o credencial principal.
    valores = _existing_secret_values(vault, _secret_ref(platform))
    valores[key] = secret.strip()
    metadata = vault.put(_secret_ref(platform), CredentialSecret(valores))
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


def _platform_secret_fields(platform: str) -> tuple[str, ...]:
    principal = SECRET_KEYS.get(platform, "")
    extras = INBOUND_SECRET_KEYS.get(platform, ())
    return tuple(campo for campo in (principal, *extras) if campo)


def platform_secret_fields(home: Path, platform: str) -> dict[str, bool]:
    """Presença, por campo, dos segredos da plataforma no cofre."""
    return {
        campo: platform_secret(home, platform, field=campo) is not None
        for campo in _platform_secret_fields(platform)
    }


def save_platform_inbound_field(home: Path, platform: str, *, field: str, secret: str) -> dict:
    """Salva um campo de segredo de entrada preservando os demais campos."""
    if field not in INBOUND_SECRET_KEYS.get(platform, ()):
        raise ValueError(f"plataforma '{platform}' não aceita o segredo de entrada '{field}'")
    if not secret.strip():
        raise ValueError("segredo é obrigatório")
    vault = build_credential_service(home)
    valores = _existing_secret_values(vault, _secret_ref(platform))
    valores[field] = secret.strip()
    metadata = vault.put(_secret_ref(platform), CredentialSecret(valores))
    return {
        "platform": platform,
        "field": field,
        "saved": True,
        "masked": metadata.masked_identifier,
    }


def drop_platform_inbound_fields(home: Path, platform: str) -> dict:
    """Remove todos os campos de entrada; limpa o registro quando restar só isso."""
    campos = INBOUND_SECRET_KEYS.get(platform, ())
    if not campos:
        raise ValueError(f"plataforma '{platform}' não tem segredos de entrada")
    vault = build_credential_service(home)
    ref = _secret_ref(platform)
    valores = _existing_secret_values(vault, ref)
    for campo in campos:
        valores.pop(campo, None)
    if not valores:
        try:
            vault.delete(ref)
        except CredentialNotFoundError:
            pass
    else:
        vault.put(ref, CredentialSecret(valores))
    return {"platform": platform, "removed": True}


def vault_state(home: Path) -> str:
    try:
        return build_credential_service(home).state.value
    except (VaultError, OSError, ValueError):
        return "indisponivel"
