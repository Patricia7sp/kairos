"""Composição local e sem efeitos de rede dos adapters de provider.

Este módulo é a única tabela de composição por provider. Os descritores e as
factories guardados no registry não carregam credenciais; o gateway as obtém
do cofre somente quando precisa instanciar um adapter.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from kairos_providers._async_cleanup import AsyncCleanupCoordinator
from kairos_providers.adapters import (
    AnthropicAdapter,
    AnthropicMessagesAdapter,
    GeminiNativeAdapter,
    GoogleGeminiAdapter,
    OllamaAdapter,
    OllamaNativeAdapter,
    OpenAIResponsesAdapter,
    OpenRouterAdapter,
)
from kairos_providers.adapters import OpenAICompatibleAdapter as LegacyOpenAICompatibleAdapter
from kairos_providers.adapters.openai_compatible import OpenAICompatibleAdapter
from kairos_providers.catalog import ModelCatalog
from kairos_providers.catalog_store import CatalogSnapshotStore
from kairos_providers.contracts import CatalogOrigin, ProviderDescriptor
from kairos_providers.curated_catalog import curated_models
from kairos_providers.gateway import ProviderBillingMetadata, ProviderGateway
from kairos_providers.provider_profiles import (
    DEEPSEEK_PROFILE,
    GROQ_PROFILE,
    OpenAICompatibleProfile,
    custom_profile,
)
from kairos_providers.provider_registry import ProviderAdapterRegistry
from kairos_security.credentials import build_credential_service

__all__ = [
    "ProviderCompositionConfig",
    "build_legacy_provider",
    "build_provider_gateway",
    "get_google_adc_token",
    "resolve_legacy_api_key",
]


_OLLAMA_LOCAL_URL = "http://127.0.0.1:11434"
_LEGACY_ENVIRONMENT_KEYS = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "groq": ("GROQ_API_KEY",),
}


@dataclass(frozen=True)
class ProviderCompositionConfig:
    """Configuração administrativa, nunca derivada de uma requisição de usuário.

    Ollama permanece local por padrão. Um endpoint remoto exige que a camada
    administrativa passe explicitamente ``ollama_allow_remote=True``; a
    factory não aceita essa decisão como argumento de runtime.
    """

    custom: OpenAICompatibleProfile = field(default_factory=custom_profile)
    ollama_base_url: str = _OLLAMA_LOCAL_URL
    ollama_allow_remote: bool = False
    openrouter_referer: str | None = None
    openrouter_title: str | None = None

    def __post_init__(self) -> None:
        if type(self.ollama_allow_remote) is not bool:
            raise ValueError("ollama_allow_remote deve ser bool")
        url = httpx.URL(self.ollama_base_url)
        host = url.host
        if not host:
            raise ValueError("base_url do Ollama é inválida")
        if not self.ollama_allow_remote and not _is_loopback(host):
            raise ValueError("Ollama remoto requer configuração administrativa explícita")


class _LazyCredentialService:
    """Atrasa a abertura/inicialização do cofre até uma consulta de credencial."""

    def __init__(self, home: Path) -> None:
        self._home = home
        self._service: Any | None = None

    def list(self, provider: str) -> Any:
        return self._get().list(provider)

    def get(self, ref: Any) -> Any:
        return self._get().get(ref)

    def _get(self) -> Any:
        if self._service is None:
            self._service = build_credential_service(self._home)
        return self._service


class _ProviderHttpClients:
    """Owner dos clientes compartilhados até ``gateway.aclose()``.

    Clientes são criados apenas ao criar o primeiro adapter de um provider;
    construir o gateway ou listar seu catálogo, portanto, não abre conexão.
    """

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._closing = False
        self._close = AsyncCleanupCoordinator(task_name="kairos-provider-clients-close")
        self._client_factory = client_factory or self._new_http_client

    def for_provider(self, provider: str) -> httpx.AsyncClient:
        with self._close.lock:
            if self._closing or self._close.succeeded:
                raise RuntimeError("gateway de providers já foi encerrado")
            client = self._clients.get(provider)
            if client is None:
                client = self._client_factory()
                self._clients[provider] = client
            return client

    @staticmethod
    def _new_http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._close.run(self._close_attempt, on_reserve=self._begin_closing)

    def _begin_closing(self) -> None:
        self._closing = True

    async def _close_attempt(self) -> None:
        errors: list[BaseException] = []
        for provider, client in tuple(self._clients.items()):
            try:
                await client.aclose()
            except BaseException as exc:  # noqa: BLE001 - tenta os demais sob cancelamento
                errors.append(exc)
            else:
                del self._clients[provider]
        if errors:
            raise BaseExceptionGroup("falha ao fechar clientes de providers", errors)


class _ComposedProviderGateway(ProviderGateway):
    """Gateway que declara a posse dos clientes HTTP criados pelas factories."""

    def __init__(self, *args: Any, clients: _ProviderHttpClients, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._clients = clients

    async def aclose(self) -> None:
        """Fecha os clientes HTTP compartilhados criados por esta composição."""
        await self._clients.aclose()


def build_provider_gateway(
    home: Path,
    *,
    config: ProviderCompositionConfig | None = None,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> ProviderGateway:
    """Monta registry, catálogo e cofre sem consultar nenhum endpoint remoto."""
    configuration = config or ProviderCompositionConfig()
    registry = ProviderAdapterRegistry()
    clients = _ProviderHttpClients(client_factory)
    _register_adapters(registry, clients, configuration)

    catalog = ModelCatalog()
    catalog.merge(curated_models(), origin=CatalogOrigin.CURATED)
    snapshots = CatalogSnapshotStore(home / "model-catalog.json")
    for descriptor in registry.list_descriptors():
        snapshot = snapshots.load(descriptor.id)
        if snapshot is not None:
            catalog.load_snapshot(snapshot)

    return _ComposedProviderGateway(
        registry,
        catalog,
        _LazyCredentialService(home),
        snapshots,
        clients=clients,
        billing_routes=_billing_routes(configuration),
    )


def _billing_routes(
    config: ProviderCompositionConfig,
) -> dict[str, ProviderBillingMetadata]:
    return {
        "openai": ProviderBillingMetadata("openai", "https://api.openai.com/v1", "credential"),
        "anthropic": ProviderBillingMetadata(
            "anthropic", "https://api.anthropic.com/v1", "credential"
        ),
        "gemini": ProviderBillingMetadata(
            "gemini", "https://generativelanguage.googleapis.com/v1beta", "credential"
        ),
        "ollama": ProviderBillingMetadata("ollama", config.ollama_base_url, "local"),
        "deepseek": ProviderBillingMetadata("deepseek", DEEPSEEK_PROFILE.base_url, "credential"),
        "groq": ProviderBillingMetadata("groq", GROQ_PROFILE.base_url, "credential"),
        "custom": ProviderBillingMetadata("custom", config.custom.base_url, "credential"),
        "openrouter": ProviderBillingMetadata(
            "openrouter", "https://openrouter.ai/api/v1", "credential"
        ),
    }


def _register_adapters(
    registry: ProviderAdapterRegistry,
    clients: _ProviderHttpClients,
    config: ProviderCompositionConfig,
) -> None:
    registry.register(
        ProviderDescriptor("openai", "OpenAI", ("api_key",)),
        lambda api_key=None, **_kwargs: OpenAIResponsesAdapter(
            clients.for_provider("openai"), api_key or ""
        ),
    )
    registry.register(
        ProviderDescriptor("anthropic", "Anthropic", ("api_key",)),
        lambda api_key=None, **_kwargs: AnthropicMessagesAdapter(
            clients.for_provider("anthropic"), api_key or ""
        ),
    )
    registry.register(
        ProviderDescriptor("gemini", "Google Gemini", ("api_key", "oauth")),
        lambda api_key=None, oauth_token=None, token=None, **_kwargs: GeminiNativeAdapter(
            clients.for_provider("gemini"), api_key=api_key, oauth_token=oauth_token or token
        ),
    )
    registry.register(
        ProviderDescriptor("ollama", "Ollama"),
        lambda **_kwargs: OllamaNativeAdapter(
            clients.for_provider("ollama"),
            config.ollama_base_url,
            allow_remote=config.ollama_allow_remote,
        ),
    )
    _register_compatible_adapter(registry, clients, DEEPSEEK_PROFILE)
    _register_compatible_adapter(registry, clients, GROQ_PROFILE)
    _register_compatible_adapter(registry, clients, config.custom)
    registry.register(
        ProviderDescriptor("openrouter", "OpenRouter", ("api_key",)),
        lambda api_key=None, **_kwargs: OpenRouterAdapter(
            clients.for_provider("openrouter"),
            api_key or "",
            referer=config.openrouter_referer,
            title=config.openrouter_title,
        ),
    )


def _register_compatible_adapter(
    registry: ProviderAdapterRegistry,
    clients: _ProviderHttpClients,
    profile: OpenAICompatibleProfile,
) -> None:
    registry.register(
        ProviderDescriptor(profile.id, profile.name, ("api_key",)),
        lambda api_key=None, **_kwargs: OpenAICompatibleAdapter(
            profile, clients.for_provider(profile.id), api_key
        ),
    )


def resolve_legacy_api_key(
    provider: str,
    auth_store: Mapping[str, Any],
    secret_resolver: Callable[[str, str], Mapping[str, str] | Any] | None,
) -> str | None:
    """Resolve o caminho legado sem copiar segredos para registry ou catálogo."""
    for environment_key in _LEGACY_ENVIRONMENT_KEYS.get(provider, ()):
        value = os.environ.get(environment_key)
        if value and value.strip():
            return value.strip()

    credentials = auth_store.get(provider, [])
    if not isinstance(credentials, list) or not credentials:
        return None
    first = credentials[0]
    if not isinstance(first, Mapping):
        return None
    credential_id = first.get("credential_id")
    if isinstance(credential_id, str) and credential_id and secret_resolver is not None:
        resolved = secret_resolver(provider, credential_id)
        values = resolved.reveal() if hasattr(resolved, "reveal") else resolved
        if isinstance(values, Mapping):
            return _secret_value(values)
    return _secret_value(first)


def _secret_value(values: Mapping[str, Any]) -> str | None:
    for name in ("api_key", "token"):
        value = values.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_google_adc_token() -> str | None:
    """Obtém ADC para a ponte legada apenas quando Gemini é efetivamente usado."""
    if not (gcloud := shutil.which("gcloud")):
        return None
    try:
        result = subprocess.run(  # noqa: S603
            [gcloud, "auth", "application-default", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def build_legacy_provider(
    provider: str, *, api_key: str | None, **kwargs: Any
) -> LegacyOpenAICompatibleAdapter | AnthropicAdapter | GoogleGeminiAdapter | OllamaAdapter:
    """Compatibilidade temporária para superfícies que ainda usam BaseLLMProvider."""
    factory = _LEGACY_FACTORIES.get(provider, _legacy_custom)
    if factory is _legacy_custom:
        kwargs.setdefault("name", provider)
    return factory(api_key=api_key, **kwargs)


def _legacy_openai(*, api_key: str | None, **kwargs: Any) -> LegacyOpenAICompatibleAdapter:
    return LegacyOpenAICompatibleAdapter(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key=api_key,
        default_model=kwargs.get("model", "gpt-4o"),
    )


def _legacy_anthropic(*, api_key: str | None, **kwargs: Any) -> AnthropicAdapter:
    return AnthropicAdapter(
        name="anthropic",
        api_key=api_key,
        default_model=kwargs.get("model", "claude-3-7-sonnet-20250219"),
    )


def _legacy_gemini(*, api_key: str | None, **kwargs: Any) -> GoogleGeminiAdapter:
    return GoogleGeminiAdapter(
        name="gemini",
        api_key=api_key,
        oauth_token=get_google_adc_token(),
        default_model=kwargs.get("model", "gemini-2.0-flash"),
    )


def _legacy_openrouter(*, api_key: str | None, **kwargs: Any) -> LegacyOpenAICompatibleAdapter:
    return LegacyOpenAICompatibleAdapter(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_model=kwargs.get("model", "anthropic/claude-3.7-sonnet"),
    )


def _legacy_deepseek(*, api_key: str | None, **kwargs: Any) -> LegacyOpenAICompatibleAdapter:
    return LegacyOpenAICompatibleAdapter(
        name="deepseek",
        base_url=DEEPSEEK_PROFILE.base_url,
        api_key=api_key,
        default_model=kwargs.get("model", "deepseek-chat"),
    )


def _legacy_groq(*, api_key: str | None, **kwargs: Any) -> LegacyOpenAICompatibleAdapter:
    return LegacyOpenAICompatibleAdapter(
        name="groq",
        base_url=GROQ_PROFILE.base_url,
        api_key=api_key,
        default_model=kwargs.get("model", "llama-3.3-70b-versatile"),
    )


def _legacy_ollama(*, api_key: str | None, **kwargs: Any) -> OllamaAdapter:
    del api_key
    return OllamaAdapter(name="ollama", default_model=kwargs.get("model", "llama3.3"))


def _legacy_custom(*, api_key: str | None, **kwargs: Any) -> LegacyOpenAICompatibleAdapter:
    return LegacyOpenAICompatibleAdapter(
        name=kwargs.pop("name", "custom"),
        base_url=kwargs.get("base_url", "http://localhost:8000/v1"),
        api_key=api_key,
        default_model=kwargs.get("model", "custom"),
    )


_LEGACY_FACTORIES: dict[str, Callable[..., Any]] = {
    "openai": _legacy_openai,
    "anthropic": _legacy_anthropic,
    "gemini": _legacy_gemini,
    "openrouter": _legacy_openrouter,
    "deepseek": _legacy_deepseek,
    "groq": _legacy_groq,
    "ollama": _legacy_ollama,
}


def _is_loopback(host: str) -> bool:
    if host.casefold() in {"localhost", "localhost."}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
