"""Perfis declarativos para endpoints compatíveis com OpenAI Chat Completions."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

import httpx

__all__ = [
    "DEEPSEEK_PROFILE",
    "GROQ_PROFILE",
    "OpenAICompatibleProfile",
    "custom_profile",
]


_ATTRIBUTION_HEADERS = frozenset({"http-referer", "x-title"})
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_COMMON_PARAMETERS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "seed",
        "response_format",
        "tool_choice",
        "parallel_tool_calls",
    }
)


@dataclass(frozen=True)
class OpenAICompatibleProfile:
    """Contrato imutável de um endpoint que implementa Chat Completions.

    Headers adicionais são deliberadamente limitados a atribuição explícita;
    autenticação é sempre construída pelo adapter a partir da credencial em
    memória, nunca por configuração de perfil.
    """

    id: str
    base_url: str
    models_url: str
    allowed_headers: frozenset[str] = frozenset()
    headers: Mapping[str, str] = field(default_factory=dict)
    display_name: str | None = None
    allowed_parameters: frozenset[str] = _COMMON_PARAMETERS
    model_parameter_allowlists: Mapping[str, frozenset[str]] = field(default_factory=dict)
    tool_unsupported_models: frozenset[str] = frozenset()
    trusted_remote: bool = False

    def __post_init__(self) -> None:
        _validate_trusted_remote(self.trusted_remote)
        if not self.id.strip():
            raise ValueError("provider id é obrigatório")
        base_url = _normalize_url(self.base_url, "base_url")
        models_url = _normalize_url(self.models_url, "models_url")
        if not self.trusted_remote and not _is_loopback_host(httpx.URL(base_url).host or ""):
            raise ValueError("base_url remoto requer trusted_remote=True")
        if _origin(base_url) != _origin(models_url):
            raise ValueError("models_url deve usar a mesma origem de base_url")
        allowed = _normalize_allowed_headers(self.allowed_headers)
        headers = _normalize_headers(self.headers, allowed)
        model_allowlists = _normalize_model_allowlists(self.model_parameter_allowlists)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "models_url", models_url)
        object.__setattr__(self, "allowed_headers", allowed)
        object.__setattr__(self, "headers", MappingProxyType(headers))
        object.__setattr__(self, "allowed_parameters", frozenset(self.allowed_parameters))
        object.__setattr__(self, "model_parameter_allowlists", MappingProxyType(model_allowlists))

    @property
    def name(self) -> str:
        return self.display_name or self.id.title()

    def parameters_for(self, model: str) -> frozenset[str]:
        return self.model_parameter_allowlists.get(model, self.allowed_parameters)


def custom_profile(
    *,
    base_url: str = "http://127.0.0.1:8000/v1",
    models_url: str | None = None,
    headers: Mapping[str, str] | None = None,
    allowed_headers: frozenset[str] | set[str] | tuple[str, ...] = (),
    trusted_remote: bool = False,
    allowed_parameters: frozenset[str] = _COMMON_PARAMETERS,
) -> OpenAICompatibleProfile:
    """Cria perfil customizado, exigindo confiança administrativa para hosts remotos.

    A validação é puramente sintática e não resolve DNS: ela evita abrir uma
    consulta DNS no processo de configuração e torna explícita a decisão de
    confiar em um host remoto. ``models_url`` deve permanecer na mesma origem
    do endpoint de chat para não introduzir um segundo alvo de rede.
    """
    _validate_trusted_remote(trusted_remote)
    normalized_base = _normalize_url(base_url, "base_url")
    normalized_models = _normalize_url(models_url or f"{normalized_base}/models", "models_url")
    if not trusted_remote and not _is_loopback_host(httpx.URL(normalized_base).host or ""):
        raise ValueError("base_url remoto requer trusted_remote=True")
    if _origin(normalized_base) != _origin(normalized_models):
        raise ValueError("models_url deve usar a mesma origem de base_url")
    return OpenAICompatibleProfile(
        id="custom",
        base_url=normalized_base,
        models_url=normalized_models,
        allowed_headers=frozenset(allowed_headers),
        headers=headers or {},
        display_name="Personalizado",
        allowed_parameters=allowed_parameters,
        trusted_remote=trusted_remote,
    )


def _normalize_url(value: str, field_name: str) -> str:
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} é inválida")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError(f"{field_name} é inválida") from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(f"{field_name} é inválida")
    return str(url).rstrip("/")


def _validate_trusted_remote(value: object) -> None:
    if type(value) is not bool:
        raise ValueError("trusted_remote deve ser bool")


def _normalize_allowed_headers(headers: frozenset[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for name in headers:
        _validate_header_name(name)
        normalized_name = name.casefold()
        if normalized_name not in _ATTRIBUTION_HEADERS:
            raise ValueError("header não permitido")
        normalized.add(normalized_name)
    return frozenset(normalized)


def _normalize_headers(headers: Mapping[str, str], allowed: frozenset[str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        _validate_header_name(name)
        if not isinstance(value, str):
            raise ValueError("header não permitido")
        if "\r" in value or "\n" in value:
            raise ValueError("header contém CRLF")
        if name.casefold() not in allowed:
            raise ValueError("header não permitido")
        normalized[name] = value
    return normalized


def _validate_header_name(name: object) -> None:
    if not isinstance(name, str) or "\r" in name or "\n" in name:
        raise ValueError("header contém CRLF")
    if not _HEADER_NAME.fullmatch(name):
        raise ValueError("header não permitido")


def _normalize_model_allowlists(
    value: Mapping[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    normalized: dict[str, frozenset[str]] = {}
    for model, parameters in value.items():
        if not isinstance(model, str) or not model.strip():
            raise ValueError("modelo de perfil é inválido")
        normalized[model] = frozenset(parameters)
    return normalized


def _is_loopback_host(host: str) -> bool:
    if host.casefold() in {"localhost", "localhost."}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = httpx.URL(url)
    return parsed.scheme, parsed.host or "", parsed.port


DEEPSEEK_PROFILE = OpenAICompatibleProfile(
    id="deepseek",
    base_url="https://api.deepseek.com/v1",
    models_url="https://api.deepseek.com/v1/models",
    display_name="DeepSeek",
    model_parameter_allowlists={"deepseek-reasoner": frozenset({"max_tokens"})},
    tool_unsupported_models=frozenset({"deepseek-reasoner"}),
    trusted_remote=True,
)

GROQ_PROFILE = OpenAICompatibleProfile(
    id="groq",
    base_url="https://api.groq.com/openai/v1",
    models_url="https://api.groq.com/openai/v1/models",
    display_name="Groq",
    trusted_remote=True,
)
