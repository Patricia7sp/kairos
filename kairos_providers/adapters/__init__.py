"""Adapters para todos os provedores LLM suportados."""

from kairos_providers.adapters.anthropic_adapter import AnthropicAdapter
from kairos_providers.adapters.gemini_adapter import GoogleGeminiAdapter
from kairos_providers.adapters.gemini_native import GeminiNativeAdapter
from kairos_providers.adapters.ollama_adapter import OllamaAdapter
from kairos_providers.adapters.ollama_native import OllamaNativeAdapter
from kairos_providers.adapters.openai_adapter import OpenAICompatibleAdapter
from kairos_providers.adapters.openai_responses import OpenAIResponsesAdapter

__all__ = [
    "AnthropicAdapter",
    "GeminiNativeAdapter",
    "GoogleGeminiAdapter",
    "OllamaAdapter",
    "OllamaNativeAdapter",
    "OpenAICompatibleAdapter",
    "OpenAIResponsesAdapter",
]
