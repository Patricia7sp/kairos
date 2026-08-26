"""Adapters para todos os provedores LLM suportados."""

from kairos_providers.adapters.anthropic_adapter import AnthropicAdapter
from kairos_providers.adapters.anthropic_messages import AnthropicMessagesAdapter
from kairos_providers.adapters.gemini_adapter import GoogleGeminiAdapter
from kairos_providers.adapters.gemini_native import GeminiNativeAdapter
from kairos_providers.adapters.ollama_adapter import OllamaAdapter
from kairos_providers.adapters.ollama_native import OllamaNativeAdapter
from kairos_providers.adapters.openai_adapter import OpenAICompatibleAdapter
from kairos_providers.adapters.openai_responses import OpenAIResponsesAdapter
from kairos_providers.adapters.openrouter import OpenRouterAdapter, OpenRouterRoutingPolicy

__all__ = [
    "AnthropicAdapter",
    "AnthropicMessagesAdapter",
    "GeminiNativeAdapter",
    "GoogleGeminiAdapter",
    "OllamaAdapter",
    "OllamaNativeAdapter",
    "OpenAICompatibleAdapter",
    "OpenAIResponsesAdapter",
    "OpenRouterAdapter",
    "OpenRouterRoutingPolicy",
]
