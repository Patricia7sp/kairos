"""Adapter nativo da OpenAI Responses API para o contrato canônico."""

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

__all__ = ["OpenAIResponsesAdapter"]


_OPENAI_API_BASE = "https://api.openai.com/v1"
_NON_CHAT_MODEL_MARKERS = (
    "embedding",
    "moderation",
    "transcribe",
    "tts",
    "whisper",
    "dall-e",
    "image",
    "sora",
    "realtime",
    "audio",
)


class OpenAIResponsesAdapter:
    """Converte Responses/SSE em eventos canônicos sem reter payloads externos."""

    __slots__ = ("_api_key", "_http")

    descriptor = ProviderDescriptor("openai", "OpenAI", ("api_key",))

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    def __repr__(self) -> str:
        return f"{type(self).__name__}(http=<configured>, api_key=<redacted>)"

    async def discover_models(self) -> tuple[CatalogModel, ...]:
        """Lista modelos disponíveis, enriquecendo capacidades conhecidas pela curadoria."""
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
            if not isinstance(model_id, str) or not model_id.strip() or not _is_chat_text_model(model_id):
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

            models.append(
                CatalogModel(
                    ref=ProviderModelRef(self.descriptor.id, model_id),
                    display_name=model_id,
                    capabilities=ModelCapabilities(chat=True, streaming=True),
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
            "Conexão com OpenAI validada",
            len(models),
        )

    async def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]:
        """Envia uma requisição Responses e normaliza seus eventos SSE."""
        payload = self._payload_for(request)
        pending_calls: dict[str, dict[str, str]] = {}
        finished = False
        try:
            async with self._http.stream(
                "POST", f"{_OPENAI_API_BASE}/responses", headers=self._headers(), json=payload
            ) as response:
                self._raise_for_status(response)
                async for event in _sse_events(response):
                    normalized, completed = _normalize_event(event, pending_calls)
                    for canonical in normalized:
                        yield canonical
                    finished = finished or completed
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc

        if not finished:
            for call in _take_pending_calls(pending_calls):
                yield ProviderEvent(kind="tool_call", tool_call=call)
            yield ProviderEvent(kind="finish", finish_reason="stop")

    async def _request(self, method: str, path: str) -> httpx.Response:
        try:
            response = await self._http.request(
                method, f"{_OPENAI_API_BASE}{path}", headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        self._raise_for_status(response)
        return response

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "text/event-stream"}

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

    def _payload_for(self, request: AdapterRequest) -> dict[str, Any]:
        if request.model.provider != self.descriptor.id:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        payload = dict(request.parameters)
        payload.update(
            {
                "model": request.model.model,
                "input": _input_for(request),
                "tools": [_tool_for(tool) for tool in request.tools],
                "stream": True,
            }
        )
        if not request.tools:
            payload.pop("tools")
        return payload


def _is_chat_text_model(model_id: str) -> bool:
    normalized = model_id.casefold()
    return not any(marker in normalized for marker in _NON_CHAT_MODEL_MARKERS)


def _input_for(request: AdapterRequest) -> list[dict[str, Any]]:
    input_items: list[dict[str, Any]] = []
    for message in request.messages:
        text_parts = []
        for part in message.content:
            if part.kind != "text" or not isinstance(part.value, str):
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            text_parts.append({"type": "input_text", "text": part.value})

        if message.role == "tool":
            if message.tool_call_id is None:
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": "".join(part["text"] for part in text_parts),
                }
            )
        elif text_parts:
            input_items.append({"role": message.role, "content": text_parts})

        for call in message.tool_calls:
            input_items.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )
    return input_items


