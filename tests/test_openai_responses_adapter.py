"""Contrato HTTP do adapter nativo da OpenAI Responses API."""

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
from kairos_providers.adapters.openai_responses import OpenAIResponsesAdapter


def sse(*events: dict[str, object]) -> bytes:
    """Representa o corpo SSE completo que a API entrega ao cliente HTTP."""
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def request_with_tool() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("openai", "gpt-5.6-terra"),
        messages=(
            CanonicalMessage(
                role="user",
                content=(ContentPart(kind="text", value="Qual hora em Lisboa?"),),
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
        parameters={"temperature": 0.2},
    )


def simple_request() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("openai", "gpt-5.6-luna"),
        messages=(
            CanonicalMessage(
                role="user",
                content=(ContentPart(kind="text", value="Olá"),),
            ),
        ),
    )


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="https://api.openai.test/v1")


async def collect(stream):
    return [event async for event in stream]


class OpenAIResponsesAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_token_limit_is_sent_as_responses_output_limit(self):
        from dataclasses import replace

        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["max_output_tokens"], 128)
            self.assertNotIn("max_tokens", payload)
            return httpx.Response(
                200,
                content=sse({"type": "response.completed", "response": {"status": "completed"}}),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            await collect(
                OpenAIResponsesAdapter(client, "test").stream(
                    replace(simple_request(), parameters={"max_tokens": 128})
                )
            )

    async def test_responses_stream_normaliza_texto_tool_usage_e_requisicao(self):
        """Remover a conversão Responses/SSE quebraria texto, tools ou uso do gateway."""
        secret = "sk-openai-responses-sentinel"  # noqa: S105 - sentinela sintética de vazamento

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/responses")
            self.assertEqual(request.headers["authorization"], f"Bearer {secret}")
            self.assertEqual(
                json.loads(request.content),
                {
                    "model": "gpt-5.6-terra",
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Qual hora em Lisboa?"}],
                        },
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "name": "hora_local",
                            "description": "Obtém a hora local.",
                            "parameters": {
                                "type": "object",
                                "properties": {"cidade": {"type": "string"}},
                            },
                        },
                    ],
                    "temperature": 0.2,
                    "stream": True,
                },
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    {"type": "response.output_text.delta", "delta": "São "},
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "item_1",
                        "call_id": "call_1",
                        "delta": '{"cidade":"Lis',
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": "item_1",
                        "call_id": "call_1",
                        "name": "hora_local",
                        "arguments": '{"cidade":"Lisboa"}',
                    },
                    {
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "usage": {"input_tokens": 12, "output_tokens": 4},
                        },
                    },
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            adapter = OpenAIResponsesAdapter(client, secret)
            events = await collect(adapter.stream(request_with_tool()))

        self.assertEqual(
            [event.kind for event in events], ["text_delta", "tool_call", "usage", "finish"]
        )
        self.assertEqual(events[0].text, "São ")
        self.assertEqual(events[1].tool_call.id, "call_1")  # type: ignore[union-attr]
        self.assertEqual(events[1].tool_call.name, "hora_local")  # type: ignore[union-attr]
        self.assertEqual(events[1].tool_call.arguments, '{"cidade":"Lisboa"}')  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.input_tokens, 12)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.output_tokens, 4)  # type: ignore[union-attr]
        self.assertEqual(events[3].finish_reason, "completed")

    async def test_serializa_historico_assistant_e_tool_com_itens_responses(self):
        """Usar input_text para resposta ou tool histórico torna o próximo turno inválido."""
        request = AdapterRequest(
            model=ProviderModelRef("openai", "gpt-5.6-terra"),
            messages=(
                CanonicalMessage(
                    role="user",
                    content=(ContentPart(kind="text", value="Como está o tempo?"),),
                ),
                CanonicalMessage(
                    role="assistant",
                    content=(ContentPart(kind="text", value="Vou consultar."),),
                    tool_calls=(
                        CanonicalToolCall(
                            id="call_weather",
                            name="tempo",
                            arguments='{"cidade":"Lisboa"}',
                        ),
                    ),
                ),
                CanonicalMessage(
                    role="tool",
                    tool_call_id="call_weather",
                    content=(ContentPart(kind="text", value="22°C e limpo"),),
                ),
            ),
        )

        def handler(http_request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                json.loads(http_request.content)["input"],
                [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Como está o tempo?"}],
                    },
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Vou consultar."}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_weather",
                        "name": "tempo",
                        "arguments": '{"cidade":"Lisboa"}',
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_weather",
                        "output": "22°C e limpo",
                    },
                ],
            )
            return httpx.Response(200, content=sse({"type": "response.completed", "response": {}}))

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(OpenAIResponsesAdapter(client, "secret").stream(request))

        self.assertEqual([event.kind for event in events], ["finish"])

    async def test_discover_models_aceita_apenas_familias_textuais_responses_comprovadas(self):
        """Aceitar qualquer ID da Models API exporia modelos sem chat/Responses comprovado."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/v1/models")
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-5.6-terra"},
                        {"id": "gpt-4.1-mini"},
                        {"id": "gpt-4o-mini"},
                        {"id": "gpt-3.5-turbo-0125"},
                        {"id": "o4-mini"},
                        {"id": "gpt-3.5-turbo-instruct"},
                        {"id": "gpt-3.5-turbo-1106"},
                        {"id": "gpt-4-32k"},
                        {"id": "gpt-4o-search-preview"},
                        {"id": "gpt-oss-120b"},
                        {"id": "babbage-002"},
                        {"id": "davinci-002"},
                        {"id": "computer-use-preview"},
                        {"id": "text-embedding-3-small"},
                        {"id": "ft:gpt-5.6-terra:org:custom"},
                    ]
                },
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            models = await OpenAIResponsesAdapter(client, "secret").discover_models()

        self.assertEqual(
            [model.ref.model for model in models],
            ["gpt-5.6-terra", "gpt-4.1-mini", "gpt-4o-mini", "gpt-3.5-turbo-0125", "o4-mini"],
        )
        self.assertTrue(models[0].capabilities.chat)
        self.assertTrue(models[0].capabilities.tools)
        self.assertTrue(models[1].capabilities.chat)
        self.assertTrue(models[1].capabilities.streaming)

    async def test_response_completed_interrompe_consumo_de_eventos_tardios(self):
        """Ler SSE depois do terminal poderia persistir texto posterior inválido no turno."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=sse(
                    {"type": "response.output_text.delta", "delta": "antes"},
                    {
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "usage": {"input_tokens": 3, "output_tokens": 1},
                        },
                    },
                    {"type": "response.output_text.delta", "delta": "tarde"},
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(
                OpenAIResponsesAdapter(client, "secret").stream(simple_request())
            )

        self.assertEqual([event.kind for event in events], ["text_delta", "usage", "finish"])
        self.assertEqual(events[0].text, "antes")

    async def test_openai_auth_error_e_estavel_sem_segredo(self):
        """Propagar corpo/header de 401 exporia credenciais e impediria ação de autenticação."""
        secret = "sk-auth-error-sentinel"  # noqa: S105 - sentinela sintética de vazamento

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": f"bad key {secret}"}})

        async with client_for(httpx.MockTransport(handler)) as client:
            adapter = OpenAIResponsesAdapter(client, secret)
            with self.assertRaises(ProviderError) as context:
                await collect(adapter.stream(simple_request()))

        self.assertIs(context.exception.kind, ProviderErrorKind.AUTH)
        self.assertFalse(context.exception.retryable)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception))
