"""Adapter nativo do daemon local Ollama."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from kairos_providers.adapter_contract import (
    AdapterRequest,
    CanonicalMessage,
    CanonicalToolCall,
    ContentPart,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
)
from kairos_providers.base import ConnectionStatus, TokenUsage
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ProviderDescriptor,
    ProviderModelRef,
)

__all__ = ["OllamaNativeAdapter"]


class OllamaNativeAdapter:
    """Converte `/api/chat` NDJSON no contrato canônico, sem credenciais."""

    __slots__ = ("_base_url", "_http")

    descriptor = ProviderDescriptor("ollama", "Ollama", ())

    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
        ):
            raise ValueError("base_url do Ollama é inválida")
        if url.query or url.fragment:
            raise ValueError("base_url do Ollama não pode conter credenciais")
        self._http = http
        self._base_url = str(url).rstrip("/")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(http=<configured>, base_url=<configured>)"

    async def discover_models(self) -> tuple[CatalogModel, ...]:
        response = await self._request("GET", "/api/tags")
        try:
            document = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc
        records = document.get("models") if isinstance(document, dict) else None
        if not isinstance(records, list):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        models: list[CatalogModel] = []
        for record in records:
            name = record.get("name") if isinstance(record, dict) else None
            if not isinstance(name, str) or not name.strip():
                continue
            models.append(
                CatalogModel(
                    ref=ProviderModelRef(self.descriptor.id, name),
                    display_name=name,
                    capabilities=ModelCapabilities(chat=True, tools=True, streaming=True),
                    origins=frozenset({CatalogOrigin.DYNAMIC}),
                )
            )
        return tuple(models)

    async def test_connection(self) -> ConnectionStatus:
        try:
            models = await self.discover_models()
        except ProviderError:
            return ConnectionStatus(
                False,
                self.descriptor.id,
                "Ollama indisponível",
                0,
                auth_method="local",
                state="unavailable",
            )
        return ConnectionStatus(
            True,
            self.descriptor.id,
            "Ollama ativo localmente",
            len(models),
            auth_method="local",
        )

    async def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]:
        payload = _payload_for(request, self.descriptor.id)
        pending_calls: dict[str, CanonicalToolCall] = {}
        finished = False
        try:
            async with self._http.stream("POST", self._url("/api/chat"), json=payload) as response:
                _raise_for_status(response)
                async for document in _ndjson_documents(response):
                    if document.get("error") is not None:
                        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
                    for event in _text_events(document):
                        yield event
                    _remember_tool_calls(document, pending_calls)
                    if document.get("done") is True:
                        for call in pending_calls.values():
                            yield ProviderEvent(kind="tool_call", tool_call=call)
                        pending_calls.clear()
                        usage = _usage_from(document)
                        if usage is not None:
                            yield ProviderEvent(kind="usage", usage=usage)
                        reason = document.get("done_reason")
                        yield ProviderEvent(
                            kind="finish",
                            finish_reason=reason if isinstance(reason, str) and reason else "stop",
                        )
                        finished = True
                        break
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        if not finished:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True)

    async def _request(self, method: str, path: str) -> httpx.Response:
        try:
            response = await self._http.request(method, self._url(path))
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        _raise_for_status(response)
        return response

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"


def _payload_for(request: AdapterRequest, provider: str) -> dict[str, Any]:
    if request.model.provider != provider:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    payload: dict[str, Any] = {
        "model": request.model.model,
        "messages": [_message_for(message) for message in request.messages],
        "stream": True,
    }
    if request.tools:
        payload["tools"] = [_tool_for(tool) for tool in request.tools]
    options = _options_for(request.parameters)
    if options:
        payload["options"] = options
    return payload


def _message_for(message: CanonicalMessage) -> dict[str, Any]:
    content = "".join(_text_parts(message.content))
    if message.role not in {"system", "user", "assistant", "tool"}:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    if message.role == "tool":
        if message.tool_call_id is None or message.tool_calls:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": content}
    if message.tool_call_id is not None:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    normalized: dict[str, Any] = {"role": message.role}
    if content:
        normalized["content"] = content
    if message.tool_calls:
        if message.role != "assistant":
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        normalized["tool_calls"] = [_tool_call_for(call) for call in message.tool_calls]
    return normalized


def _text_parts(parts: tuple[ContentPart, ...]) -> list[str]:
    values: list[str] = []
    for part in parts:
        if part.kind != "text" or not isinstance(part.value, str):
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        values.append(part.value)
    return values


def _tool_call_for(call: CanonicalToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": _arguments_object(call.arguments)},
    }


def _arguments_object(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False) from exc
    if not isinstance(parsed, dict):
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    return parsed


def _tool_for(tool: Mapping[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    source = function if isinstance(function, dict) else tool
    name = source.get("name") if isinstance(source, dict) else None
    if tool.get("type") != "function" or not isinstance(name, str) or not name:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    normalized: dict[str, Any] = {"type": "function", "function": {"name": name}}
    for field in ("description", "parameters"):
        value = source.get(field)
        if value is not None:
            normalized["function"][field] = value
    return normalized


def _options_for(parameters: Mapping[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if "temperature" in parameters:
        options["temperature"] = parameters["temperature"]
    if "max_tokens" in parameters:
        value = parameters["max_tokens"]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        options["num_predict"] = value
    return options


def _text_events(document: Mapping[str, Any]) -> tuple[ProviderEvent, ...]:
    message = document.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return (ProviderEvent(kind="text_delta", text=content),) if isinstance(content, str) and content else ()


def _remember_tool_calls(
    document: Mapping[str, Any], pending_calls: dict[str, CanonicalToolCall]
) -> None:
    message = document.get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if calls is None:
        return
    if not isinstance(calls, list):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    for item in calls:
        if not isinstance(item, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        function = item.get("function")
        if not isinstance(function, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        name = function.get("name")
        arguments = function.get("arguments", {})
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"ollama-call-{len(pending_calls) + 1}"
        pending_calls[call_id] = CanonicalToolCall(
            call_id,
            name,
            json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
        )


def _usage_from(document: Mapping[str, Any]) -> TokenUsage | None:
    input_tokens = _nonnegative_int(document.get("prompt_eval_count"))
    output_tokens = _nonnegative_int(document.get("eval_count"))
    if input_tokens is None and output_tokens is None:
        return None
    return TokenUsage(input_tokens or 0, output_tokens or 0)


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


async def _ndjson_documents(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line:
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc
        if not isinstance(document, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        yield document


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    status = response.status_code
    if status in {401, 403}:
        raise ProviderError(ProviderErrorKind.AUTH, retryable=False)
    if status == 404:
        raise ProviderError(ProviderErrorKind.MODEL, retryable=False)
    if status == 429:
        raise ProviderError(ProviderErrorKind.RATE_LIMIT, retryable=True)
    if status in {400, 405, 409, 422}:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    if status in {408, 504}:
        raise ProviderError(ProviderErrorKind.NETWORK, retryable=True)
    raise ProviderError(ProviderErrorKind.INTERNAL, retryable=status >= 500)
