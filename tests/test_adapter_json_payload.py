"""Immutable turn options must reach real HTTP serializers as JSON values."""

import asyncio
import json

import httpx
import pytest

from kairos_integration import InteractionEnvelope
from kairos_providers import AdapterRequest, CanonicalMessage, ContentPart, ProviderModelRef
from kairos_providers.composition import build_provider_gateway


@pytest.mark.parametrize(
    "provider,options",
    [
        ("openrouter", {"reasoning": {"enabled": False}, "stop": ["END"]}),
        ("openai", {"reasoning": {"effort": "low"}}),
        ("anthropic", {"thinking": {"type": "enabled", "budget_tokens": 1024}}),
        ("custom", {"response_format": {"type": "json_object"}}),
    ],
)
def test_frozen_nested_parameters_serialize_at_http_boundary(tmp_path, provider, options):
    received = []

    def upstream(request):
        received.append(json.loads(request.content))
        body = (
            'data: {"type":"message_stop"}\n\n' if provider == "anthropic" else "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    async def check():
        gateway = build_provider_gateway(
            tmp_path,
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
        )
        envelope = InteractionEnvelope("test-turn", "test", "Hello", parameters=options)
        request = AdapterRequest(
            ProviderModelRef(provider, "test-model"),
            (CanonicalMessage("user", (ContentPart("text", "Hello"),)),),
            parameters=envelope.parameters,
        )
        try:
            adapter = gateway.registry.create(provider, api_key="synthetic")
            async for _ in adapter.stream(request):
                pass
        finally:
            await gateway.aclose()
        for name, expected in options.items():
            assert received[0][name] == expected
        with pytest.raises(TypeError):
            envelope.parameters[next(iter(options))]["new"] = True

    asyncio.run(check())
