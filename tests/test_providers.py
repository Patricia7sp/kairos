"""Testes unitários para a camada abstrata de provedores LLM."""

import unittest

from kairos_providers.adapters import (
    AnthropicAdapter,
    GoogleGeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
)
from kairos_providers.base import BaseLLMProvider
from kairos_providers.manager import ProviderManager


class ProviderAdaptersTests(unittest.TestCase):
    def test_openai_adapter_metadata(self):
        adapter = OpenAICompatibleAdapter(api_key="sk-test", default_model="gpt-4o")
        self.assertIsInstance(adapter, BaseLLMProvider)
        models = adapter.list_models()
        self.assertTrue(any(m.id == "gpt-4o" for m in models))
        headers = adapter._headers()
        self.assertEqual(headers.get("Authorization"), "Bearer sk-test")

    def test_anthropic_adapter_metadata(self):
        adapter = AnthropicAdapter(api_key="sk-ant-test")
        self.assertIsInstance(adapter, BaseLLMProvider)
        models = adapter.list_models()
        self.assertTrue(any("claude-3-7-sonnet" in m.id for m in models))
        headers = adapter._headers()
        self.assertEqual(headers.get("x-api-key"), "sk-ant-test")
        self.assertIn("anthropic-version", headers)

    def test_gemini_adapter_metadata(self):
        adapter = GoogleGeminiAdapter(api_key="gem-test")
        self.assertIsInstance(adapter, BaseLLMProvider)
        models = adapter.list_models()
        self.assertTrue(any("gemini-2.0" in m.id for m in models))
        headers = adapter._headers()
        self.assertEqual(headers.get("x-goog-api-key"), "gem-test")

    def test_gemini_adapter_with_oauth_adc_token(self):
        adapter = GoogleGeminiAdapter(oauth_token="ya29.fake-adc-token")  # noqa: S106
        headers = adapter._headers()
        self.assertEqual(headers.get("Authorization"), "Bearer ya29.fake-adc-token")
        self.assertNotIn("x-goog-api-key", headers)

    def test_ollama_adapter_metadata(self):
        adapter = OllamaAdapter()
        self.assertIsInstance(adapter, BaseLLMProvider)
        models = adapter.list_models()
        self.assertTrue(len(models) > 0)
        self.assertEqual(adapter.base_url, "http://127.0.0.1:11434")

    def test_provider_manager_resolution(self):
        manager = ProviderManager(
            auth_store={
                "openai": [{"api_key": "sk-store-123"}],
                "anthropic": [{"api_key": "sk-ant-store"}],
            }
        )
        p_openai = manager.get_provider("openai")
        self.assertEqual(p_openai.api_key, "sk-store-123")

        p_anthropic = manager.get_provider("anthropic")
        self.assertEqual(p_anthropic.api_key, "sk-ant-store")

        all_models = manager.list_all_models()
        self.assertTrue(len(all_models) >= 10)


if __name__ == "__main__":
    unittest.main()
