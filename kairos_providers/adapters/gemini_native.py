"""Adapter nativo da Gemini Generative Language API."""

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
from kairos_providers.curated_catalog import curated_models

__all__ = ["GeminiNativeAdapter"]


_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiNativeAdapter:
    """Converte GenerateContent/SSE no contrato canônico sem expor credenciais."""

    __slots__ = ("_api_key", "_http", "_oauth_token")

    descriptor = ProviderDescriptor("gemini", "Google Gemini", ("api_key", "oauth"))

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str | None = None,
        oauth_token: str | None = None,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._oauth_token = oauth_token

    def __repr__(self) -> str:
        auth = "oauth" if self._oauth_token else "api_key"
        return f"{type(self).__name__}(http=<configured>, auth={auth!r})"

    async def discover_models(self) -> tuple[CatalogModel, ...]:
        response = await self._request("GET", "/models")
        try:
            document = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc
        records = document.get("models") if isinstance(document, dict) else None
        if not isinstance(records, list):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)

        curated = {
            model.ref.model: model
            for model in curated_models()
            if model.ref.provider == self.descriptor.id
        }
        models: list[CatalogModel] = []
        for record in records:
            model_id = _model_id(record)
            if model_id is None:
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
            display_name = record.get("displayName")
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
        if not self._api_key and not self._oauth_token:
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
            return ConnectionStatus(False, self.descriptor.id, exc.message, 0, state="unavailable")
        return ConnectionStatus(
            True,
            self.descriptor.id,
            "Conexão com Google Gemini validada",
            len(models),
            auth_method="oauth" if self._oauth_token else "api_key",
        )

    async def stream(self, request: AdapterRequest) -> AsyncIterator[ProviderEvent]:
        payload = _payload_for(request, self.descriptor.id)
        pending_calls: dict[str, CanonicalToolCall] = {}
        latest_usage: TokenUsage | None = None
        finished = False
        try:
            async with self._http.stream(
                "POST",
                self._url(f"/models/{request.model.model}:streamGenerateContent"),
                headers=self._headers(streaming=True),
                params={"alt": "sse"},
                json=payload,
            ) as response:
                self._raise_for_status(response)
                async for document in _sse_documents(response):
                    if document.get("error") is not None:
                        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
                    usage = _usage_from(document)
                    if usage is not None:
                        latest_usage = usage
                    events, finish_reason = _events_from_document(document, pending_calls)
                    for event in events:
                        yield event
                    if finish_reason is not None:
                        for call in pending_calls.values():
                            yield ProviderEvent(kind="tool_call", tool_call=call)
                        pending_calls.clear()
                        if latest_usage is not None:
                            yield ProviderEvent(kind="usage", usage=latest_usage)
                        yield ProviderEvent(kind="finish", finish_reason=finish_reason)
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
            response = await self._http.request(
                method, self._url(path), headers=self._headers(streaming=False)
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, retryable=True) from exc
        self._raise_for_status(response)
        return response

    def _headers(self, *, streaming: bool) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream" if streaming else "application/json",
        }
        if self._oauth_token:
            headers["authorization"] = f"Bearer {self._oauth_token}"
        elif self._api_key:
            headers["x-goog-api-key"] = self._api_key
        else:  # pragma: no cover - constructor validates, retained for defensive safety.
            raise ProviderError(ProviderErrorKind.AUTH, retryable=False)
        return headers

    @staticmethod
    def _url(path: str) -> str:
        return f"{_GEMINI_API_BASE}{path}"

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        _raise_for_status(response)


def _model_id(record: object) -> str | None:
    if not isinstance(record, dict):
        return None
    raw_name = record.get("name")
    methods = record.get("supportedGenerationMethods")
    if not isinstance(raw_name, str) or not raw_name.startswith("models/"):
        return None
    model_id = raw_name.removeprefix("models/")
    if not model_id.startswith("gemini-") or not isinstance(methods, list):
        return None
    return model_id if "generateContent" in methods else None


