"""Adapter Webhook: entrega para endpoints HTTP configurados (POST JSON).

`_reversa_sdd/providers-gateway/design.md` — adapter de plataforma registrado
de fora do gateway.

Alvo: `webhook:nome-do-endpoint` ou `webhook:https://urldireta`. O nome é
resolvido entre os endpoints configurados em `messaging.json`; uma URL completa
como destino é aceita diretamente (endpoint de teste/ocasional).
"""

from __future__ import annotations

import httpx

from kairos_gateway.service import SendResult


class WebhookAdapter:
    name = "webhook"

    def __init__(
        self,
        endpoints: dict[str, str],
        *,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._endpoints = dict(endpoints)
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def verify(self) -> dict[str, object]:
        if not self._endpoints:
            return {"ok": False, "message": "nenhum endpoint de webhook configurado"}
        nomes = ", ".join(sorted(self._endpoints))
        return {"ok": True, "message": f"endpoints configurados: {nomes}"}

    def _resolve(self, destino: str) -> str | None:
        if not destino:
            return next(iter(self._endpoints.values()), None)
        if destino in self._endpoints:
            return self._endpoints[destino]
        if destino.startswith("http://") or destino.startswith("https://"):
            return destino
        return None

    def send(self, target: str, payload: str) -> SendResult:
        destino = target.split(":", 1)[1] if ":" in target else ""
        url = self._resolve(destino.strip())
        if url is None:
            return SendResult(ok=False, retryable=False, error_kind="endpoint_inexistente")
        try:
            resposta = self._client.post(
                url, json={"text": payload, "source": "kairos"}, follow_redirects=True
            )
        except httpx.HTTPError:
            return SendResult(ok=False, retryable=True, error_kind="rede")
        if resposta.status_code in (200, 201, 202, 204):
            return SendResult(ok=True)
        if resposta.status_code in (429, 408) or resposta.status_code >= 500:
            return SendResult(ok=False, retryable=True, error_kind="transitorio")
        return SendResult(ok=False, retryable=False, error_kind="plataforma")
