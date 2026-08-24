"""Adapter para OpenAI, OpenRouter, DeepSeek, Groq e outros compatíveis com a API OpenAI."""

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


class OpenAICompatibleAdapter(BaseLLMProvider):
    def __init__(
        self,
        name: str = "openai",
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        default_model: str = "gpt-4o",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, base_url=base_url, api_key=api_key, **kwargs)
        self.default_model = default_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "https://kairos.agent"
            headers["X-Title"] = "Kairos"
        return headers

    def list_models(self) -> list[ModelDescriptor]:
        if self.name == "openai":
            return [
                ModelDescriptor(
                    "gpt-4o",
                    "GPT-4o (Omni)",
                    "openai",
                    128_000,
                    16_384,
                    True,
                    True,
                    True,
                    2.50,
                    10.00,
                ),
                ModelDescriptor(
                    "gpt-4o-mini",
                    "GPT-4o Mini",
                    "openai",
                    128_000,
                    16_384,
                    True,
                    True,
                    True,
                    0.15,
                    0.60,
                ),
                ModelDescriptor(
                    "o3-mini",
                    "o3-mini (Reasoning)",
                    "openai",
                    200_000,
                    100_000,
                    True,
                    False,
                    True,
                    1.10,
                    4.40,
                ),
                ModelDescriptor(
                    "o1",
                    "o1 (Deep Reasoning)",
                    "openai",
                    200_000,
                    100_000,
                    True,
                    True,
                    True,
                    15.00,
                    60.00,
                ),
            ]
        if self.name == "deepseek":
            return [
                ModelDescriptor(
                    "deepseek-chat",
                    "DeepSeek-V3",
                    "deepseek",
                    64_000,
                    8_192,
                    True,
                    False,
                    True,
                    0.14,
                    0.28,
                ),
                ModelDescriptor(
                    "deepseek-reasoner",
                    "DeepSeek-R1",
                    "deepseek",
                    64_000,
                    8_192,
                    True,
                    False,
                    True,
                    0.55,
                    2.19,
                ),
            ]
        if self.name == "groq":
            return [
                ModelDescriptor(
                    "llama-3.3-70b-versatile",
                    "Llama 3.3 70B",
                    "groq",
                    128_000,
                    8_192,
                    True,
                    False,
                    True,
                    0.59,
                    0.79,
                ),
            ]
        return [
            ModelDescriptor(
                self.default_model, self.default_model, self.name, 128_000, 8_192, True, True, True
            )
        ]

    async def test_connection(self) -> ConnectionStatus:
        if not self.api_key and self.name not in ("ollama", "lmstudio"):
            return ConnectionStatus(False, self.name, "API Key não configurada", 0)
        url = f"{self.base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url, headers=self._headers())
                if res.status_code == 200:
                    data = res.json()
                    count = len(data.get("data", []))
                    return ConnectionStatus(True, self.name, "Conexão bem-sucedida", count)
                return ConnectionStatus(
                    False, self.name, f"Erro HTTP {res.status_code}: {res.text[:100]}", 0
                )
        except Exception as exc:  # noqa: BLE001
            return ConnectionStatus(False, self.name, f"Falha de conexão: {exc}", 0)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        selected_model = model or self.default_model
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None and not selected_model.startswith(("o1", "o3")):
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        client = httpx.AsyncClient(timeout=120.0)
        try:
            async with client.stream(
                "POST", url, headers=self._headers(), json=payload
            ) as response:
                if response.status_code >= 400:
                    err_body = await response.aread()
                    yield StreamChunk(
                        delta_text=f"\n[Erro {response.status_code} da API {self.name}: {err_body.decode(errors='replace')}]",
                        finish_reason="error",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except Exception:  # noqa: BLE001, S112
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        if "usage" in data:
                            u = data["usage"]
                            yield StreamChunk(
                                usage=TokenUsage(
                                    input_tokens=u.get("prompt_tokens", 0),
                                    output_tokens=u.get("completion_tokens", 0),
                                )
                            )
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})
                    finish = choice.get("finish_reason")
                    text_delta = delta.get("content") or ""
                    tool_calls = delta.get("tool_calls") or []

                    yield StreamChunk(
                        delta_text=text_delta,
                        delta_tool_calls=tool_calls,
                        finish_reason=finish,
                        raw_event=data,
                    )
        finally:
            await client.aclose()
