"""Contrato HTTP do adapter nativo Gemini."""

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
from kairos_providers.adapters.gemini_native import GeminiNativeAdapter


def sse(*events: dict[str, object]) -> bytes:
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def collect(stream):
    return [event async for event in stream]


def request_with_history_and_tool() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("gemini", "gemini-3.7-flash"),
        messages=(
            CanonicalMessage(role="system", content=(ContentPart("text", "Seja conciso."),)),
            CanonicalMessage(role="user", content=(ContentPart("text", "Tempo em Lisboa?"),)),
            CanonicalMessage(
                role="assistant",
                content=(ContentPart("text", "Vou consultar."),),
                tool_calls=(
                    CanonicalToolCall("call_previous", "weather", '{"city":"Lisboa"}'),
                ),
            ),
            CanonicalMessage(
                role="tool",
                tool_call_id="call_previous",
                content=(ContentPart("text", "22°C"),),
            ),
        ),
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "Consulta o tempo.",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            },
        ),
        parameters={"temperature": 0.2, "max_tokens": 128},
    )


def simple_request() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("gemini", "gemini-3.7-flash"),
        messages=(CanonicalMessage(role="user", content=(ContentPart("text", "Olá"),)),),
    )


class GeminiNativeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_normaliza_function_call_function_response_usage_e_payload(self):
        """A continuidade de tools depende de converter as duas direções nativas."""
        secret = "AIzaSy-gemini-sentinel"  # noqa: S105 - sentinela sintética de vazamento

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1beta/models/gemini-3.7-flash:streamGenerateContent")
            self.assertEqual(request.url.params, httpx.QueryParams({"alt": "sse"}))
            self.assertNotIn(secret, str(request.url))
            self.assertEqual(request.headers["x-goog-api-key"], secret)
            self.assertNotIn("authorization", request.headers)
            self.assertEqual(
                json.loads(request.content),
                {
                    "systemInstruction": {"parts": [{"text": "Seja conciso."}]},
                    "contents": [
                        {"role": "user", "parts": [{"text": "Tempo em Lisboa?"}]},
                        {
                            "role": "model",
                            "parts": [
                                {"text": "Vou consultar."},
                                {
                                    "functionCall": {
                                        "id": "call_previous",
                                        "name": "weather",
                                        "args": {"city": "Lisboa"},
                                    }
                                },
                            ],
                        },
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "functionResponse": {
                                        "id": "call_previous",
                                        "name": "weather",
                                        "response": {"content": "22°C"},
                                    }
                                }
                            ],
                        },
                    ],
                    "tools": [
                        {
                            "functionDeclarations": [
                                {
                                    "name": "weather",
                                    "description": "Consulta o tempo.",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"city": {"type": "string"}},
                                    },
                                }
                            ]
                        }
                    ],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 128},
                },
            )
            return httpx.Response(
                200,
                content=sse(
                    {"candidates": [{"content": {"parts": [{"text": "São "}]}}]},
                    {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {"functionCall": {"id": "call_weather", "name": "weather", "args": {"city": "Lisboa"}}}
                                    ]
                                },
                                "finishReason": "STOP",
                            }
                        ],
                        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4},
                    },
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(GeminiNativeAdapter(client, api_key=secret).stream(request_with_history_and_tool()))

        self.assertEqual([event.kind for event in events], ["text_delta", "tool_call", "usage", "finish"])
        self.assertEqual(events[0].text, "São ")
        self.assertEqual(events[1].tool_call, CanonicalToolCall("call_weather", "weather", '{"city":"Lisboa"}'))
        self.assertEqual(events[2].usage.input_tokens, 12)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.output_tokens, 4)  # type: ignore[union-attr]
        self.assertEqual(events[3].finish_reason, "stop")

    async def test_oauth_substitui_api_key_e_nunca_vaza_em_repr(self):
        oauth = "ya29.gemini-oauth-sentinel"
        api_key = "AIzaSy-unused-sentinel"

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["authorization"], f"Bearer {oauth}")
            self.assertNotIn("x-goog-api-key", request.headers)
            return httpx.Response(200, content=sse({"candidates": [{"finishReason": "STOP"}]}))

        async with client_for(httpx.MockTransport(handler)) as client:
            adapter = GeminiNativeAdapter(client, api_key=api_key, oauth_token=oauth)
            events = await collect(adapter.stream(simple_request()))

        self.assertEqual([event.kind for event in events], ["finish"])
        self.assertNotIn(api_key, repr(adapter))
        self.assertNotIn(oauth, repr(adapter))

    async def test_discover_models_filtra_generate_content_e_remove_prefixo_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1beta/models")
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "models/gemini-3.7-flash", "displayName": "Gemini Flash", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/gemini-embed", "supportedGenerationMethods": ["embedContent"]},
                        {"name": "models/palm-chat", "supportedGenerationMethods": ["generateContent"]},
                    ]
                },
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            models = await GeminiNativeAdapter(client, api_key="secret").discover_models()

        self.assertEqual([model.ref.model for model in models], ["gemini-3.7-flash"])
        self.assertEqual(models[0].display_name, "Gemini 3.7 Flash")
        self.assertTrue(models[0].capabilities.tools)

    async def test_eof_sem_finish_e_erro_de_rede_sem_confirmar_tool(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=sse(
                    {"candidates": [{"content": {"parts": [{"functionCall": {"id": "truncated", "name": "weather", "args": {}}}]}}]}
                ),
            )

        events = []
        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                async for event in GeminiNativeAdapter(client, api_key="secret").stream(simple_request()):
                    events.append(event)

        self.assertIs(context.exception.kind, ProviderErrorKind.NETWORK)
        self.assertTrue(context.exception.retryable)
        self.assertEqual(events, [])

    async def test_auth_error_nao_expoe_corpo_ou_chave(self):
        secret = "AIzaSy-error-sentinel"  # noqa: S105

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": f"bad key {secret}"}})

        async with client_for(httpx.MockTransport(handler)) as client:
            adapter = GeminiNativeAdapter(client, api_key=secret)
            with self.assertRaises(ProviderError) as context:
                await collect(adapter.stream(simple_request()))

        self.assertIs(context.exception.kind, ProviderErrorKind.AUTH)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception))
        self.assertNotIn(secret, repr(adapter))

    async def test_sem_credencial_reporta_indisponivel_sem_chamar_rede(self):
        async with client_for(httpx.MockTransport(lambda _request: self.fail("não deve chamar HTTP"))) as client:
            status = await GeminiNativeAdapter(client).test_connection()

        self.assertFalse(status.ok)
        self.assertEqual(status.state, "unavailable")

    async def test_evento_de_erro_no_stream_e_interno_sem_payload_externo(self):
        secret = "AIzaSy-stream-error-sentinel"  # noqa: S105

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse({"error": {"message": f"bad {secret}"}}))

        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                await collect(GeminiNativeAdapter(client, api_key=secret).stream(simple_request()))

        self.assertIs(context.exception.kind, ProviderErrorKind.INTERNAL)
        self.assertNotIn(secret, str(context.exception))
