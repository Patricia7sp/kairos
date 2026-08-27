"""Contrato HTTP do adapter nativo da Anthropic Messages API."""

from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import httpx

from kairos_integration.cost_accounting import estimate_interaction_cost
from kairos_providers import (
    AdapterRequest,
    CanonicalMessage,
    CanonicalToolCall,
    ContentPart,
    ModelPrice,
    ProviderError,
    ProviderErrorKind,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
)
from kairos_providers.adapters.anthropic_messages import AnthropicMessagesAdapter
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository, UsageRepository


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
        messages=(CanonicalMessage(role="user", content=(ContentPart(kind="text", value="Olá"),)),),
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
                        "message": {
                            "usage": {
                                "input_tokens": 12,
                                "cache_read_input_tokens": 3,
                                "cache_creation_input_tokens": 5,
                            }
                        },
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
            events = await collect(
                AnthropicMessagesAdapter(client, secret).stream(request_with_tool())
            )

        self.assertEqual(
            [event.kind for event in events], ["text_delta", "tool_call", "usage", "finish"]
        )
        self.assertEqual(events[0].text, "São ")
        self.assertEqual(events[1].tool_call.id, "toolu_01")  # type: ignore[union-attr]
        self.assertEqual(events[1].tool_call.name, "hora_local")  # type: ignore[union-attr]
        self.assertEqual(events[1].tool_call.arguments, '{"cidade":"Lisboa"}')  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.input_tokens, 20)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.output_tokens, 4)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.cache_read_tokens, 3)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.cache_write_tokens, 5)  # type: ignore[union-attr]
        self.assertEqual(events[3].finish_reason, "tool_use")

    async def test_usage_do_adapter_preserva_subconjuntos_na_contabilidade_persistida(self):
        """Somar detalhes outra vez ou descartá-los quebra custo e auditoria persistida."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    {
                        "type": "message_start",
                        "message": {
                            "usage": {
                                "input_tokens": 7,
                                "cache_read_input_tokens": 3,
                                "cache_creation_input_tokens": 5,
                            }
                        },
                    },
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 4},
                    },
                    {"type": "message_stop"},
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(
                AnthropicMessagesAdapter(client, "secret").stream(simple_request())
            )

        usage = next(event.usage for event in events if event.kind == "usage")
        assert usage is not None
        cost = estimate_interaction_cost(
            ModelPrice(
                prompt=Decimal("0.01"),
                completion=Decimal("0.02"),
                request=Decimal("0"),
            ),
            usage,
            attempts=1,
            source="catalog:test",
        )
        self.assertEqual(cost.estimated_usd, 0.23)

        with tempfile.TemporaryDirectory() as tmpdir:
            db = connect(Path(tmpdir) / "state.db")
            try:
                initialize_schema(db)
                SessionRepository(db).create("s1", source="test")
                repository = UsageRepository(db)
                repository.record_event(
                    "s1",
                    ResolvedModelSelection(
                        ProviderModelRef("anthropic", "claude-haiku-4-5-20251001"),
                        SelectionReason.GLOBAL_DEFAULT,
                    ),
                    billing_provider="anthropic",
                    billing_base_url="https://api.anthropic.com/v1",
                    billing_mode="api_key",
                    usage=usage,
                    api_call_count=1,
                    estimated_cost_usd=cost.estimated_usd,
                    cost_status=cost.status,
                    cost_source=cost.source,
                )
                repository.flush(now=1.0)
                row = db.execute(
                    "SELECT input_tokens, output_tokens, cache_read_tokens, "
                    "cache_write_tokens, estimated_cost_usd FROM session_model_usage"
                ).fetchone()
            finally:
                db.close()

        assert row is not None
        self.assertEqual(tuple(row), (15, 4, 3, 5, 0.23))

    async def test_discover_models_mantem_apenas_modelos_claude_textuais(self):
        """Promover registros não-Claude como chat produziria uma seleção inválida."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/v1/models")
            self.assertEqual(request.headers["x-api-key"], "secret")
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            self.assertEqual(request.headers["accept"], "application/json")
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
        self.assertFalse(models[1].capabilities.tools)
        self.assertTrue(models[1].capabilities.streaming)

    async def test_agrupar_tool_results_adjacentes_em_uma_mensagem_user(self):
        """Duas mensagens user seguidas após tools violam a alternância exigida por Messages."""
        request = AdapterRequest(
            model=ProviderModelRef("anthropic", "claude-sonnet-5"),
            messages=(
                CanonicalMessage(
                    role="assistant",
                    content=(),
                    tool_calls=(
                        CanonicalToolCall(id="toolu_1", name="temperatura", arguments="{}"),
                        CanonicalToolCall(id="toolu_2", name="umidade", arguments="{}"),
                    ),
                ),
                CanonicalMessage(
                    role="tool",
                    tool_call_id="toolu_1",
                    content=(ContentPart(kind="text", value="22°C"),),
                ),
                CanonicalMessage(
                    role="tool",
                    tool_call_id="toolu_2",
                    content=(ContentPart(kind="text", value="60%"),),
                ),
            ),
        )

        def handler(http_request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                json.loads(http_request.content)["messages"],
                [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "temperatura",
                                "input": {},
                            },
                            {"type": "tool_use", "id": "toolu_2", "name": "umidade", "input": {}},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "22°C"},
                            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "60%"},
                        ],
                    },
                ],
            )
            return httpx.Response(200, content=sse({"type": "message_stop"}))

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(AnthropicMessagesAdapter(client, "secret").stream(request))

        self.assertEqual([event.kind for event in events], ["finish"])

    async def test_eof_sem_message_stop_falha_sem_emitir_tool_ou_finish(self):
        """Tratar stream truncado como término confirmaria uma tool call possivelmente incompleta."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=sse(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_truncated",
                            "name": "tempo",
                            "input": {},
                        },
                    }
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                await collect(AnthropicMessagesAdapter(client, "secret").stream(simple_request()))

        self.assertIs(context.exception.kind, ProviderErrorKind.NETWORK)
        self.assertTrue(context.exception.retryable)

    async def test_tool_fechada_sem_message_stop_nao_vaza_antes_do_erro(self):
        """`content_block_stop` não confirma a tool enquanto o término da mensagem não chegou."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=sse(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_closed_but_truncated",
                            "name": "tempo",
                            "input": {},
                        },
                    },
                    {"type": "content_block_stop", "index": 0},
                ),
            )

        events = []
        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                async for event in AnthropicMessagesAdapter(client, "secret").stream(
                    simple_request()
                ):
                    events.append(event)

        self.assertIs(context.exception.kind, ProviderErrorKind.NETWORK)
        self.assertEqual(events, [])

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
