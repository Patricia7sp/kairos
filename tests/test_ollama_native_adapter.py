"""Contrato HTTP do adapter nativo Ollama."""

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
from kairos_providers.adapters.ollama_native import OllamaNativeAdapter


def ndjson(*events: dict[str, object]) -> bytes:
    return b"".join(f"{json.dumps(event)}\n".encode() for event in events)


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def collect(stream):
    return [event async for event in stream]


def request_with_history_and_tool() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("ollama", "llama3.3"),
        messages=(
            CanonicalMessage(role="system", content=(ContentPart("text", "Seja conciso."),)),
            CanonicalMessage(role="user", content=(ContentPart("text", "Tempo em Lisboa?"),)),
            CanonicalMessage(
                role="assistant",
                content=(),
                tool_calls=(CanonicalToolCall("call_previous", "weather", '{"city":"Lisboa"}'),),
            ),
            CanonicalMessage(
                role="tool", tool_call_id="call_previous", content=(ContentPart("text", "22°C"),)
            ),
        ),
        tools=(
            {
                "type": "function",
                "function": {"name": "weather", "parameters": {"type": "object", "properties": {}}},
            },
        ),
        parameters={"temperature": 0.2, "max_tokens": 128},
    )


def simple_request() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("ollama", "llama3.3"),
        messages=(CanonicalMessage(role="user", content=(ContentPart("text", "Olá"),)),),
    )


class OllamaNativeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_normaliza_ndjson_tool_usage_e_payload_sem_credencial(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/chat")
            self.assertFalse(any(name.lower() == "authorization" for name in request.headers))
            self.assertEqual(
                json.loads(request.content),
                {
                    "model": "llama3.3",
                    "messages": [
                        {"role": "system", "content": "Seja conciso."},
                        {"role": "user", "content": "Tempo em Lisboa?"},
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_previous",
                                    "type": "function",
                                    "function": {
                                        "name": "weather",
                                        "arguments": {"city": "Lisboa"},
                                    },
                                }
                            ],
                        },
                        {"role": "tool", "tool_call_id": "call_previous", "content": "22°C"},
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "weather",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                    "options": {"temperature": 0.2, "num_predict": 128},
                    "stream": True,
                },
            )
            return httpx.Response(
                200,
                content=ndjson(
                    {"message": {"role": "assistant", "content": "São "}, "done": False},
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_weather",
                                    "function": {
                                        "name": "weather",
                                        "arguments": {"city": "Lisboa"},
                                    },
                                }
                            ]
                        },
                        "done": False,
                    },
                    {"done": True, "done_reason": "stop", "prompt_eval_count": 9, "eval_count": 3},
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(
                OllamaNativeAdapter(client, "http://127.0.0.1:11434/").stream(
                    request_with_history_and_tool()
                )
            )

        self.assertEqual(
            [event.kind for event in events], ["text_delta", "tool_call", "usage", "finish"]
        )
        self.assertEqual(events[0].text, "São ")
        self.assertEqual(
            events[1].tool_call, CanonicalToolCall("call_weather", "weather", '{"city":"Lisboa"}')
        )
        self.assertEqual(events[2].usage.input_tokens, 9)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.output_tokens, 3)  # type: ignore[union-attr]
        self.assertEqual(events[3].finish_reason, "stop")

    async def test_discover_models_usa_somente_tags_instaladas(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/tags")
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "llama3.3"},
                        {"name": "nomic-embed-text", "details": {"family": "embedding"}},
                    ]
                },
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            models = await OllamaNativeAdapter(client, "http://127.0.0.1:11434").discover_models()

        self.assertEqual([model.ref.model for model in models], ["llama3.3", "nomic-embed-text"])
        self.assertIsNone(models[0].capabilities.chat)
        self.assertIsNone(models[0].capabilities.tools)
        self.assertFalse(models[0].is_selectable())
        self.assertFalse(models[1].capabilities.chat)
        self.assertFalse(models[1].is_selectable())

    async def test_base_url_lan_exige_opt_in_explicito_sem_resolver_dns(self):
        async with client_for(
            httpx.MockTransport(lambda _request: self.fail("não deve chamar HTTP"))
        ) as client:
            with self.assertRaisesRegex(ValueError, "allow_remote"):
                OllamaNativeAdapter(client, "http://192.168.10.20:11434")
            adapter = OllamaNativeAdapter(client, "http://192.168.10.20:11434", allow_remote=True)

        self.assertIn("base_url=<configured>", repr(adapter))

    async def test_base_url_loopback_e_aceita_sem_opt_in(self):
        async with client_for(
            httpx.MockTransport(lambda _request: self.fail("não deve chamar HTTP"))
        ) as client:
            adapter = OllamaNativeAdapter(client, "http://[::1]:11434")

        self.assertIn("base_url=<configured>", repr(adapter))

    async def test_daemon_ausente_e_unavailable_sem_credencial(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("daemon absent")

        async with client_for(httpx.MockTransport(handler)) as client:
            status = await OllamaNativeAdapter(client, "http://127.0.0.1:11434").test_connection()

        self.assertFalse(status.ok)
        self.assertEqual(status.state, "unavailable")
        self.assertEqual(status.auth_method, "local")
        self.assertNotIn("daemon absent", status.message)

    async def test_eof_sem_done_e_rede_e_nao_confirma_tool(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=ndjson(
                    {
                        "message": {
                            "tool_calls": [{"function": {"name": "weather", "arguments": {}}}]
                        },
                        "done": False,
                    }
                ),
            )

        events = []
        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                async for event in OllamaNativeAdapter(client, "http://127.0.0.1:11434").stream(
                    simple_request()
                ):
                    events.append(event)

        self.assertIs(context.exception.kind, ProviderErrorKind.NETWORK)
        self.assertEqual(events, [])

    async def test_http_404_vira_model_error(self):
        async with client_for(httpx.MockTransport(lambda _request: httpx.Response(404))) as client:
            with self.assertRaises(ProviderError) as context:
                await collect(
                    OllamaNativeAdapter(client, "http://127.0.0.1:11434").stream(simple_request())
                )

        self.assertIs(context.exception.kind, ProviderErrorKind.MODEL)
