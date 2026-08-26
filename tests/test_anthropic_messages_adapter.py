"""Contrato HTTP do adapter nativo da Anthropic Messages API."""

from __future__ import annotations

import json
import unittest

import httpx

from kairos_providers import (
    AdapterRequest,
    CanonicalMessage,
    CanonicalToolCall,
    ContentPart,
    ProviderError,
    ProviderErrorKind,
    ProviderModelRef,
)
from kairos_providers.adapters.anthropic_messages import AnthropicMessagesAdapter


def sse(*events: dict[str, object]) -> bytes:
    """Representa o corpo SSE completo que a API entrega ao cliente HTTP."""
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="https://api.anthropic.test/v1")


async def collect(stream):
    return [event async for event in stream]


def request_with_tool() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("anthropic", "claude-sonnet-5"),
        messages=(
            CanonicalMessage(
                role="system",
                content=(ContentPart(kind="text", value="Você é conciso."),),
            ),
            CanonicalMessage(
                role="user",
                content=(ContentPart(kind="text", value="Qual hora em Lisboa?"),),
            ),
            CanonicalMessage(
                role="assistant",
                content=(ContentPart(kind="text", value="Vou consultar."),),
                tool_calls=(
                    CanonicalToolCall(
                        id="toolu_previous", name="hora_local", arguments='{"cidade":"Lisboa"}'
                    ),
                ),
            ),
            CanonicalMessage(
                role="tool",
                tool_call_id="toolu_previous",
                content=(ContentPart(kind="text", value="18:00"),),
            ),
        ),
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "hora_local",
                    "description": "Obtém a hora local.",
                    "parameters": {
                        "type": "object",
                        "properties": {"cidade": {"type": "string"}},
                    },
                },
            },
        ),
        parameters={"temperature": 0.2, "max_tokens": 256},
    )


def simple_request() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("anthropic", "claude-haiku-4-5-20251001"),
        messages=(
            CanonicalMessage(role="user", content=(ContentPart(kind="text", value="Olá"),)),
        ),
    )


class AnthropicMessagesAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_preserva_tool_use_id_texto_usage_e_payload_nativo(self):
        """Descartar ID/tool histórico torna impossível continuar a chamada seguinte."""
        secret = "sk-antropic-messages-sentinel"  # noqa: S105 - sentinela sintética de vazamento

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/messages")
            self.assertEqual(request.headers["x-api-key"], secret)
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            self.assertEqual(
                json.loads(request.content),
                {
                    "model": "claude-sonnet-5",
                    "system": "Você é conciso.",
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "Qual hora em Lisboa?"}],
                        },
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "Vou consultar."},
                                {
                                    "type": "tool_use",
                                    "id": "toolu_previous",
                                    "name": "hora_local",
                                    "input": {"cidade": "Lisboa"},
                                },
                            ],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_previous",
                                    "content": "18:00",
                                },
                            ],
                        },
                    ],
                    "tools": [
                        {
                            "name": "hora_local",
                            "description": "Obtém a hora local.",
                            "input_schema": {
                                "type": "object",
                                "properties": {"cidade": {"type": "string"}},
                            },
                        },
                    ],
                    "temperature": 0.2,
                    "max_tokens": 256,
                    "stream": True,
                },
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    {
                        "type": "message_start",
                        "message": {"usage": {"input_tokens": 12, "cache_read_input_tokens": 3}},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "São "},
                    },
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_01",
                            "name": "hora_local",
                            "input": {},
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "input_json_delta", "partial_json": '{"cidade":"Lis'},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "input_json_delta", "partial_json": 'boa"}'},
                    },
                    {"type": "content_block_stop", "index": 1},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 4},
                    },
                    {"type": "message_stop"},
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(AnthropicMessagesAdapter(client, secret).stream(request_with_tool()))

        self.assertEqual([event.kind for event in events], ["text_delta", "tool_call", "usage", "finish"])
        self.assertEqual(events[0].text, "São ")
        self.assertEqual(events[1].tool_call.id, "toolu_01")  # type: ignore[union-attr]
        self.assertEqual(events[1].tool_call.name, "hora_local")  # type: ignore[union-attr]
        self.assertEqual(events[1].tool_call.arguments, '{"cidade":"Lisboa"}')  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.input_tokens, 12)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.output_tokens, 4)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.cache_read_tokens, 3)  # type: ignore[union-attr]
        self.assertEqual(events[3].finish_reason, "tool_use")

    async def test_discover_models_mantem_apenas_modelos_claude_textuais(self):
        """Promover registros não-Claude como chat produziria uma seleção inválida."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/v1/models")
            self.assertEqual(request.headers["x-api-key"], "secret")
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"},
                        {"id": "claude-next", "display_name": "Claude Next"},
                        {"id": "not-a-chat-model", "display_name": "Other"},
                        {"id": "", "display_name": "Empty"},
                    ]
                },
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            models = await AnthropicMessagesAdapter(client, "secret").discover_models()

        self.assertEqual([model.ref.model for model in models], ["claude-sonnet-5", "claude-next"])
        self.assertEqual(models[0].display_name, "Claude Sonnet 5")
        self.assertTrue(models[0].capabilities.tools)
        self.assertTrue(models[1].capabilities.streaming)

    async def test_auth_error_e_repr_nao_expoem_segredo(self):
        """Corpo de erro do provider pode carregar a chave que a interface não pode registrar."""
        secret = "sk-anthropic-error-sentinel"  # noqa: S105 - sentinela sintética de vazamento

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": f"bad key {secret}"}})

        async with client_for(httpx.MockTransport(handler)) as client:
            adapter = AnthropicMessagesAdapter(client, secret)
            with self.assertRaises(ProviderError) as context:
                await collect(adapter.stream(simple_request()))

        self.assertIs(context.exception.kind, ProviderErrorKind.AUTH)
        self.assertFalse(context.exception.retryable)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception))
        self.assertNotIn(secret, repr(adapter))
