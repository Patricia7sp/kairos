"""Canal de entrada Webhook genérico: POST autenticado vira turno do agente.

Fecha a paridade do canal webhook no laço que o gateway entrega (o adapter
outbound já posta em endpoints configurados): este módulo recebe um JSON do
cliente com a mensagem de texto, exige o token de ingestão (no cofre) e a
fonte na lista autorizada, e submete o texto ao mesmo serviço de interação das
interfaces web, terminal e dos demais canais. A resposta volta pelo adapter
outbound — para o ``reply_url`` da entrega ou o endpoint padrão configurado.

É o canal de integração "faça você mesmo": qualquer script/serviço consegue
falar com o agente apresentando o token. Por isso a segurança é dobrada e
fail-closed: sem token no cofre nada entra (503); token divergente é rejeitado
(401); fonte fora da lista autorizada é ignorada; e sem adapter de envio o turno
não roda — nada de efeito fingido.

O corpo aceito:

.. code-block:: json

    {
      "text": "mensagem para o agente",
      "source": "sensor-x",
      "reply_url": "https://meu-servico/resposta",
      "id": "opcional-para-idempotencia"
    }

``source`` também pode vir no header ``X-Kairos-Webhook-Source``. O token vai no
header ``X-Kairos-Webhook-Token`` (nunca no corpo, para não virar log/estado).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path
from typing import Any

from kairos_gateway.adapters.config import load_config, platform_secret
from kairos_gateway.inbound import _chunk_text

__all__ = [
    "WebhookConfigError",
    "WebhookInbound",
    "WebhookUnauthorizedError",
    "build_webhook_inbound",
    "parse_webhook_message",
]

logger = logging.getLogger(__name__)

_MAX_SEEN = 256  # janela de dedupe em memória contra reentregas


class WebhookConfigError(Exception):
    """Canal desabilitado ou token/adapte ausente — falha fechado em 5xx."""


class WebhookUnauthorizedError(Exception):
    """Token de ingestão ausente ou divergente — rejeita."""


@dataclass(frozen=True)
class WebhookMessage:
    """Uma mensagem entregue ao canal de entrada webhook."""

    message_id: str | None  # id explícito do corpo — chave da dedupe, se houver
    source: str  # nome da fonte autorizada
    text: str
    reply_url: str  # "" quando não informado — vale o endpoint padrão


def parse_webhook_message(payload: Any) -> WebhookMessage | None:
    """Extrai a mensagem do corpo (``text`` obrigatório; ``source``/``id`` opcionais)."""
    if not isinstance(payload, dict):
        return None
    texto = payload.get("text")
    source = payload.get("source")
    if not isinstance(texto, str) or not texto.strip():
        return None
    if source is not None and not isinstance(source, str):
        return None
    message_id = payload.get("id")
    if message_id is not None and not isinstance(message_id, str):
        return None
    reply_url = payload.get("reply_url")
    if reply_url is not None and not isinstance(reply_url, str):
        return None
    return WebhookMessage(
        message_id=message_id,
        source=source or "",
        text=texto,
        reply_url=(reply_url or "").strip(),
    )


class WebhookInbound:
    """Canal de entrada dirigido por webhook: token + fonte autorizada."""

    def __init__(
        self,
        home: Path | str,
        router: Any,
        *,
        ingest_token: str | None = None,
        adapter: Any = None,
    ) -> None:
        self.home = Path(home)
        doc = load_config(self.home)
        inbound = doc["webhook"].get("inbound", {})
        self._enabled: bool = bool(inbound.get("enabled", False))
        self._allowed: frozenset[str] = frozenset(inbound.get("allowed_sources", []))
        self._experiences: bool = bool(inbound.get("experiences", True))
        self._router = router
        self._ingest_token = (
            ingest_token
            if ingest_token is not None
            else platform_secret(self.home, "webhook", field="ingest_token")
        )
        self._adapter = adapter
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def inbound_enabled(self) -> bool:
        return self._enabled

    @property
    def allowed_sources(self) -> frozenset[str]:
        return self._allowed

    @property
    def experiences_enabled(self) -> bool:
        return self._experiences

    def has_valid_token(self, token_header: str | None) -> bool:
        if not self._ingest_token or not token_header:
            return False
        return compare_digest(self._ingest_token, token_header)

    def accept(
        self,
        raw_body: bytes,
        *,
        token_header: str | None,
        source_header: str | None,
    ) -> int:
        """Valida o token, deduplica e agenda os turnos; devolve quantos aceitou."""
        if not self._enabled:
            raise WebhookConfigError(
                "canal de entrada desabilitado (webhook.inbound.enabled = false)"
            )
        if not self._ingest_token:
            raise WebhookConfigError("token de ingestão do webhook não configurado no cofre")
        if not self.has_valid_token(token_header):
            raise WebhookUnauthorizedError("token de ingestão ausente ou divergente")
        try:
            payload = json.loads(raw_body or b"")
        except (ValueError, TypeError) as exc:
            raise WebhookUnauthorizedError("corpo do webhook inválido") from exc
        mensagem = parse_webhook_message(payload)
        if mensagem is None:
            return 0
        fonte = mensagem.source or (source_header or "").strip()
        fonte = fonte.strip()
        if not fonte or fonte not in self._allowed:
            return 0  # fail-closed: fonte fora da lista não fala
        mensagem = WebhookMessage(
            message_id=mensagem.message_id,
            source=fonte,
            text=mensagem.text,
            reply_url=mensagem.reply_url,
        )
        if mensagem.message_id and self._is_seen(mensagem.message_id):
            return 0
        if not self._adapter:
            logger.warning(
                "webhook inbound: entrega de %s sem adapter de envio (não responderá)", fonte
            )
            return 0
        task = asyncio.get_running_loop().create_task(self._dispatch(mensagem))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return 1

    def _is_seen(self, message_id: str) -> bool:
        if message_id in self._seen:
            return True
        self._seen.add(message_id)
        self._seen_order.append(message_id)
        if len(self._seen_order) > _MAX_SEEN:
            velho = self._seen_order.popleft()
            self._seen.discard(velho)
        return False

    # --- turno ---

    async def _dispatch(self, mensagem: WebhookMessage) -> None:
        conversation_id = f"webhook:{mensagem.source}"
        lock = self._chat_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            try:
                envelope = self._build_envelope(conversation_id, mensagem)
                text, error = await _stream_turn(self, envelope, mensagem.reply_url)
            except BaseException as exc:  # noqa: BLE001
                logger.warning("webhook inbound: turno falhou: %s", exc)
                final = "Falha ao processar sua mensagem — abra o painel Kairos."
            else:
                final = text or error or "Sem resposta."
            await self._send_chunked(mensagem.reply_url, final)

    def _build_envelope(self, conversation_id: str, mensagem: WebhookMessage) -> Any:
        from kairos_integration.interaction_contract import InteractionEnvelope

        content = self._augment(mensagem.text)
        idempotency = (
            f"webhook:{mensagem.message_id}"
            if mensagem.message_id
            else f"webhook:{mensagem.source}:{len(mensagem.text)}"
        )
        return InteractionEnvelope(
            conversation_id=conversation_id,
            source="webhook",
            content=content,
            idempotency_key=idempotency,
            web_search=True,
            tools=True,
        )

    def _augment(self, text: str) -> str:
        """Prefixa o turno com experiências ativas (ligado por padrão)."""
        if not self._experiences or not text.strip():
            return text
        from kairos_memory import build_experience_context

        context = build_experience_context(text, self.home)
        return f"{context}\n\n{text}" if context else text

    async def _notice_approval(self, reply_url: str, event: Any) -> None:
        """Webhook não tem botões de aprovação — recusa honesta."""
        tool_call = getattr(event, "tool_call", None)
        tool_name = getattr(tool_call, "name", "?") if tool_call is not None else "?"
        if self._adapter is None:
            return
        texto = (
            "Aprovacao necessaria para a ferramenta "
            f"'{tool_name}'.\nO webhook nao tem botoes de aprovacao — "
            "abra o painel Kairos para decidir."
        )
        with suppress(BaseException):
            await self._send_chunked(reply_url, texto)

    # --- envio final ---

    async def _send_chunked(self, reply_url: str, text: str) -> None:
        if self._adapter is None:
            return
        alvo = f"webhook:{reply_url}" if reply_url else "webhook:"
        for i, chunk in enumerate(_chunk_text(text)):
            if i >= 10:
                await _protect_send(
                    self._adapter,
                    alvo,
                    "Saida longa — consulte o painel para o resultado completo.",
                )
                break
            await _protect_send(self._adapter, alvo, chunk)


async def _protect_send(adapter: Any, target: str, text: str) -> None:
    """Envia com suporte a falhas; adapter.sender é síncrono no gateway."""
    try:
        await asyncio.to_thread(adapter.send, target, text)
    except BaseException as exc:  # noqa: BLE001
        logger.warning("webhook inbound: envio para %s falhou: %s", target, exc)


def _event_kind(event: Any) -> str | None:
    kind = getattr(event, "kind", None)
    if isinstance(kind, str):
        return kind
    value = getattr(kind, "value", None)
    return value if isinstance(value, str) else None


async def _stream_turn(
    inbound: WebhookInbound, envelope: Any, destinatario: str
) -> tuple[str | None, str | None]:
    """Roda o turno pelo router e recolhe o texto; o destinatário é o reply_url."""
    accumulated: list[str] = []
    try:
        async for event in inbound._router.stream(envelope):
            kind = _event_kind(event)
            if kind == "delta":
                accumulated.append(getattr(event, "text", "") or "")
            elif kind == "turn_end":
                break
            elif kind == "tool_approval_request":
                await inbound._notice_approval(destinatario, event)
            elif kind == "turn_error":
                return None, getattr(event, "error", "falha desconhecida")
    except BaseException as exc:  # noqa: BLE001
        return None, f"falha ao executar: {exc}"
    text = "".join(accumulated).strip()
    return (text or None), None


# ---------------------------------------------------------------------------
# Composição


def build_webhook_inbound(
    home: Path | str,
    *,
    router: Any = None,
    adapter: Any = None,
    ingest_token: str | None = None,
) -> WebhookInbound:
    """Monta o canal de entrada com o ``InteractionRouter`` e destino padrão."""
    if router is None:
        from kairos_integration.composition import build_interaction_router

        router = build_interaction_router(Path(home))
    if adapter is None:
        from kairos_gateway.adapters import build_platform_adapters

        adapter = build_platform_adapters(Path(home)).get("webhook")
    return WebhookInbound(
        Path(home),
        router,
        ingest_token=ingest_token,
        adapter=adapter,
    )
