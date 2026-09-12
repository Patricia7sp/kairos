"""Adapter nativo da OpenRouter para catálogo e Chat Completions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

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
from kairos_providers.adapters._json import wire_json
from kairos_providers.base import ConnectionStatus, TokenUsage
from kairos_providers.contracts import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelPrice,
    ProviderDescriptor,
    ProviderModelRef,
)

__all__ = ["OpenRouterAdapter", "OpenRouterRoutingPolicy"]


_OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_SUPPORTED_REQUEST_PARAMETERS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "max_tokens",
        "frequency_penalty",
        "presence_penalty",
        "repetition_penalty",
        "seed",
        "stop",
        "response_format",
        "reasoning",
        "include_reasoning",
        "structured_outputs",
    }
)


@dataclass(frozen=True)
class OpenRouterRoutingPolicy:
    """Preferências explícitas de endpoints; nunca selecionam outro ID de modelo."""

    data_collection: str = "deny"
    require_parameters: bool = True
    allow_fallbacks: bool = True

    def __post_init__(self) -> None:
        if (
            self.data_collection not in {"deny", "allow"}
            or type(self.require_parameters) is not bool
            or type(self.allow_fallbacks) is not bool
        ):
            raise ValueError("política de roteamento inválida")

    @classmethod
    def from_parameters(cls, value: object) -> OpenRouterRoutingPolicy:
        if not isinstance(value, Mapping) or set(value) - {
            "data_collection",
            "require_parameters",
            "allow_fallbacks",
        }:
            raise ValueError("política de roteamento inválida")
        try:
            return cls(**value)
        except (TypeError, ValueError) as exc:
            raise ValueError("política de roteamento inválida") from exc

    def as_payload(self) -> dict[str, str | bool]:
        return {
            "data_collection": self.data_collection,
            "require_parameters": self.require_parameters,
            "allow_fallbacks": self.allow_fallbacks,
        }


class OpenRouterAdapter:
    """Converte OpenRouter Chat Completions/SSE em eventos canônicos seguros."""

    __slots__ = ("_api_key", "_http", "_referer", "_title")

    descriptor = ProviderDescriptor("openrouter", "OpenRouter", ("api_key",))

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        *,
        referer: str | None = None,
        title: str | None = None,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._referer = _validated_referer(referer)
        self._title = _validated_title(title)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(http=<configured>, api_key=<redacted>)"

    async def discover_models(self) -> tuple[CatalogModel, ...]:
        response = await self._request(
            "GET", "/models?output_modalities=text&input_modalities=text"
        )
        try:
            document = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc
        records = document.get("data") if isinstance(document, dict) else None
        if not isinstance(records, list):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        return tuple(model for record in records if (model := _catalog_model(record)) is not None)

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
            # The model catalog is public; only this authenticated endpoint
            # proves the supplied credential is accepted. Never expose its body.
            await self._request("GET", "/key")
            models = await self.discover_models()
        except ProviderError as exc:
            return ConnectionStatus(False, self.descriptor.id, exc.message, 0)
        return ConnectionStatus(
            True, self.descriptor.id, "Conexão com OpenRouter validada", len(models)
        )

    async def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]:
        payload = _payload_for(request)
        try:
            async with self._http.stream(
                "POST",
                f"{_OPENROUTER_API_BASE}/chat/completions",
                headers=self._headers(streaming=True),
                json=wire_json(payload),
                follow_redirects=False,
            ) as response:
                if response.status_code == 404 and await _blocked_by_policy(response):
                    raise ProviderError(ProviderErrorKind.POLICY, retryable=False)
                self._raise_for_status(response)
                async for event in _events_from_sse(response):
                    yield event
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc

    async def _request(self, method: str, path: str) -> httpx.Response:
        try:
            response = await self._http.request(
                method,
                f"{_OPENROUTER_API_BASE}{path}",
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
        if self._referer is not None:
            headers["HTTP-Referer"] = self._referer
        if self._title is not None:
            headers["X-OpenRouter-Title"] = self._title
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {301, 302, 303, 307, 308, 400, 405, 409, 422}:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        if status in {401, 403}:
            raise ProviderError(ProviderErrorKind.AUTH, retryable=False)
        if status == 404:
            raise ProviderError(ProviderErrorKind.MODEL, retryable=False)
        if status == 402:
            raise ProviderError(ProviderErrorKind.LIMIT, retryable=False)
        if status == 429:
            raise ProviderError(ProviderErrorKind.RATE_LIMIT, retryable=True)
        if status in {408, 504}:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True)
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=status >= 500)


async def _blocked_by_policy(response: httpx.Response) -> bool:
    """Classify a bounded error body; never expose the upstream's text."""
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=4096):
        if len(body) + len(chunk) > 16384:
            return False
        body.extend(chunk)
    try:
        document = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    error = document.get("error") if isinstance(document, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return isinstance(message, str) and message.startswith(
        (
            "No endpoints found matching your data policy",
            "No endpoints found that match your data policy",
        )
    )


def _catalog_model(record: object) -> CatalogModel | None:
    if not isinstance(record, dict):
        return None
    model_id = record.get("id")
    architecture = record.get("architecture")
    if not isinstance(model_id, str) or not model_id.strip() or not isinstance(architecture, dict):
        return None
    input_modalities = _strings(architecture.get("input_modalities"))
    output_modalities = _strings(architecture.get("output_modalities"))
    if "text" not in input_modalities or "text" not in output_modalities:
        return None
    supported_parameters = _strings(record.get("supported_parameters"))
    pricing = record.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    display_name = record.get("name")
    expiration_date = record.get("expiration_date")
    return CatalogModel(
        ref=ProviderModelRef("openrouter", model_id),
        display_name=display_name if isinstance(display_name, str) and display_name else model_id,
        capabilities=ModelCapabilities(
            chat=True,
            tools="tools" in supported_parameters,
            vision="image" in input_modalities,
            streaming=True,
            context_length=_positive_int(record.get("context_length")),
        ),
        origins=frozenset({CatalogOrigin.DYNAMIC}),
        price=ModelPrice(
            prompt=_decimal_or_none(pricing.get("prompt")),
            completion=_decimal_or_none(pricing.get("completion")),
            request=_decimal_or_none(pricing.get("request")),
        ),
        supported_parameters=supported_parameters,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        expiration_date=expiration_date if isinstance(expiration_date, str) else None,
    )


def _payload_for(request: AdapterRequest) -> dict[str, Any]:
    if request.model.provider != "openrouter":
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    try:
        policy = OpenRouterRoutingPolicy.from_parameters(request.parameters.get("routing", {}))
    except ValueError as exc:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False) from exc
    payload = {
        name: value
        for name, value in request.parameters.items()
        if name in _SUPPORTED_REQUEST_PARAMETERS
    }
    payload.update(
        {
            "model": request.model.model,
            "messages": [_message_for(message) for message in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "provider": policy.as_payload(),
        }
    )
    if request.tools:
        payload["tools"] = [_tool_for(tool) for tool in request.tools]
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
        if (value := function.get(key)) is not None:
            normalized["function"][key] = value
    return normalized


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
    if (usage := _usage_from(document)) is not None:
        state.usage = usage
    if state.finish_reason is not None:
        return ()
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices:
        return ()
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(delta := choice.get("delta"), dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    _remember_tool_calls(delta, state.pending_calls)
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None:
        if not isinstance(finish_reason, str) or not finish_reason:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        state.finish_reason = finish_reason
    text = delta.get("content")
    return (ProviderEvent(kind="text_delta", text=text),) if isinstance(text, str) and text else ()


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
        if (call_id := _explicit_string(item, "id")) is not None:
            current["id"] = call_id
        if "function" not in item:
            continue
        function = item["function"]
        if not isinstance(function, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        if (name := _explicit_string(function, "name")) is not None:
            current["name"] = name
        if (arguments := _explicit_string(function, "arguments")) is not None:
            current["arguments"] += arguments


def _explicit_string(record: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in record:
        return None
    value = record[field_name]
    if not isinstance(value, str):
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    return value


def _final_events(state: _SSEState) -> tuple[ProviderEvent, ...]:
    if state.pending_calls and state.finish_reason != "tool_calls":
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    events = [
        ProviderEvent(kind="tool_call", tool_call=call)
        for call in _take_pending_calls(state.pending_calls)
    ]
    if state.usage is not None:
        events.append(ProviderEvent(kind="usage", usage=state.usage))
    events.append(ProviderEvent(kind="finish", finish_reason=state.finish_reason or "stop"))
    return tuple(events)


def _take_pending_calls(pending: dict[int, dict[str, str]]) -> tuple[CanonicalToolCall, ...]:
    calls = tuple(_validated_tool_call(call) for _, call in sorted(pending.items()))
    pending.clear()
    return calls


def _validated_tool_call(call: Mapping[str, str]) -> CanonicalToolCall:
    call_id, name, arguments = call["id"], call["name"], call["arguments"]
    if not call_id or not name:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    try:
        parsed_arguments = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False) from exc
    if not isinstance(parsed_arguments, dict):
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    return CanonicalToolCall(call_id, name, arguments)


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


def _usage_from(document: Mapping[str, Any]) -> TokenUsage | None:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    return TokenUsage(
        input_tokens=_nonnegative_int(usage.get("prompt_tokens")),
        output_tokens=_nonnegative_int(usage.get("completion_tokens")),
        cache_read_tokens=_nonnegative_int(
            prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None
        ),
        reasoning_tokens=_nonnegative_int(
            completion_details.get("reasoning_tokens")
            if isinstance(completion_details, dict)
            else None
        ),
    )


def _strings(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str) and item)


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _decimal_or_none(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        decimal = Decimal(str(value))
    except InvalidOperation:
        return None
    return decimal if decimal.is_finite() else None


def _validated_referer(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_048
        or any(c in value for c in "\r\n")
    ):
        raise ValueError("HTTP-Referer inválido")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("HTTP-Referer inválido")
    return value


def _validated_title(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or any(c in value for c in "\r\n")
    ):
        raise ValueError("X-OpenRouter-Title inválido")
    return value
