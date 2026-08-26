"""Contrato HTTP do adapter de primeira classe da OpenRouter."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

import httpx

from kairos_providers import (
    AdapterRequest,
    CanonicalMessage,
    ContentPart,
    ProviderError,
    ProviderErrorKind,
    ProviderModelRef,
)
from kairos_providers.adapters.openrouter import OpenRouterAdapter


def sse(*documents: dict[str, object] | str) -> bytes:
    return b"".join(
        f"data: {json.dumps(document) if isinstance(document, dict) else document}\n\n".encode()
        for document in documents
    )


def simple_request(model: str = "acme/chat") -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("openrouter", model),
        messages=(
            CanonicalMessage(
                role="user",
                content=(ContentPart(kind="text", value="Olá"),),
            ),
        ),
        parameters={"temperature": 0.2, "ignored": "não envia"},
    )


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="https://openrouter.test/api/v1")


async def collect(stream):
    return [event async for event in stream]


class OpenRouterAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_mapeia_gratuito_tools_preco_e_metadados(self):
        """Descartar campos do catálogo quebraria filtro gratuito e compatibilidade por modelo."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v1/models")
            self.assertEqual(
                dict(request.url.params),
                {"output_modalities": "text", "input_modalities": "text"},
            )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "acme/chat:free",
                            "name": "Acme Free",
                            "context_length": 65_536,
                            "supported_parameters": ["temperature", "tools"],
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                            "expiration_date": "2026-12-31",
                            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
                        },
                        {
                            "id": "openrouter/free",
                            "name": "OpenRouter Free Router",
                            "context_length": 32_768,
                            "supported_parameters": [],
                            "architecture": {
                                "input_modalities": ["text"],
                                "output_modalities": ["text"],
                            },
                            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
                        },
                        {
                            "id": "acme/image-only",
                            "architecture": {
                                "input_modalities": ["image"],
                                "output_modalities": ["image"],
                            },
                            "pricing": {},
                        },
                        {
                            "id": "acme/no-text-output",
                            "architecture": {
                                "input_modalities": ["text"],
                                "output_modalities": ["image"],
                            },
                            "pricing": {},
                        },
                    ]
                },
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            models = await OpenRouterAdapter(client, "secret").discover_models()

        self.assertEqual(
            [model.ref.model for model in models], ["acme/chat:free", "openrouter/free"]
        )
        free = models[0]
        self.assertTrue(free.is_free)
        self.assertTrue(free.capabilities.tools)
        self.assertEqual(free.capabilities.context_length, 65_536)
        self.assertEqual(free.price.prompt, Decimal("0"))
        self.assertEqual(free.price.completion, Decimal("0"))
        self.assertEqual(free.supported_parameters, frozenset({"temperature", "tools"}))
        self.assertEqual(free.input_modalities, frozenset({"text", "image"}))
        self.assertEqual(free.output_modalities, frozenset({"text"}))
        self.assertEqual(free.expiration_date, "2026-12-31")
        self.assertTrue(models[1].is_free)

    async def test_stream_envia_politica_segura_sem_mudar_modelo_e_headers_publicos(self):
        """Qualquer fallback entre IDs ou coleta permissiva viola a seleção explícita do usuário."""
        secret = "openrouter-policy-sentinel"  # noqa: S105 - sentinela sintética de vazamento

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], f"Bearer {secret}")
            self.assertEqual(request.headers["http-referer"], "https://kairos.example")
            self.assertEqual(request.headers["x-openrouter-title"], "Kairos")
            self.assertEqual(
                json.loads(request.content),
                {
                    "model": "acme/chat",
                    "messages": [{"role": "user", "content": "Olá"}],
                    "temperature": 0.2,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "provider": {
                        "data_collection": "deny",
                        "require_parameters": True,
                        "allow_fallbacks": True,
                    },
                },
            )
            return httpx.Response(
                200,
                content=sse(
                    {"choices": [{"delta": {"content": "Oi"}, "finish_reason": "stop"}]},
                    {"usage": {"prompt_tokens": 3, "completion_tokens": 1}},
                    "[DONE]",
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            adapter = OpenRouterAdapter(
                client, secret, referer="https://kairos.example", title="Kairos"
            )
            events = await collect(adapter.stream(simple_request()))

        self.assertEqual([event.kind for event in events], ["text_delta", "usage", "finish"])
        self.assertEqual(events[0].text, "Oi")
        self.assertEqual(events[-1].finish_reason, "stop")

    async def test_stream_rejeita_eof_truncado_sem_confirmar_tool_ou_finish(self):
        """Tratar EOF como término confirmaria uma tool call que o upstream não concluiu."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_truncated",
                                            "function": {"name": "weather", "arguments": "{"},
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ]
                    }
                ),
            )

        observed = []
        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                async for event in OpenRouterAdapter(client, "secret").stream(simple_request()):
                    observed.append(event)

        self.assertIs(context.exception.kind, ProviderErrorKind.NETWORK)
        self.assertEqual(observed, [])

    async def test_stream_preserva_tool_call_e_ignora_delta_apos_terminal(self):
        """Reemitir dados após o término ou perder o ID da tool corromperia o próximo turno."""
        request = AdapterRequest(
            model=ProviderModelRef("openrouter", "acme/chat"),
            messages=simple_request().messages,
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "parameters": {"type": "object"},
                    },
                },
            ),
        )

        def handler(http_request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                json.loads(http_request.content)["tools"],
                [
                    {
                        "type": "function",
                        "function": {"name": "weather", "parameters": {"type": "object"}},
                    }
                ],
            )
            return httpx.Response(
                200,
                content=sse(
                    {"choices": [{"delta": {"content": "Vou consultar. "}, "finish_reason": None}]},
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_weather",
                                            "function": {
                                                "name": "weather",
                                                "arguments": '{"city":"Lis',
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [{"index": 0, "function": {"arguments": 'boa"}'}}]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                    {"choices": [{"delta": {"content": "tarde"}, "finish_reason": None}]},
                    {"usage": {"prompt_tokens": 4, "completion_tokens": 2}},
                    "[DONE]",
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(OpenRouterAdapter(client, "secret").stream(request))

        self.assertEqual(
            [event.kind for event in events], ["text_delta", "tool_call", "usage", "finish"]
        )
        self.assertEqual(events[1].tool_call.id, "call_weather")  # type: ignore[union-attr]
        self.assertEqual(events[1].tool_call.arguments, '{"city":"Lisboa"}')  # type: ignore[union-attr]
        self.assertEqual(events[-1].finish_reason, "tool_calls")

    async def test_erros_nao_expoem_credencial_e_rejeitam_referer_inseguro(self):
        """Erros ou configuração pública não podem transformar segredo em dado observável."""
        secret = "openrouter-auth-sentinel"  # noqa: S105 - sentinela sintética de vazamento

        with self.assertRaises(ValueError):
            async with httpx.AsyncClient() as client:
                OpenRouterAdapter(client, secret, referer=f"https://{secret}@kairos.example")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": f"bad key {secret}"}})

        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                await collect(OpenRouterAdapter(client, secret).stream(simple_request()))

        self.assertIs(context.exception.kind, ProviderErrorKind.AUTH)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception))
