"""Adapter WhatsApp: envia mensagens de texto pela API Cloud (Meta Graph).

`_reversa_sdd/providers-gateway/design.md` — adapter de plataforma registrado
de fora do gateway.

Alvo: `whatsapp:+55...` (número E.164). O destino sobrepõe o
`number_default`. O token de acesso e o `phone_number_id` são configuração
obrigatória; sem eles o adapter não é construído.
"""

from __future__ import annotations

from typing import Any

import httpx

from kairos_gateway.service import SendResult

_GRAPH_API = "https://graph.facebook.com/v21.0"


class WhatsAppAdapter:
    name = "whatsapp"

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        *,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token.strip()
        self._phone_number_id = phone_number_id.strip()
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def verify(self) -> dict[str, Any]:
        """Confirma token e número de envio sem despachar mensagem alguma."""
        if not self._phone_number_id:
            return {"ok": False, "message": "phone_number_id não configurado"}
        try:
            resposta = self._client.get(
                f"{_GRAPH_API}/{self._phone_number_id}",
                params={"fields": "verified_name,display_phone_number"},
                headers=self._headers(),
            )
        except httpx.HTTPError:
            return {"ok": False, "message": "API da Meta sem resposta"}
        if resposta.status_code == 200:
            dados = resposta.json()
            nome = dados.get("verified_name") or dados.get("display_phone_number") or "contato"
            return {"ok": True, "message": f"Conta de WhatsApp {nome} conectada"}
        try:
            dados = resposta.json()
            erro = dados.get("error") or {}
            return {"ok": False, "message": erro.get("message") or f"HTTP {resposta.status_code}"}
        except ValueError:
            return {"ok": False, "message": f"HTTP {resposta.status_code}"}

    def send(self, target: str, payload: str) -> SendResult:
        destino = target.split(":", 1)[1] if ":" in target else ""
        destino = destino.strip()
        if not destino:
            return SendResult(ok=False, retryable=False, error_kind="sem_destino")
        if not self._phone_number_id:
            return SendResult(ok=False, retryable=False, error_kind="sem_gateway")
        corpo = {
            "messaging_product": "whatsapp",
            "to": destino,
            "type": "text",
            "text": {"body": payload},
        }
        try:
            resposta = self._client.post(
                f"{_GRAPH_API}/{self._phone_number_id}/messages",
                json=corpo,
                headers=self._headers(),
            )
        except httpx.HTTPError:
            return SendResult(ok=False, retryable=True, error_kind="rede")
        if resposta.status_code == 200:
            return SendResult(ok=True)
        if resposta.status_code in (400, 401, 403, 404):
            return SendResult(ok=False, retryable=False, error_kind="plataforma")
        return SendResult(ok=False, retryable=True, error_kind="transitorio")
