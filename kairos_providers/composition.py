"""Composição local e sem efeitos de rede dos adapters de provider.

Este módulo é a única tabela de composição por provider. Os descritores e as
factories guardados no registry não carregam credenciais; o gateway as obtém
do cofre somente quando precisa instanciar um adapter.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from kairos_providers._async_cleanup import AsyncCleanupCoordinator
from kairos_providers.adapters import (
    AnthropicMessagesAdapter,
    GeminiNativeAdapter,
    OllamaNativeAdapter,
    OpenAIResponsesAdapter,
    OpenRouterAdapter,
)
from kairos_providers.adapters.openai_compatible import OpenAICompatibleAdapter
from kairos_providers.catalog import ModelCatalog
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
from kairos_providers.settings import (
    EndpointCatalogSnapshotStore,
    load_config_document,
    settings_from_document,
)
from kairos_security.credentials import build_credential_service

__all__ = [
    "ProviderCompositionConfig",
    "build_provider_gateway",
]


_OLLAMA_LOCAL_URL = "http://127.0.0.1:11434"


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
    if config is None:
        document = load_config_document(home)
        custom = settings_from_document(document, "custom")
        openrouter = settings_from_document(document, "openrouter")
        configuration = ProviderCompositionConfig(
            custom=custom_profile(**custom, allowed_headers=frozenset(custom["headers"])),
            openrouter_referer=openrouter["referer"],
            openrouter_title=openrouter["title"],
        )
    else:
        configuration = config
    registry = ProviderAdapterRegistry()
    clients = _ProviderHttpClients(client_factory)
    _register_adapters(registry, clients, configuration)

    catalog = ModelCatalog()
    catalog.merge(curated_models(), origin=CatalogOrigin.CURATED)
    snapshots = EndpointCatalogSnapshotStore(
        home,
        {
            "base_url": configuration.custom.base_url,
            "models_url": configuration.custom.models_url,
            "headers": dict(configuration.custom.headers),
        },
    )
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


def _is_loopback(host: str) -> bool:
    if host.casefold() in {"localhost", "localhost."}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
