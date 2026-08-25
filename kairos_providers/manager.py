"""Gerenciador e Roteador de Provedores de LLM."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from kairos_providers.adapters import (
    AnthropicAdapter,
    GoogleGeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
)
from kairos_providers.base import (
    BaseLLMProvider,
    ConnectionStatus,
    ModelDescriptor,
)


def get_google_adc_token() -> str | None:
    """Tenta obter o token OAuth oficial do Google Cloud ADC se o gcloud estiver logado."""
    if not shutil.which("gcloud"):
        return None
    try:
        res = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:  # noqa: BLE001, S110
        pass
    return None


class ProviderManager:
    """Gerencia e instancia provedores com base em credenciais salvas e variáveis de ambiente."""

    def __init__(
        self,
        auth_store: dict[str, Any] | None = None,
        secret_resolver: Callable[[str, str], Mapping[str, str] | Any] | None = None,
    ) -> None:
        self.auth_store = auth_store or {}
        self.secret_resolver = secret_resolver

    def get_api_key(self, provider: str) -> str | None:
        env_map = {
            "openai": ["OPENAI_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY"],
            "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
            "openrouter": ["OPENROUTER_API_KEY"],
            "deepseek": ["DEEPSEEK_API_KEY"],
            "groq": ["GROQ_API_KEY"],
        }
        for var in env_map.get(provider, []):
            val = os.environ.get(var)
            if val and val.strip():
                return val.strip()

        creds = self.auth_store.get(provider, [])
        if isinstance(creds, list) and creds:
            first = creds[0]
            if isinstance(first, dict):
                credential_id = first.get("credential_id")
                if credential_id and self.secret_resolver is not None:
                    resolved = self.secret_resolver(provider, credential_id)
                    values = resolved.reveal() if hasattr(resolved, "reveal") else resolved
                    if isinstance(values, Mapping):
                        return values.get("api_key") or values.get("token")
                return first.get("api_key") or first.get("token")
        return None

    def get_provider(self, provider_name: str, **kwargs: Any) -> BaseLLMProvider:
        api_key = self.get_api_key(provider_name)

        if provider_name == "openai":
            return OpenAICompatibleAdapter(
                name="openai",
                base_url="https://api.openai.com/v1",
                api_key=api_key,
                default_model=kwargs.get("model", "gpt-4o"),
            )
        if provider_name == "anthropic":
            return AnthropicAdapter(
                name="anthropic",
                api_key=api_key,
                default_model=kwargs.get("model", "claude-3-7-sonnet-20250219"),
            )
        if provider_name == "gemini":
            adc_token = get_google_adc_token()
            return GoogleGeminiAdapter(
                name="gemini",
                api_key=api_key,
                oauth_token=adc_token,
                default_model=kwargs.get("model", "gemini-2.0-flash"),
            )
        if provider_name == "openrouter":
            return OpenAICompatibleAdapter(
                name="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
                default_model=kwargs.get("model", "anthropic/claude-3.7-sonnet"),
            )
        if provider_name == "deepseek":
            return OpenAICompatibleAdapter(
                name="deepseek",
                base_url="https://api.deepseek.com/v1",
                api_key=api_key,
                default_model=kwargs.get("model", "deepseek-chat"),
            )
        if provider_name == "groq":
            return OpenAICompatibleAdapter(
                name="groq",
                base_url="https://api.groq.com/openai/v1",
                api_key=api_key,
                default_model=kwargs.get("model", "llama-3.3-70b-versatile"),
            )
        if provider_name == "ollama":
            return OllamaAdapter(
                name="ollama",
                default_model=kwargs.get("model", "llama3.3"),
            )

        return OpenAICompatibleAdapter(
            name=provider_name,
            base_url=kwargs.get("base_url", "http://localhost:8000/v1"),
            api_key=api_key,
            default_model=kwargs.get("model", "custom"),
        )

    def list_all_models(self) -> list[ModelDescriptor]:
        providers = ["openai", "anthropic", "gemini", "deepseek", "groq", "ollama"]
        models: list[ModelDescriptor] = []
        for p in providers:
            inst = self.get_provider(p)
            models.extend(inst.list_models())
        return models

    async def test_all_connections(self) -> dict[str, ConnectionStatus]:
        providers = ["openai", "anthropic", "gemini", "openrouter", "deepseek", "groq", "ollama"]
        results: dict[str, ConnectionStatus] = {}
        for p in providers:
            inst = self.get_provider(p)
            results[p] = await inst.test_connection()
        return results