def _payload_for(request: AdapterRequest, provider: str) -> dict[str, Any]:  # noqa: PLR0912
    if request.model.provider != provider:
        raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    tool_names = _tool_names(request.messages)
    for message in request.messages:
        text_parts = _text_parts(message.content)
        if message.role == "system":
            if message.tool_call_id is not None or message.tool_calls:
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            system_parts.extend({"text": text} for text in text_parts)
            continue
        if message.role == "tool":
            if message.tool_call_id is None or message.tool_calls:
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            name = tool_names.get(message.tool_call_id)
            if name is None:
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "id": message.tool_call_id,
                                "name": name,
                                "response": {"content": "".join(text_parts)},
                            }
                        }
                    ],
                }
            )
            continue
        if message.role not in {"user", "assistant"} or message.tool_call_id is not None:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        parts: list[dict[str, Any]] = [{"text": text} for text in text_parts]
        if message.tool_calls:
            if message.role != "assistant":
                raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
            parts.extend(_function_call_part(call) for call in message.tool_calls)
        if parts:
            contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})

    payload: dict[str, Any] = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    if request.tools:
        payload["tools"] = [{"functionDeclarations": [_tool_for(tool) for tool in request.tools]}]
    generation_config = _generation_config(request.parameters)
    if generation_config:
        payload["generationConfig"] = generation_config
    return payload


def _tool_names(messages: tuple[CanonicalMessage, ...]) -> dict[str, str]:
    names: dict[str, str] = {}
    for message in messages:
        if message.tool_calls and message.role != "assistant":
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        for call in message.tool_calls:
            names[call.id] = call.name
    return names


def _text_parts(parts: tuple[ContentPart, ...]) -> list[str]:
    values: list[str] = []
    for part in parts:
        if part.kind != "text" or not isinstance(part.value, str):
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        values.append(part.value)
    return values


def _function_call_part(call: CanonicalToolCall) -> dict[str, Any]:
    return {"functionCall": {"id": call.id, "name": call.name, "args": _arguments_object(call.arguments)}}


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
    normalized: dict[str, Any] = {"name": name}
    for field in ("description", "parameters"):
        value = source.get(field)
        if value is not None:
            normalized[field] = value
    return normalized


def _generation_config(parameters: Mapping[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if "temperature" in parameters:
        config["temperature"] = parameters["temperature"]
    if "max_tokens" in parameters:
        value = parameters["max_tokens"]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ProviderError(ProviderErrorKind.INCOMPATIBLE, retryable=False)
        config["maxOutputTokens"] = value
    return config


def _events_from_document(
    document: Mapping[str, Any], pending_calls: dict[str, CanonicalToolCall]
) -> tuple[tuple[ProviderEvent, ...], str | None]:
    candidates = document.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return (), None
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    events: list[ProviderEvent] = []
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if parts is not None and not isinstance(parts, list):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    for part in parts or []:
        if not isinstance(part, dict):
            raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
        text = part.get("text")
        if isinstance(text, str) and text:
            events.append(ProviderEvent(kind="text_delta", text=text))
        function_call = part.get("functionCall")
        if function_call is not None:
            call = _tool_call_from(function_call, len(pending_calls) + 1)
            pending_calls[call.id] = call
    reason = candidate.get("finishReason")
    return tuple(events), _finish_reason(reason)


def _tool_call_from(value: object, ordinal: int) -> CanonicalToolCall:
    if not isinstance(value, dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    name = value.get("name")
    if not isinstance(name, str) or not name:
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    call_id = value.get("id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"gemini-call-{ordinal}"
    arguments = value.get("args", {})
    if not isinstance(arguments, dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    return CanonicalToolCall(call_id, name, json.dumps(arguments, ensure_ascii=False, separators=(",", ":")))


def _finish_reason(value: object) -> str | None:
    return value.casefold() if isinstance(value, str) and value else None


def _usage_from(document: Mapping[str, Any]) -> TokenUsage | None:
    metadata = document.get("usageMetadata")
    if not isinstance(metadata, dict):
        return None
    input_tokens = _nonnegative_int(metadata.get("promptTokenCount"))
    output_tokens = _nonnegative_int(metadata.get("candidatesTokenCount"))
    cache_read_tokens = _nonnegative_int(metadata.get("cachedContentTokenCount"))
    if input_tokens is None and output_tokens is None and cache_read_tokens is None:
        return None
    return TokenUsage(input_tokens or 0, output_tokens or 0, cache_read_tokens or 0)


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


async def _sse_documents(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line:
            continue
        if document := _parse_sse(data_lines):
            yield document
        data_lines.clear()
    if document := _parse_sse(data_lines):
        yield document


def _parse_sse(data_lines: list[str]) -> dict[str, Any] | None:
    if not data_lines or data_lines == ["[DONE]"]:
        return None
    try:
        document = json.loads("\n".join(data_lines))
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False) from exc
    if not isinstance(document, dict):
        raise ProviderError(ProviderErrorKind.INTERNAL, retryable=False)
    return document


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
