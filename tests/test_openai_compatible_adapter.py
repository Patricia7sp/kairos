"""Contrato do adapter canônico de Chat Completions compatível com OpenAI."""

from __future__ import annotations

import asyncio
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
from kairos_providers.adapters.openai_compatible import OpenAICompatibleAdapter
from kairos_providers.provider_profiles import (
    DEEPSEEK_PROFILE,
    GROQ_PROFILE,
    OpenAICompatibleProfile,
    custom_profile,
)


def sse(*events: dict[str, object] | str) -> bytes:
    """Representa um stream SSE completo de Chat Completions."""
    return b"".join(
        f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n".encode()
        for event in events
    )


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, follow_redirects=True)


async def collect(stream):
    return [event async for event in stream]


def request_with_history_and_tool() -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef("deepseek", "deepseek-chat"),
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
                        id="call_previous",
                        name="weather",
                        arguments='{"city":"Lisboa"}',
                    ),
                ),
            ),
            CanonicalMessage(
                role="tool",
                tool_call_id="call_previous",
                content=(ContentPart(kind="text", value="22°C e limpo"),),
            ),
        ),
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "Obtém o tempo.",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            },
        ),
        parameters={"temperature": 0.2, "max_tokens": 128, "not_supported": "drop"},
    )


def simple_request(provider: str = "deepseek", model: str = "deepseek-chat") -> AdapterRequest:
    return AdapterRequest(
        model=ProviderModelRef(provider, model),
        messages=(
            CanonicalMessage(role="user", content=(ContentPart(kind="text", value="Olá"),)),
        ),
    )


class OpenAICompatibleProfileTests(unittest.TestCase):
    def test_custom_endpoint_rejeita_header_nao_permitido(self):
        """Aceitar Authorization externo permitiria trocar a credencial configurada."""
        with self.assertRaisesRegex(ValueError, "header não permitido"):
            custom_profile(headers={"Authorization": "roubar"})

    def test_custom_endpoint_exige_allowlist_explicita_para_atribuicao(self):
        """Cabeçalhos de atribuição sem aprovação administrativa não devem sair do processo."""
        with self.assertRaisesRegex(ValueError, "header não permitido"):
            custom_profile(headers={"HTTP-Referer": "https://kairos.test"})

        profile = custom_profile(
            headers={"HTTP-Referer": "https://kairos.test"},
            allowed_headers={"HTTP-Referer"},
        )

        self.assertEqual(dict(profile.headers), {"HTTP-Referer": "https://kairos.test"})

    def test_custom_endpoint_rejeita_injecao_crlf_e_host_remoto_sem_trust(self):
        """CRLF e destinos remotos implícitos abririam injeção de header e SSRF."""
        with self.assertRaisesRegex(ValueError, "CRLF"):
            custom_profile(
                headers={"HTTP-Referer": "https://kairos.test\r\nAuthorization: roubar"},
                allowed_headers={"HTTP-Referer"},
            )
        with self.assertRaisesRegex(ValueError, "trusted_remote=True"):
            custom_profile(base_url="https://provider.example/v1")

    def test_custom_endpoint_rejeita_url_com_credencial_ou_query(self):
        """URLs configuráveis não podem carregar segredo ou alterar o alvo por query."""
        with self.assertRaises(ValueError):
            custom_profile(base_url="https://key@provider.example/v1", trusted_remote=True)
        with self.assertRaises(ValueError):
            custom_profile(base_url="https://provider.example/v1?key=secret", trusted_remote=True)

    def test_perfil_direto_tambem_exige_trust_para_host_remoto(self):
        """Instanciar o descritor diretamente não pode contornar a barreira contra SSRF."""
        with self.assertRaisesRegex(ValueError, "trusted_remote=True"):
            OpenAICompatibleProfile(
                id="custom",
                base_url="https://provider.example/v1",
                models_url="https://provider.example/v1/models",
            )

    def test_trusted_remote_rejeita_valores_que_nao_sao_bool_antes_do_host_check(self):
        """Truthy/falsy não booleanos não podem decidir a fronteira administrativa de SSRF."""
        for value in ("false", "true", 0, 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "trusted_remote deve ser bool"):
                custom_profile(base_url="https://provider.example/v1", trusted_remote=value)  # type: ignore[arg-type]

    def test_deepseek_e_groq_sao_perfis_declarativos(self):
        """Trocar a marca não pode exigir um branch no gateway de interação."""
        self.assertEqual(DEEPSEEK_PROFILE.id, "deepseek")
        self.assertEqual(GROQ_PROFILE.id, "groq")
        self.assertTrue(DEEPSEEK_PROFILE.base_url.endswith("/v1"))


class OpenAICompatibleAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_usa_profile_sem_branch_no_gateway(self):
        async with httpx.AsyncClient() as client:
            adapter = OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, "secret")
            self.assertEqual(adapter.descriptor.id, "deepseek")

    async def test_stream_serializa_historico_tools_filtra_parametros_e_normaliza_eventos(self):
        """Payload incompatível perderia continuidade de tools ou enviaria campos recusados pelo modelo."""
        secret = "deepseek-compatible-sentinel"  # noqa: S105 - sentinela de vazamento

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url, httpx.URL("https://api.deepseek.com/v1/chat/completions"))
            self.assertEqual(request.headers["authorization"], f"Bearer {secret}")
            self.assertEqual(
                json.loads(request.content),
                {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "user", "content": "Como está o tempo?"},
                        {
                            "role": "assistant",
                            "content": "Vou consultar.",
                            "tool_calls": [
                                {
                                    "id": "call_previous",
                                    "type": "function",
                                    "function": {
                                        "name": "weather",
                                        "arguments": '{"city":"Lisboa"}',
                                    },
                                },
                            ],
                        },
                        {"role": "tool", "tool_call_id": "call_previous", "content": "22°C e limpo"},
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "weather",
                                "description": "Obtém o tempo.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"city": {"type": "string"}},
                                },
                            },
                        },
                    ],
                    "temperature": 0.2,
                    "max_tokens": 128,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )
            return httpx.Response(
                200,
                content=sse(
                    {"choices": [{"delta": {"content": "São "}, "finish_reason": None}]},
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_weather",
                                            "type": "function",
                                            "function": {"name": "weather", "arguments": '{"city":"Lis'},
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
                                    "tool_calls": [
                                        {"index": 0, "function": {"arguments": 'boa"}'}}
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 4,
                            "prompt_tokens_details": {"cached_tokens": 2},
                        },
                    },
                    "[DONE]",
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(
                OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, secret).stream(
                    request_with_history_and_tool()
                )
            )

        self.assertEqual([event.kind for event in events], ["text_delta", "tool_call", "usage", "finish"])
        self.assertEqual(events[0].text, "São ")
        self.assertEqual(events[1].tool_call, CanonicalToolCall("call_weather", "weather", '{"city":"Lisboa"}'))
        self.assertEqual(events[2].usage.input_tokens, 12)  # type: ignore[union-attr]
        self.assertEqual(events[2].usage.cache_read_tokens, 2)  # type: ignore[union-attr]
        self.assertEqual(events[3].finish_reason, "tool_calls")

    async def test_modelo_reasoner_filtra_temperature_nao_suportada(self):
        """Enviar temperature a modelos reasoning conhecidos causa rejeição evitável no upstream."""
        request = AdapterRequest(
            model=ProviderModelRef("deepseek", "deepseek-reasoner"),
            messages=simple_request().messages,
            parameters={"temperature": 0.2, "max_tokens": 64},
        )

        def handler(http_request: httpx.Request) -> httpx.Response:
            payload = json.loads(http_request.content)
            self.assertNotIn("temperature", payload)
            self.assertEqual(payload["max_tokens"], 64)
            return httpx.Response(200, content=sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}, "[DONE]"))

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, "secret").stream(request))

        self.assertEqual([event.kind for event in events], ["finish"])

    async def test_discovery_preserva_curadoria_e_trata_modelo_dinamico_com_capacidade_conservadora(self):
        """Inferir tools de um ID desconhecido permitiria selecionar capacidade não comprovada."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url, httpx.URL("https://api.deepseek.com/v1/models"))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "deepseek-v4-pro"},
                        {"id": "deepseek-experimental"},
                        {"id": "text-embedding-3"},
                    ]
                },
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            models = await OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, "secret").discover_models()

        self.assertEqual([model.ref.model for model in models], ["deepseek-v4-pro", "deepseek-experimental"])
        self.assertTrue(models[0].capabilities.tools)
        self.assertFalse(models[1].capabilities.tools)
        self.assertTrue(models[1].capabilities.streaming)

    async def test_eof_sem_done_nao_confirma_tool_ou_finish(self):
        """Aceitar stream truncado confirmaria uma tool call que pode estar incompleta."""

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
                                            "id": "truncated",
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
                async for event in OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, "secret").stream(
                    simple_request()
                ):
                    observed.append(event)

        self.assertIs(context.exception.kind, ProviderErrorKind.NETWORK)
        self.assertEqual(observed, [])

    async def test_terminal_tool_calls_rejeita_delta_incompleto_ou_argumentos_invalidos(self):
        """Descartar tool call truncada no terminal perderia efeito pendente e confirmaria turno inválido."""
        sentinel = "tool-arguments-sentinel"
        invalid_deltas = (
            {"index": 0, "function": {"name": "weather", "arguments": "{}"}},
            {"index": 0, "id": "call_without_name", "function": {"arguments": "{}"}},
            {
                "index": 0,
                "id": "call_invalid_arguments",
                "function": {"name": "weather", "arguments": f'{{"token":"{sentinel}"'},
            },
        )
        for delta in invalid_deltas:
            with self.subTest(delta=delta):
                def handler(_request: httpx.Request, delta: dict[str, object] = delta) -> httpx.Response:
                    return httpx.Response(
                        200,
                        content=sse(
                            {"choices": [{"delta": {"tool_calls": [delta]}, "finish_reason": "tool_calls"}]},
                            "[DONE]",
                        ),
                    )

                observed = []
                async with client_for(httpx.MockTransport(handler)) as client:
                    with self.assertRaises(ProviderError) as context:
                        async for event in OpenAICompatibleAdapter(
                            DEEPSEEK_PROFILE, client, "secret"
                        ).stream(simple_request()):
                            observed.append(event)

                self.assertIs(context.exception.kind, ProviderErrorKind.INCOMPATIBLE)
                self.assertFalse(context.exception.retryable)
                self.assertNotIn(sentinel, str(context.exception))
                self.assertNotIn(sentinel, repr(context.exception))
                self.assertEqual(observed, [])

    async def test_tool_delta_rejeita_imediatamente_campos_explicitos_com_tipo_invalido(self):
        """Ignorar tipos inválidos em fragmentos permitiria que um delta posterior os mascarasse."""
        invalid_deltas = (
            {"index": 0, "id": 1, "function": {"name": "weather", "arguments": "{}"}},
            {"index": 0, "id": "call_1", "function": {"name": 1, "arguments": "{}"}},
            {"index": 0, "id": "call_1", "function": {"name": "weather", "arguments": {}}},
        )
        for delta in invalid_deltas:
            with self.subTest(delta=delta):
                def handler(_request: httpx.Request, delta: dict[str, object] = delta) -> httpx.Response:
                    return httpx.Response(
                        200,
                        content=sse(
                            {"choices": [{"delta": {"tool_calls": [delta]}, "finish_reason": None}]},
                            {"choices": [{"delta": {"content": "não chegar"}, "finish_reason": "stop"}]},
                            "[DONE]",
                        ),
                    )

                observed = []
                async with client_for(httpx.MockTransport(handler)) as client:
                    with self.assertRaises(ProviderError) as context:
                        async for event in OpenAICompatibleAdapter(
                            DEEPSEEK_PROFILE, client, "secret"
                        ).stream(simple_request()):
                            observed.append(event)

                self.assertIs(context.exception.kind, ProviderErrorKind.INCOMPATIBLE)
                self.assertFalse(context.exception.retryable)
                self.assertEqual(observed, [])

    async def test_fragmento_anterior_nao_mascara_arguments_objeto_no_delta_terminal(self):
        """Concatenar somente strings não pode aceitar objeto inválido após argumentos já válidos."""

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
                                            "id": "call_1",
                                            "function": {"name": "weather", "arguments": "{}"},
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
                                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": {}}}]},
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                    "[DONE]",
                ),
            )

        observed = []
        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                async for event in OpenAICompatibleAdapter(
                    DEEPSEEK_PROFILE, client, "secret"
                ).stream(simple_request()):
                    observed.append(event)

        self.assertIs(context.exception.kind, ProviderErrorKind.INCOMPATIBLE)
        self.assertEqual(observed, [])

    async def test_fragmento_incremental_sem_campos_opcionais_permanece_valido(self):
        """A ausência de id/nome/arguments em delta posterior é normal e não deve falhar."""

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
                                            "id": "call_1",
                                            "function": {"name": "weather", "arguments": "{}"},
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ]
                    },
                    {"choices": [{"delta": {"tool_calls": [{"index": 0}]}, "finish_reason": "tool_calls"}]},
                    "[DONE]",
                ),
            )

        async with client_for(httpx.MockTransport(handler)) as client:
            events = await collect(OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, "secret").stream(simple_request()))

        self.assertEqual([event.kind for event in events], ["tool_call", "finish"])
        self.assertEqual(events[0].tool_call, CanonicalToolCall("call_1", "weather", "{}"))

    async def test_done_interrompe_eventos_tardios_e_redige_erro_upstream(self):
        """Consumir dados após o terminal ou corpo de erro poderia corromper histórico e vazar segredo."""
        secret = "compatible-error-sentinel"  # noqa: S105

        def terminal_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=sse(
                    {"choices": [{"delta": {"content": "antes"}, "finish_reason": "stop"}]},
                    "[DONE]",
                    {"choices": [{"delta": {"content": "tarde"}, "finish_reason": None}]},
                ),
            )

        async with client_for(httpx.MockTransport(terminal_handler)) as client:
            events = await collect(OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, secret).stream(simple_request()))
        self.assertEqual([event.kind for event in events], ["text_delta", "finish"])
        self.assertEqual(events[0].text, "antes")

        def error_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": f"bad key {secret}"}})

        async with client_for(httpx.MockTransport(error_handler)) as client:
            with self.assertRaises(ProviderError) as context:
                await collect(OpenAICompatibleAdapter(DEEPSEEK_PROFILE, client, secret).stream(simple_request()))
        self.assertIs(context.exception.kind, ProviderErrorKind.AUTH)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception))

    async def test_redirect_nao_e_seguido(self):
        """Seguir redirect de endpoint customizado permitiria sair do alvo administrativo confiado."""
        profile = custom_profile(base_url="http://127.0.0.1:8000/v1")

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, "127.0.0.1")
            return httpx.Response(302, headers={"location": "http://metadata.internal/"})

        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as context:
                await collect(OpenAICompatibleAdapter(profile, client, "secret").stream(simple_request("custom")))
        self.assertIs(context.exception.kind, ProviderErrorKind.INCOMPATIBLE)

    async def test_cancelamento_propagado(self):
        """Converter cancelamento em erro de rede impediria encerrar um turno solicitado pelo usuário."""
        profile = custom_profile(base_url="http://127.0.0.1:8000/v1")

        def handler(_request: httpx.Request) -> httpx.Response:
            raise asyncio.CancelledError

        async with client_for(httpx.MockTransport(handler)) as client:
            with self.assertRaises(asyncio.CancelledError):
                await collect(OpenAICompatibleAdapter(profile, client, "secret").stream(simple_request("custom")))