def _tool_for(tool: Mapping[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    source = function if isinstance(function, dict) else tool
    name = source.get("name") if isinstance(source, dict) else None
    if tool.get("type") != "function" or not isinstance(name, str) or not name:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    normalized: dict[str, Any] = {"type": "function", "name": name}
    for field in ("description", "parameters", "strict"):
        value = source.get(field)
        if value is not None:
            normalized[field] = value
    return normalized


def _normalize_event(
    event: Mapping[str, Any], pending_calls: dict[str, dict[str, str]]
) -> tuple[tuple[ProviderEvent, ...], bool]:
    event_type = event.get("type")
    if event_type == "response.output_text.delta":
        return _text_events(event), False
    if event_type == "response.output_item.added":
        _remember_function_call(event, pending_calls)
        return (), False
    if event_type == "response.function_call_arguments.delta":
        _append_function_call_arguments(event, pending_calls)
        return (), False
    if event_type == "response.function_call_arguments.done":
        return _finished_function_call(event, pending_calls), False
    if event_type == "response.completed":
        return _completed_events(event, pending_calls), True
    if event_type in {"error", "response.failed", "response.incomplete"}:
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    return (), False


def _text_events(event: Mapping[str, Any]) -> tuple[ProviderEvent, ...]:
    delta = event.get("delta")
    return (ProviderEvent(kind="text_delta", text=delta),) if isinstance(delta, str) and delta else ()


def _remember_function_call(
    event: Mapping[str, Any], pending_calls: dict[str, dict[str, str]]
) -> None:
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return
    item_id = _event_item_id(event, item)
    if item_id is None:
        return
    pending_calls[item_id] = {
        "id": _string_or(item.get("call_id"), item_id),
        "name": _string_or(item.get("name")),
        "arguments": _string_or(item.get("arguments")),
    }


def _append_function_call_arguments(
    event: Mapping[str, Any], pending_calls: dict[str, dict[str, str]]
) -> None:
    item_id = _event_item_id(event)
    delta = event.get("delta")
    if item_id is None or not isinstance(delta, str):
        return
    call = pending_calls.setdefault(
        item_id,
        {
            "id": _string_or(event.get("call_id"), item_id),
            "name": _string_or(event.get("name")),
            "arguments": "",
        },
    )
    call["id"] = _string_or(event.get("call_id"), call["id"])
    call["name"] = _string_or(event.get("name"), call["name"])
    call["arguments"] += delta


def _finished_function_call(
    event: Mapping[str, Any], pending_calls: dict[str, dict[str, str]]
) -> tuple[ProviderEvent, ...]:
    item_id = _event_item_id(event)
    if item_id is None:
        return ()
    call = pending_calls.pop(item_id, {})
    call["id"] = _string_or(event.get("call_id"), call.get("id", item_id))
    call["name"] = _string_or(event.get("name"), call.get("name", ""))
    call["arguments"] = _string_or(event.get("arguments"), call.get("arguments", ""))
    if not call["name"]:
        return ()
    return (
        ProviderEvent(
            kind="tool_call",
            tool_call=CanonicalToolCall(
                id=call["id"], name=call["name"], arguments=call["arguments"]
            ),
        ),
    )


def _completed_events(
    event: Mapping[str, Any], pending_calls: dict[str, dict[str, str]]
) -> tuple[ProviderEvent, ...]:
    events = [ProviderEvent(kind="tool_call", tool_call=call) for call in _take_pending_calls(pending_calls)]
    usage = _usage_from(event)
    if usage is not None:
        events.append(ProviderEvent(kind="usage", usage=usage))
    response_data = event.get("response")
    status = response_data.get("status") if isinstance(response_data, dict) else None
    events.append(
        ProviderEvent(
            kind="finish", finish_reason=status if isinstance(status, str) else "completed"
        )
    )
    return tuple(events)


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


def _event_item_id(event: Mapping[str, Any], item: Mapping[str, Any] | None = None) -> str | None:
    value = event.get("item_id")
    if not isinstance(value, str) and item is not None:
        value = item.get("id")
    return value if isinstance(value, str) and value else None


def _string_or(value: object, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _take_pending_calls(calls: dict[str, dict[str, str]]) -> tuple[CanonicalToolCall, ...]:
    pending = tuple(
        CanonicalToolCall(id=call["id"], name=call["name"], arguments=call["arguments"])
        for call in calls.values()
        if call["name"]
    )
    calls.clear()
    return pending


def _usage_from(event: Mapping[str, Any]) -> TokenUsage | None:
    response = event.get("response")
    usage = response.get("usage") if isinstance(response, dict) else event.get("usage")
    if not isinstance(usage, dict):
        return None
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    return TokenUsage(
        input_tokens=_int_or_zero(usage.get("input_tokens")),
        output_tokens=_int_or_zero(usage.get("output_tokens")),
        cache_read_tokens=_int_or_zero(
            input_details.get("cached_tokens") if isinstance(input_details, dict) else None
        ),
        reasoning_tokens=_int_or_zero(
            output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None
        ),
    )


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
