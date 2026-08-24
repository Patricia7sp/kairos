"""Adapter para Anthropic Claude usando a Messages API oficial."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from kairos_providers.base import (
    BaseLLMProvider,
    ConnectionStatus,
    ModelDescriptor,
    StreamChunk,
    TokenUsage,
)

logger = logging.getLogger(__name__)


class AnthropicAdapter(BaseLLMProvider):
    def __init__(
        self,
        name: str = "anthropic",
        base_url: str = "https://api.anthropic.com/v1",
        api_key: str | None = None,
        default_model: str = "claude-3-7-sonnet-20250219",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, base_url=base_url, api_key=api_key, **kwargs)
        self.default_model = default_model

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def list_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                "claude-3-7-sonnet-20250219",
                "Claude 3.7 Sonnet (Thinking)",
                "anthropic",
                200_000,
                64_000,
                True,
                True,
                True,
                3.00,
                15.00,
            ),
            ModelDescriptor(
                "claude-3-5-haiku-20241022",
                "Claude 3.5 Haiku",
                "anthropic",
                200_000,
                8_192,
                True,
                True,
                True,
                0.80,
                4.00,
            ),
            ModelDescriptor(
                "claude-3-opus-20240229",
                "Claude 3 Opus",
                "anthropic",
                200_000,
                4_096,
                True,
                True,
                True,
                15.00,
                75.00,
            ),
        ]

    async def test_connection(self) -> ConnectionStatus:
        if not self.api_key:
            return ConnectionStatus(False, self.name, "Chave de API Anthropic não configurada", 0)
        url = f"{self.base_url}/messages"
        payload = {
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=self._headers(), json=payload)
                if res.status_code == 200:
                    return ConnectionStatus(True, self.name, "Conexão com Anthropic validada", 3)
                if res.status_code == 401:
                    return ConnectionStatus(
                        False, self.name, "Chave de API Anthropic inválida (401)", 0
                    )
                return ConnectionStatus(
                    False, self.name, f"Resposta {res.status_code}: {res.text[:100]}", 0
                )
        except Exception as exc:  # noqa: BLE001
            return ConnectionStatus(False, self.name, f"Erro de conexão: {exc}", 0)

    async def stream_chat(  # noqa: PLR0912
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        selected_model = model or self.default_model
        system_prompt = ""
        user_messages: list[dict[str, Any]] = []

        for m in messages:
            if m.get("role") == "system":
                system_prompt += m.get("content", "") + "\n"
            else:
                user_messages.append(
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                )

        payload: dict[str, Any] = {
            "model": selected_model,
            "max_tokens": max_tokens or 8_192,
            "messages": user_messages,
            "stream": True,
        }
        if system_prompt.strip():
            payload["system"] = system_prompt.strip()
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {}),
                }
                for t in tools
                if "function" in t
            ]

        url = f"{self.base_url}/messages"
        client = httpx.AsyncClient(timeout=120.0)
        try:
            async with client.stream(
                "POST", url, headers=self._headers(), json=payload
            ) as response:
                if response.status_code >= 400:
                    err_body = await response.aread()
                    yield StreamChunk(
                        delta_text=f"\n[Erro {response.status_code} da Anthropic: {err_body.decode(errors='replace')}]",
                        finish_reason="error",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    try:
                        data = json.loads(data_str)
                    except Exception:  # noqa: BLE001, S112
                        continue

                    event_type = data.get("type")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield StreamChunk(delta_text=delta.get("text", ""))
                    elif event_type == "message_delta":
                        usage = data.get("usage")
                        if usage:
                            yield StreamChunk(
                                usage=TokenUsage(output_tokens=usage.get("output_tokens", 0)),
                                finish_reason=data.get("delta", {}).get("stop_reason"),
                            )
        finally:
            await client.aclose()
