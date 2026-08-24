"""Adapter para Ollama local (100% offline / self-hosted)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from kairos_providers.base import (
    BaseLLMProvider,
    ConnectionStatus,
    ModelDescriptor,
    StreamChunk,
)


class OllamaAdapter(BaseLLMProvider):
    def __init__(
        self,
        name: str = "ollama",
        base_url: str = "http://127.0.0.1:11434",
        default_model: str = "llama3.3",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, base_url=base_url, api_key=None, **kwargs)
        self.default_model = default_model

    def list_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                "llama3.3", "Llama 3.3 (Local)", "ollama", 128_000, 8_192, True, False, True
            ),
            ModelDescriptor(
                "deepseek-r1:14b",
                "DeepSeek R1 14B (Local)",
                "ollama",
                64_000,
                8_192,
                True,
                False,
                True,
            ),
            ModelDescriptor(
                "qwen2.5-coder:7b",
                "Qwen 2.5 Coder 7B (Local)",
                "ollama",
                32_000,
                8_192,
                True,
                False,
                True,
            ),
        ]

    async def test_connection(self) -> ConnectionStatus:
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    models = len(res.json().get("models", []))
                    return ConnectionStatus(
                        True,
                        self.name,
                        f"Ollama ativo localmente ({models} modelos encontrados)",
                        models,
                        auth_method="local",
                    )
                return ConnectionStatus(
                    False, self.name, f"Ollama retornou status {res.status_code}", 0
                )
        except Exception:  # noqa: BLE001
            return ConnectionStatus(
                False, self.name, "Ollama não está rodando localmente em http://127.0.0.1:11434", 0
            )

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
        if temperature is not None:
            payload.setdefault("options", {})["temperature"] = temperature

        url = f"{self.base_url}/api/chat"
        client = httpx.AsyncClient(timeout=120.0)
        try:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    err = await response.aread()
                    yield StreamChunk(
                        delta_text=f"[Erro Ollama {response.status_code}: {err.decode()}]",
                        finish_reason="error",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:  # noqa: BLE001, S112
                        continue
                    msg = data.get("message", {})
                    yield StreamChunk(
                        delta_text=msg.get("content", ""),
                        finish_reason="stop" if data.get("done") else None,
                    )
        finally:
            await client.aclose()
