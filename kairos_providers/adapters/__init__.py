"""Adapters para todos os provedores LLM suportados."""

from kairos_providers.adapters.anthropic_messages import AnthropicMessagesAdapter
from kairos_providers.adapters.gemini_native import GeminiNativeAdapter
from kairos_providers.adapters.ollama_native import OllamaNativeAdapter
from kairos_providers.adapters.openai_responses import OpenAIResponsesAdapter
from kairos_providers.adapters.openrouter import OpenRouterAdapter, OpenRouterRoutingPolicy

__all__ = [
    "AnthropicMessagesAdapter",
    "GeminiNativeAdapter",
    "OllamaNativeAdapter",
    "OpenAIResponsesAdapter",
    "OpenRouterAdapter",
    "OpenRouterRoutingPolicy",
]
