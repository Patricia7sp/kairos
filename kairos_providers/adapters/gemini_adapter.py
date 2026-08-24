"""Adapter para Google Gemini via Generative Language API e Google Cloud ADC."""

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
)

logger = logging.getLogger(__name__)


class GoogleGeminiAdapter(BaseLLMProvider):
    def __init__(
        self,
        name: str = "gemini",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        api_key: str | None = None,
        oauth_token: str | None = None,
        default_model: str = "gemini-2.0-flash",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, base_url=base_url, api_key=api_key, **kwargs)
        self.oauth_token = oauth_token
        self.default_model = default_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.oauth_token:
            headers["Authorization"] = f"Bearer {self.oauth_token}"
        elif self.api_key:
            headers["x-goog-api-key"] = self.api_key
        return headers

    def list_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                "gemini-2.0-flash",
                "Gemini 2.0 Flash (Next-Gen)",
                "gemini",
                1_048_576,
                8_192,
                True,
                True,
                True,
                0.10,
                0.40,
            ),
            ModelDescriptor(
                "gemini-1.5-pro",
                "Gemini 1.5 Pro (2M Context)",
                "gemini",
                2_097_152,
                8_192,
                True,
                True,
                True,
                1.25,
                5.00,
            ),
            ModelDescriptor(
                "gemini-1.5-flash",
                "Gemini 1.5 Flash",
                "gemini",
                1_048_576,
                8_192,
                True,
                True,
                True,
                0.075,
                0.30,
            ),
        ]

    async def test_connection(self) -> ConnectionStatus:
        if not self.api_key and not self.oauth_token:
            return ConnectionStatus(
                False, self.name, "Chave de API ou Token Google não configurados", 0
            )
        url = f"{self.base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url, headers=self._headers())
                if res.status_code == 200:
                    data = res.json()
                    models = len(data.get("models", []))
                    method = "Google Cloud OAuth (ADC)" if self.oauth_token else "API Key"
                    return ConnectionStatus(
                        True,
                        self.name,
                        "Conexão com Google Gemini validada",
                        models,
                        auth_method=method,
                    )
                return ConnectionStatus(
                    False, self.name, f"Resposta {res.status_code}: {res.text[:100]}", 0
                )
        except Exception as exc:  # noqa: BLE001
            return ConnectionStatus(False, self.name, f"Erro de conexão: {exc}", 0)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        selected_model = model or self.default_model
        contents: list[dict[str, Any]] = []
        for m in messages:
            role = "model" if m.get("role") in ("assistant", "model") else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})

        payload: dict[str, Any] = {
            "contents": contents,
        }
        if temperature is not None:
            payload.setdefault("generationConfig", {})["temperature"] = temperature
        if max_tokens is not None:
            payload.setdefault("generationConfig", {})["maxOutputTokens"] = max_tokens

        url = f"{self.base_url}/models/{selected_model}:streamGenerateContent?alt=sse"
        client = httpx.AsyncClient(timeout=120.0)
        try:
            async with client.stream(
                "POST", url, headers=self._headers(), json=payload
            ) as response:
                if response.status_code >= 400:
                    err_body = await response.aread()
                    yield StreamChunk(
                        delta_text=f"\n[Erro {response.status_code} da API Google Gemini: {err_body.decode(errors='replace')}]",
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

                    candidates = data.get("candidates", [])
                    if candidates:
                        cand = candidates[0]
                        parts = cand.get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts)
                        finish = cand.get("finishReason")
                        yield StreamChunk(delta_text=text, finish_reason=finish)
        finally:
            await client.aclose()
