"""Adapter nativo da Anthropic Messages API para o contrato canônico."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from kairos_providers.adapter_contract import (
    AdapterRequest,
    CanonicalToolCall,
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
from kairos_providers.curated_catalog import curated_models

__all__ = ["AnthropicMessagesAdapter"]


_ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 8_192


class AnthropicMessagesAdapter:
    """Converte Messages/SSE em eventos canônicos sem reter payloads externos."""

    __slots__ = ("_api_key", "_http")

    descriptor = ProviderDescriptor("anthropic", "Anthropic", ("api_key",))

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    def __repr__(self) -> str:
        return f"{type(self).__name__}(http=<configured>, api_key=<redacted>)"

    async def discover_models(self) -> tuple[CatalogModel, ...]:
        """Lista modelos Claude disponíveis e preserva metadados da curadoria."""
        response = await self._request("GET", "/models")
        try:
            document = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc

        records = document.get("data") if isinstance(document, dict) else None
        if not isinstance(records, list):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)

        curated = {
            model.ref.model: model
            for model in curated_models()
            if model.ref.provider == self.descriptor.id
        }
        models: list[CatalogModel] = []
        for record in records:
            model_id = record.get("id") if isinstance(record, dict) else None
            if not _is_claude_model(model_id):
                continue

            known = curated.get(model_id)
            if known is not None:
                models.append(
                    CatalogModel(
                        ref=known.ref,
                        display_name=known.display_name,
                        capabilities=known.capabilities,
                        stability=known.stability,
                        origins=known.origins | frozenset({CatalogOrigin.DYNAMIC}),
                        price=known.price,
                    )
                )
                continue

            display_name = record.get("display_name")
            models.append(
                CatalogModel(
                    ref=ProviderModelRef(self.descriptor.id, model_id),
                    display_name=display_name if isinstance(display_name, str) else model_id,
                    capabilities=ModelCapabilities(chat=True, tools=False, streaming=True),
                    origins=frozenset({CatalogOrigin.DYNAMIC}),
                )
            )
        return tuple(models)

    async def test_connection(self) -> ConnectionStatus:
        try:
            models = await self.discover_models()
        except ProviderError as exc:
            return ConnectionStatus(False, self.descriptor.id, exc.message, 0)
        return ConnectionStatus(
            True,
            self.descriptor.id,
            "Conexão com Anthropic validada",
            len(models),
        )

    async def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]:  # noqa: PLR0912
        """Envia uma requisição Messages e normaliza seus eventos SSE."""
        payload = _payload_for(request, self.descriptor.id)
        pending_calls: dict[int, _PendingToolCall] = {}
        input_tokens = 0
        cache_read_tokens = 0
        finish_reason = "stop"
        usage_events: list[TokenUsage] = []
        finished = False
        try:
            async with self._http.stream(
                "POST",
                f"{_ANTHROPIC_API_BASE}/messages",
                headers=self._headers(streaming=True),
                json=payload,
            ) as response:
                self._raise_for_status(response)
                async for event in _sse_events(response):
                    event_type = event.get("type")
                    if event_type == "message_start":
                        input_tokens, cache_read_tokens = _input_usage(event)
                    elif event_type == "content_block_start":
                        _remember_tool_call(event, pending_calls)
                    elif event_type == "content_block_delta":
                        text = _text_delta(event)
                        if text:
                            yield ProviderEvent(kind="text_delta", text=text)
                        _append_tool_arguments(event, pending_calls)
                    elif event_type == "message_delta":
                        finish_reason = _finish_reason(event, finish_reason)
                        usage = _usage_from(event, input_tokens, cache_read_tokens)
                        if usage is not None:
                            usage_events.append(usage)
                    elif event_type == "message_stop":
                        for call in _take_pending_calls(pending_calls):
                            yield ProviderEvent(kind="tool_call", tool_call=call)
                        for usage in usage_events:
                            yield ProviderEvent(kind="usage", usage=usage)
                        yield ProviderEvent(kind="finish", finish_reason=finish_reason)
                        finished = True
                        break
                    elif event_type == "error":
                        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc

        if not finished:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True)

    async def _request(self, method: str, path: str) -> httpx.Response:
        try:
            response = await self._http.request(
                method, f"{_ANTHROPIC_API_BASE}{path}", headers=self._headers(streaming=False)
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        self._raise_for_status(response)
        return response

    def _headers(self, *, streaming: bool) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "text/event-stream" if streaming else "application/json",
        }

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        status = response.status_code
        if status in {401, 403}:
            raise ProviderError(ProviderErrorKind.AUTH, retryable=False)
        if status == 404:
            raise ProviderError(ProviderErrorKind.MODEL, retryable=False)
        if status == 402:
            raise ProviderError(ProviderErrorKind.LIMIT, retryable=False)
        if status == 429:
            raise ProviderError(ProviderErrorKind.RATE_LIMIT, retryable=True)
        if status in {400, 405, 409, 422}:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        if status in {408, 504}:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True)
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=status >= 500)


def _is_claude_model(model_id: object) -> bool:
    return isinstance(model_id, str) and model_id.startswith("claude-") and bool(model_id.strip())


def _payload_for(request: AdapterRequest, provider: str) -> dict[str, Any]:
    if request.model.provider != provider:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)

    system, messages = _messages_for(request)
    payload = dict(request.parameters)
    payload.update(
        {
            "model": request.model.model,
            "messages": messages,
            "max_tokens": _max_tokens(request.parameters),
            "stream": True,
        }
    )
    if system:
        payload["system"] = system
    else:
        payload.pop("system", None)
    if request.tools:
        payload["tools"] = [_tool_for(tool) for tool in request.tools]
    else:
        payload.pop("tools", None)
    return payload


def _max_tokens(parameters: Mapping[str, Any]) -> int:
    value = parameters.get("max_tokens", _DEFAULT_MAX_TOKENS)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)


def _messages_for(request: AdapterRequest) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        text_parts = _text_parts(message.content)
        if message.role == "system":
            if message.tool_call_id is not None or message.tool_calls:
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            system_parts.extend(text_parts)
            continue

        if message.role == "tool":
            if message.tool_call_id is None:
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            tool_result = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": "".join(text_parts),
            }
            if _is_tool_result_message(messages[-1] if messages else None):
                messages[-1]["content"].append(tool_result)
            else:
                messages.append({"role": "user", "content": [tool_result]})
            continue

        if message.role not in {"user", "assistant"}:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        if message.tool_calls and message.role != "assistant":
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)

        content: list[dict[str, Any]] = [{"type": "text", "text": text} for text in text_parts]
        content.extend(_tool_use_for(call) for call in message.tool_calls)
        if content:
            messages.append({"role": message.role, "content": content})
    return "\n".join(system_parts), messages


def _is_tool_result_message(message: dict[str, Any] | None) -> bool:
    if message is None or message.get("role") != "user":
        return False
    content = message.get("content")
    return isinstance(content, list) and all(
        isinstance(part, dict) and part.get("type") == "tool_result" for part in content
    )


def _text_parts(parts: tuple[Any, ...]) -> list[str]:
    text_parts: list[str] = []
    for part in parts:
        if part.kind != "text" or not isinstance(part.value, str):
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        text_parts.append(part.value)
    return text_parts


def _tool_use_for(call: CanonicalToolCall) -> dict[str, Any]:
    try:
        input_data = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False) from exc
    if not isinstance(input_data, dict):
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    return {"type": "tool_use", "id": call.id, "name": call.name, "input": input_data}


def _tool_for(tool: Mapping[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    source = function if isinstance(function, dict) else tool
    name = source.get("name") if isinstance(source, dict) else None
    if tool.get("type") != "function" or not isinstance(name, str) or not name:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    input_schema = source.get("parameters")
    if input_schema is None:
        input_schema = {"type": "object", "properties": {}}
    normalized: dict[str, Any] = {"name": name, "input_schema": input_schema}
    description = source.get("description")
    if isinstance(description, str):
        normalized["description"] = description
    return normalized


class _PendingToolCall:
    __slots__ = ("arguments", "id", "name")

    def __init__(self, id_: str, name: str, arguments: str) -> None:
        self.id = id_
        self.name = name
        self.arguments = arguments

    def as_canonical(self) -> CanonicalToolCall:
        return CanonicalToolCall(id=self.id, name=self.name, arguments=self.arguments or "{}")


def _remember_tool_call(event: Mapping[str, Any], calls: dict[int, _PendingToolCall]) -> None:
    index = _event_index(event)
    block = event.get("content_block")
    if index is None or not isinstance(block, dict) or block.get("type") != "tool_use":
        return
    id_ = block.get("id")
    name = block.get("name")
    input_data = block.get("input")
    if not isinstance(id_, str) or not id_ or not isinstance(name, str) or not name:
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    arguments = json.dumps(input_data, separators=(",", ":"), ensure_ascii=False) if input_data else ""
    calls[index] = _PendingToolCall(id_, name, arguments)


def _append_tool_arguments(event: Mapping[str, Any], calls: dict[int, _PendingToolCall]) -> None:
    index = _event_index(event)
    delta = event.get("delta")
    if index is None or not isinstance(delta, dict) or delta.get("type") != "input_json_delta":
        return
    partial_json = delta.get("partial_json")
    if index in calls and isinstance(partial_json, str):
        calls[index].arguments += partial_json


def _text_delta(event: Mapping[str, Any]) -> str:
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""


def _take_pending_calls(calls: dict[int, _PendingToolCall]) -> tuple[CanonicalToolCall, ...]:
    pending = tuple(call.as_canonical() for _, call in sorted(calls.items()))
    calls.clear()
    return pending


def _event_index(event: Mapping[str, Any]) -> int | None:
    index = event.get("index")
    return index if isinstance(index, int) and not isinstance(index, bool) else None


def _input_usage(event: Mapping[str, Any]) -> tuple[int, int]:
    message = event.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    return _int_or_zero(usage.get("input_tokens")), _int_or_zero(usage.get("cache_read_input_tokens"))


def _usage_from(
    event: Mapping[str, Any], input_tokens: int, cache_read_tokens: int
) -> TokenUsage | None:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=_int_or_zero(usage.get("output_tokens")),
        cache_read_tokens=cache_read_tokens,
    )


def _finish_reason(event: Mapping[str, Any], fallback: str) -> str:
    delta = event.get("delta")
    reason = delta.get("stop_reason") if isinstance(delta, dict) else None
    return reason if isinstance(reason, str) and reason else fallback


async def _sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line:
            continue
        if event := _parse_sse_data(data_lines):
            yield event
        data_lines.clear()
    if event := _parse_sse_data(data_lines):
        yield event


def _parse_sse_data(data_lines: list[str]) -> dict[str, Any] | None:
    if not data_lines or data_lines == ["[DONE]"]:
        return None
    try:
        event = json.loads("\n".join(data_lines))
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc
    return event if isinstance(event, dict) else None


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
