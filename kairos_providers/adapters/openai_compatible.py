"""Adapter canônico para o protocolo OpenAI Chat Completions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
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
from kairos_providers.curated_catalog import curated_models
from kairos_providers.provider_profiles import OpenAICompatibleProfile

__all__ = ["OpenAICompatibleAdapter"]


_NON_CHAT_MODEL_MARKERS = (
    "embedding",
    "moderation",
    "audio",
    "image",
    "vision",
    "tts",
    "whisper",
    "rerank",
)


class OpenAICompatibleAdapter:
    """Converte Chat Completions/SSE em eventos canônicos, por perfil."""

    __slots__ = ("_api_key", "_http", "descriptor", "profile")

    def __init__(
        self,
        profile: OpenAICompatibleProfile,
        http: httpx.AsyncClient,
        api_key: str | None,
    ) -> None:
        self.profile = profile
        self._http = http
        self._api_key = api_key
        self.descriptor = ProviderDescriptor(profile.id, profile.name, ("api_key",))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(profile={self.profile.id!r}, http=<configured>, api_key=<redacted>)"

    async def discover_models(self) -> tuple[CatalogModel, ...]:
        response = await self._request("GET", self.profile.models_url)
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
            if not _is_chat_model(model_id):
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
            display_name = record.get("display_name") if isinstance(record, dict) else None
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
        if not self._api_key:
            return ConnectionStatus(
                False,
                self.descriptor.id,
                "credencial inválida ou ausente",
                0,
                state="unavailable",
            )
        try:
            models = await self.discover_models()
        except ProviderError as exc:
            return ConnectionStatus(False, self.descriptor.id, exc.message, 0)
        return ConnectionStatus(
            True,
            self.descriptor.id,
            f"Conexão com {self.descriptor.display_name} validada",
            len(models),
        )

    async def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]:
        payload = _payload_for(request, self.profile)
        try:
            async with self._http.stream(
                "POST",
                f"{self.profile.base_url}/chat/completions",
                headers=self._headers(streaming=True),
                json=payload,
                follow_redirects=False,
            ) as response:
                self._raise_for_status(response)
                async for event in _events_from_sse(response):
                    yield event
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc

    async def _request(self, method: str, url: str) -> httpx.Response:
        try:
            response = await self._http.request(
                method,
                url,
                headers=self._headers(streaming=False),
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        self._raise_for_status(response)
        return response

    def _headers(self, *, streaming: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if streaming else "application/json",
        }
        headers.update(self.profile.headers)
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {301, 302, 303, 307, 308}:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
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


def _payload_for(request: AdapterRequest, profile: OpenAICompatibleProfile) -> dict[str, Any]:
    if request.model.provider != profile.id:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    if request.tools and request.model.model in profile.tool_unsupported_models:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    payload = _parameters_for(request.parameters, profile.parameters_for(request.model.model))
    payload.update(
        {
            "model": request.model.model,
            "messages": [_message_for(message) for message in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    )
    if request.tools:
        payload["tools"] = [_tool_for(tool) for tool in request.tools]
    return payload


def _parameters_for(parameters: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    payload = {name: value for name, value in parameters.items() if name in allowed}
    max_tokens = payload.get("max_tokens")
    if max_tokens is not None and (
        not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0
    ):
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    return payload


def _message_for(message: CanonicalMessage) -> dict[str, Any]:
    text = "".join(_text_parts(message.content))
    if message.role not in {"system", "developer", "user", "assistant", "tool"}:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    if message.role == "tool":
        if message.tool_call_id is None or message.tool_calls:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": text}
    if message.tool_call_id is not None:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    normalized: dict[str, Any] = {"role": message.role, "content": text}
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
        "function": {"name": call.name, "arguments": call.arguments},
    }


def _tool_for(tool: Mapping[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    if not isinstance(function, dict):
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    name = function.get("name")
    if tool.get("type") != "function" or not isinstance(name, str) or not name:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    normalized: dict[str, Any] = {"type": "function", "function": {"name": name}}
    for key in ("description", "parameters", "strict"):
        value = function.get(key)
        if value is not None:
            normalized["function"][key] = value
    return normalized


def _is_chat_model(model_id: object) -> bool:
    return isinstance(model_id, str) and bool(model_id.strip()) and not any(
        marker in model_id.casefold() for marker in _NON_CHAT_MODEL_MARKERS
    )


def _remember_tool_calls(delta: Mapping[str, Any], pending: dict[int, dict[str, str]]) -> None:
    calls = delta.get("tool_calls")
    if calls is None:
        return
    if not isinstance(calls, list):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    for item in calls:
        if not isinstance(item, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        current = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call_id = item.get("id")
        if isinstance(call_id, str) and call_id:
            current["id"] = call_id
        function = item.get("function")
        if function is None:
            continue
        if not isinstance(function, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        name = function.get("name")
        if isinstance(name, str) and name:
            current["name"] = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            current["arguments"] += arguments


def _take_pending_calls(pending: dict[int, dict[str, str]]) -> tuple[CanonicalToolCall, ...]:
    calls = tuple(
        CanonicalToolCall(call["id"], call["name"], call["arguments"])
        for _, call in sorted(pending.items())
        if call["id"] and call["name"]
    )
    pending.clear()
    return calls


def _usage_from(document: Mapping[str, Any]) -> TokenUsage | None:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    return TokenUsage(
        input_tokens=_int_or_zero(usage.get("prompt_tokens")),
        output_tokens=_int_or_zero(usage.get("completion_tokens")),
        cache_read_tokens=_int_or_zero(
            prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None
        ),
        reasoning_tokens=_int_or_zero(
            completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
        ),
    )


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


@dataclass
class _SSEState:
    pending_calls: dict[int, dict[str, str]] = field(default_factory=dict)
    usage: TokenUsage | None = None
    finish_reason: str | None = None


async def _events_from_sse(response: httpx.Response) -> AsyncIterator[ProviderEvent]:
    state = _SSEState()
    async for document in _sse_documents(response):
        if document is None:
            for event in _final_events(state):
                yield event
            return
        for event in _events_from_document(document, state):
            yield event
    raise ProviderError(ProviderErrorKind.NETWORK, retryable=True)


def _events_from_document(
    document: Mapping[str, Any], state: _SSEState
) -> tuple[ProviderEvent, ...]:
    if document.get("error") is not None:
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    usage = _usage_from(document)
    if usage is not None:
        state.usage = usage
    if state.finish_reason is not None:
        return ()
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices:
        return ()
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    _remember_tool_calls(delta, state.pending_calls)
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None:
        if not isinstance(finish_reason, str) or not finish_reason:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        state.finish_reason = finish_reason
    text = delta.get("content")
    return (ProviderEvent(kind="text_delta", text=text),) if isinstance(text, str) and text else ()


def _final_events(state: _SSEState) -> tuple[ProviderEvent, ...]:
    if state.pending_calls and state.finish_reason != "tool_calls":
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    events = [ProviderEvent(kind="tool_call", tool_call=call) for call in _take_pending_calls(state.pending_calls)]
    if state.usage is not None:
        events.append(ProviderEvent(kind="usage", usage=state.usage))
    events.append(ProviderEvent(kind="finish", finish_reason=state.finish_reason or "stop"))
    return tuple(events)


async def _sse_documents(response: httpx.Response) -> AsyncIterator[dict[str, Any] | None]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line:
            continue
        document = _parse_sse_data(data_lines)
        if document is not _NO_EVENT:
            yield document
        data_lines.clear()
    document = _parse_sse_data(data_lines)
    if document is not _NO_EVENT:
        yield document


_NO_EVENT = object()


def _parse_sse_data(data_lines: list[str]) -> dict[str, Any] | object | None:
    if not data_lines:
        return _NO_EVENT
    if data_lines == ["[DONE]"]:
        return None
    try:
        document = json.loads("\n".join(data_lines))
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc
    if not isinstance(document, dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    return document
