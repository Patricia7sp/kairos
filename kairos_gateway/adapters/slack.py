"""Adapter Slack: envia mensagens pelo incoming webhook do Slack.

`_reversa_sdd/providers-gateway/design.md` — adapter de plataforma registrado
de fora do gateway.

Alvo: `slack:#canal`. O Slack permite sobrepor o canal anexando `?channel=` à
URL do webhook; o destino vira esse parâmetro, e sem destino vale o canal
configurado como `channel_default`.

A API entrante do Slack não expõe verificação sem envio: `verify()` valida só
a forma da URL, e a validação real é o envio de teste (que publica de fato).
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx

from kairos_gateway.service import SendResult


class SlackAdapter:
    name = "slack"

    def __init__(
        self,
        webhook_url: str,
        *,
        channel_default: str = "",
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._webhook_url = webhook_url.strip()
        self._channel_default = channel_default.strip()
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def verify(self) -> dict[str, Any]:
        url = self._webhook_url
        if url.startswith("https://hooks.slack.com/") or url.startswith("https://"):
            return {
                "ok": True,
                "message": "URL de webhook válida; a conexão é confirmada no envio de teste",
            }
        return {"ok": False, "message": "URL de webhook do Slack inválida"}

    def _endpoint(self, destino: str) -> str:
        canal = destino.strip() or self._channel_default
        if not canal:
            return self._webhook_url
        separador = "&" if "?" in self._webhook_url else "?"
        return f"{self._webhook_url}{separador}channel={urllib.parse.quote(canal)}"

    def send(self, target: str, payload: str) -> SendResult:
        destino = target.split(":", 1)[1] if ":" in target else ""
        try:
            resposta = self._client.post(self._endpoint(destino), json={"text": payload})
        except httpx.HTTPError:
            return SendResult(ok=False, retryable=True, error_kind="rede")
        if resposta.status_code in (200, 201, 202, 204):
            return SendResult(ok=True)
        if resposta.status_code == 429:
            return SendResult(ok=False, retryable=True, error_kind="rate_limit")
        if resposta.status_code >= 500:
            return SendResult(ok=False, retryable=True, error_kind="transitorio")
        return SendResult(ok=False, retryable=False, error_kind="plataforma")
