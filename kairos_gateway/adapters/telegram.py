"""Adapter Telegram: envia mensagens pela Bot API.

`_reversa_sdd/providers-gateway/design.md` — adapters registrados de fora do
gateway entregam `plataforma:destino`.

Alvo: `telegram:chat_id`. O destino sobrepõe o `chat_id_default` configurado.
Falha 401 (token inválido/revogado) é permanente; 429 e 5xx são transitórias —
o gateway decide a escada de cooldown via `retry_after` da plataforma.
"""

from __future__ import annotations

from typing import Any

import httpx

from kairos_gateway.service import SendResult

_TELEGRAM_API = "https://api.telegram.org/bot{token}"


def _retry_after(resposta: httpx.Response, dados: dict[str, Any]) -> float | None:
    header = resposta.headers.get("Retry-After")
    if header and header.isdigit():
        return float(header)
    parameters = dados.get("parameters") or {}
    valor = parameters.get("retry_after")
    if isinstance(valor, (int, float)):
        return float(valor)
    return None


class TelegramAdapter:
    name = "telegram"

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 10.0,
        chat_id_default: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token.strip()
        self._timeout = timeout
        self._chat_id_default = chat_id_default
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def _url(self, method: str) -> str:
        return f"{_TELEGRAM_API.format(token=self._token)}/{method}"

    def verify(self) -> dict[str, Any]:
        """Confirma que o token conversa com a API (getMe). Não envia nada."""
        try:
            resposta = self._client.get(self._url("getMe"))
            dados = resposta.json()
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "message": "API do Telegram sem resposta"}
        if resposta.status_code == 200 and dados.get("ok"):
            user = dados.get("result") or {}
            nome = user.get("username") or user.get("first_name") or "desconhecido"
            return {"ok": True, "message": f"Bot @{nome} conectado"}
        detail = dados.get("description") or f"HTTP {resposta.status_code}"
        return {"ok": False, "message": detail}

    def send(self, target: str, payload: str) -> SendResult:
        destino = target.split(":", 1)[1] if ":" in target else ""
        chat_id = destino.strip() or self._chat_id_default.strip()
        if not chat_id:
            return SendResult(ok=False, retryable=False, error_kind="sem_destino")
        try:
            resposta = self._client.post(
                self._url("sendMessage"), json={"chat_id": chat_id, "text": payload}
            )
            dados = resposta.json()
        except (httpx.HTTPError, ValueError):
            return SendResult(ok=False, retryable=True, error_kind="rede")
        if resposta.status_code == 200 and dados.get("ok"):
            return SendResult(ok=True)
        if resposta.status_code == 429:
            return SendResult(ok=False, retryable=True, retry_after=_retry_after(resposta, dados))
        if resposta.status_code in (401, 403):
            return SendResult(ok=False, retryable=False, error_kind="nao_autorizado")
        if resposta.status_code == 400:
            return SendResult(ok=False, retryable=False, error_kind="destino_invalido")
        if resposta.status_code >= 500:
            return SendResult(ok=False, retryable=True, retry_after=_retry_after(resposta, dados))
        return SendResult(ok=False, retryable=False, error_kind="plataforma")
