"""Matriz mínima de composição para todo provider registrado."""

from __future__ import annotations

import pytest

from kairos_providers.composition import build_provider_gateway

REGISTERED_PROVIDER_IDS = (
    "anthropic",
    "custom",
    "deepseek",
    "gemini",
    "groq",
    "ollama",
    "openai",
    "openrouter",
)


@pytest.mark.parametrize("provider", REGISTERED_PROVIDER_IDS)
def test_todo_provider_compoe_adapter_com_descritor_canonico(tmp_path, provider: str) -> None:
    gateway = build_provider_gateway(tmp_path)

    adapter = gateway.registry.create(provider)

    assert adapter.descriptor.id == provider
    assert callable(adapter.discover_models)
    assert callable(adapter.test_connection)
    assert callable(adapter.stream)
