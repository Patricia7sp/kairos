"""Canal de entrada WhatsApp: webhooks da Cloud API viram turnos do agente.

O canal de entrada fecha o laço que o gateway hoje não tem: ``kairos_gateway``
só entrega mensagens para fora (``WhatsAppAdapter``); este módulo recebe os
webhooks da Meta (GET de apertão de mão e POST de entrega), autentica o
remetente pela lista explícita de telefones autorizados e submete o texto ao
mesmo serviço de interação das interfaces web/terminal — que decide
ferramentas e emite aprovações.

Espelha o canal Telegram (`kairos_gateway/inbound.py`) — mesmo fail-closed,
mesmo envelope, mesma injeção de experiências — mas é **dirigido por webhook**,
não por polling: a Meta entrega por HTTPS e espera ``200 EVENT_RECEIVED`` em
segundos. O processamento do turno é assíncrono (_async delivery_, que o SDD
``providers-gateway/requirements.md`` marca para o WhatsApp).

Princípios (herdados do AGENTS.md):

- **Fail-closed.** Canal desabilitado ou lista de telefones vazia → ninguém fala
  com o agente. POST sem assinatura válida → rejeitado, nunca processado.
- **Verificação real.** A assinatura ``X-Hub-Signature-256`` é HMAC-SHA256 do
  corpo cru com o App Secret, comparada em tempo constante; sem segredo no
  cofre o webhook recusa (503), não "aceita apesar de".
- **Sem efeito fingido.** Sem adapter de envio (token/phone_number_id) não há
  resposta possível — o turno não roda (nada de efeitos sem canal de volta).
  Aprovação de ferramenta não tem botões no WhatsApp: a recusa é honesta e
  aponta para o painel, nunca um "aprovado" falso.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path
from typing import Any, Protocol

from kairos_gateway.adapters.config import load_config, platform_secret
from kairos_gateway.inbound import _chunk_text

__all__ = [
    "WhatsAppConfigError",
    "WhatsAppError",
    "WhatsAppInbound",
    "WhatsAppMessage",
    "WhatsAppSignatureError",
    "build_whatsapp_inbound",
]

logger = logging.getLogger(__name__)

_MAX_TEXT = 4000  # margem segura sobre o limite de 4096 da API
_MAX_SEEN = 256  # janela de dedupe em memória contra reentregas da Meta


class WhatsAppError(Exception):
    """Erro de configuração ou autenticação no canal de entrada."""


class WhatsAppConfigError(WhatsAppError):
    """Canal desabilitado ou segredo/adapte ausente — falha fechado em 5xx."""


class WhatsAppSignatureError(WhatsAppError):
    """Webhook com assinatura ou apertão de mão inválidos — rejeita."""


class InteractionRouterProtocol(Protocol):
    """Duck-typing mínima do ``InteractionRouter`` usado neste módulo."""

    async def stream(self, envelope: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Verificação (assinatura e apertão de mão)


def verify_webhook_signature(
    app_secret: str, raw_body: bytes, signature_header: str | None
) -> bool:
    """Valida ``X-Hub-Signature-256`` contra o corpo cru, em tempo constante."""
    if not raw_body or not signature_header or not signature_header.startswith("sha256="):
        return False
    esperado = (
        "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    )
    return compare_digest(esperado, signature_header)


def verify_challenge(
    *, shared_token: str, mode: str | None, verify_token: str | None, challenge: str | None
) -> str:
    """Confere o apertão de mão do subscribe e devolve o desafio para a Meta.

    Qualquer divergência é recusa (fail-closed); o desafio só sai com
    ``hub.mode=subscribe`` e o Verify Token igual ao do cofre.
    """
    if mode != "subscribe" or not verify_token or not challenge or not shared_token:
        raise WhatsAppSignatureError("apertão de mão incompleto")
    if not compare_digest(shared_token, verify_token):
        raise WhatsAppSignatureError("verify_token divergente")
    return challenge


# ---------------------------------------------------------------------------
# Payload


@dataclass(frozen=True)
class WhatsAppMessage:
    """Uma mensagem de texto entregue pela Cloud API."""

    message_id: str  # wamid — chave da dedupe/idempotência
    wa_id: str  # telefone E.164 do remetente, como veio na entrega
    phone: str  # remetente normalizado em dígitos (chave da allowlist)
    text: str


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def parse_messages(payload: Any) -> list[WhatsAppMessage]:
    """Extrai mensagens de texto de ``entry[].changes[].value.messages[]``.

    Mudanças que não são ``field=="messages"`` (ex.: ``statuses``) são
    ignoradas; tipos não-texto também — o canal responde a texto, no mesmo
    recorte do Telegram.
    """
    if not isinstance(payload, dict):
        return []
    mensagens: list[WhatsAppMessage] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for raw in value.get("messages") or []:
                if not isinstance(raw, dict) or raw.get("type") != "text":
                    continue
                corpo = raw.get("text")
                texto = corpo.get("body") if isinstance(corpo, dict) else None
                message_id = raw.get("id")
                remetente = raw.get("from")
                if not (
                    isinstance(texto, str)
                    and texto.strip()
                    and isinstance(message_id, str)
                    and message_id
                    and isinstance(remetente, str)
                    and remetente
                ):
                    continue
                telefone = _digits(remetente)
                if not telefone:
                    continue
                mensagens.append(WhatsAppMessage(message_id, remetente, telefone, texto))
    return mensagens


# ---------------------------------------------------------------------------
# WhatsAppInbound — verificação + despacho assíncrono


class WhatsAppInbound:
    """Canal de entrada dirigido por webhook: valida e despacha turnos."""

    def __init__(
        self,
        home: Path | str,
        router: Any,
        *,
        app_secret: str | None = None,
        verify_token: str | None = None,
        adapter: Any = None,
        message_limit: int = 10,
    ) -> None:
        self.home = Path(home)
        doc = load_config(self.home)
        inbound = doc["whatsapp"].get("inbound", {})
        self._enabled: bool = bool(inbound.get("enabled", False))
        self._allowed: frozenset[str] = frozenset(
            _digits(p) for p in inbound.get("allowed_phone_numbers", [])
        )
        self._experiences: bool = bool(inbound.get("experiences", True))
        self._router = router
        self._app_secret = (
            app_secret
            if app_secret is not None
            else platform_secret(self.home, "whatsapp", field="app_secret")
        )
        self._verify_token = (
            verify_token
            if verify_token is not None
            else platform_secret(self.home, "whatsapp", field="verify_token")
        )
        self._adapter = adapter
        self._message_limit = message_limit
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def inbound_enabled(self) -> bool:
        return self._enabled

    @property
    def allowed_phone_numbers(self) -> frozenset[str]:
        return self._allowed

    @property
    def experiences_enabled(self) -> bool:
        return self._experiences

    # --- verificação exposta ao servidor web ---

    def verify_handshake(
        self, *, mode: str | None, verify_token: str | None, challenge: str | None
    ) -> str:
        """Apertão de mão do subscribe; devolve o desafio ou recusa."""
        if not self._enabled:
            raise WhatsAppConfigError(
                "canal de entrada desabilitado (whatsapp.inbound.enabled = false)"
            )
        if not self._verify_token:
            raise WhatsAppConfigError("verify_token do webhook não configurado no cofre")
        return verify_challenge(
            shared_token=self._verify_token,
            mode=mode,
            verify_token=verify_token,
            challenge=challenge,
        )

    def has_valid_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        if not self._app_secret:
            return False
        return verify_webhook_signature(self._app_secret, raw_body, signature_header)

    # --- entrega ---

    def accept(self, raw_body: bytes, signature_header: str | None) -> int:
        """Valida a entrega, deduplica e agenda os turnos; devolve quantos aceitou.

        A Meta exige ``200 EVENT_RECEIVED`` rápido; o turno roda em task
        assíncrona. Sem adapter de envio o turno não roda (nada de efeito
        fingido), mas a entrega é ackada e o aviso fica no log.
        """
        if not self._enabled:
            raise WhatsAppConfigError(
                "canal de entrada desabilitado (whatsapp.inbound.enabled = false)"
            )
        if not self._app_secret:
            raise WhatsAppConfigError("app_secret do webhook não configurado no cofre")
        if not self.has_valid_signature(raw_body, signature_header):
            raise WhatsAppSignatureError("assinatura X-Hub-Signature-256 inválida")
        try:
            payload = json.loads(raw_body or b"")
        except (ValueError, TypeError) as exc:
            raise WhatsAppSignatureError("payload do webhook inválido") from exc
        aceitos = 0
        for mensagem in parse_messages(payload):
            if mensagem.phone not in self._allowed:
                continue  # fail-closed: remetente fora da lista não fala
            if self._is_seen(mensagem.message_id):
                continue
            if not self._adapter:
                logger.warning(
                    "whatsapp inbound: entrega de %s sem adapter de envio (não responderá): %s",
                    mensagem.phone,
                    mensagem.message_id,
                )
                continue
            task = asyncio.get_running_loop().create_task(self._dispatch(mensagem))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            aceitos += 1
        return aceitos

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

    async def _dispatch(self, mensagem: WhatsAppMessage) -> None:
        conversation_id = f"whatsapp:{mensagem.phone}"
        lock = self._chat_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            try:
                envelope = self._build_envelope(conversation_id, mensagem)
                text, error = await self._stream_turn(envelope, mensagem.phone)
            except BaseException as exc:  # noqa: BLE001
                logger.warning("whatsapp inbound: turno falhou: %s", exc)
                final = "Falha ao processar sua mensagem — abra o painel Kairos."
            else:
                final = text or error or "Sem resposta."
            await self._send_chunked(mensagem.phone, final)

    def _build_envelope(self, conversation_id: str, mensagem: WhatsAppMessage) -> Any:
        from kairos_integration.interaction_contract import InteractionEnvelope

        content = self._augment(mensagem.text)
        return InteractionEnvelope(
            conversation_id=conversation_id,
            source="whatsapp",
            content=content,
            idempotency_key=f"whatsapp:{mensagem.message_id}",
            web_search=True,
            tools=True,
        )

    def _augment(self, text: str) -> str:
        """Prefixa o turno com experiências ativas (ligado por padrão).

        Desligue com ``whatsapp.inbound.experiences: false``. A injeção é local
        ao turno que está chegando — nunca altera o system prompt de uma
        conversa já em andamento (Lei 1 de `kairos_integration.surfaces`).
        """
        if not self._experiences or not text.strip():
            return text
        from kairos_memory import build_experience_context

        context = build_experience_context(text, self.home)
        return f"{context}\n\n{text}" if context else text

    @staticmethod
    def _event_kind(event: Any) -> str | None:
        kind = getattr(event, "kind", None)
        if isinstance(kind, str):
            return kind
        value = getattr(kind, "value", None)
        return value if isinstance(value, str) else None

    async def _stream_turn(self, envelope: Any, phone: str) -> tuple[str | None, str | None]:
        accumulated: list[str] = []
        try:
            async for event in self._router.stream(envelope):
                kind = self._event_kind(event)
                if kind == "delta":
                    accumulated.append(getattr(event, "text", "") or "")
                elif kind == "turn_end":
                    break
                elif kind == "tool_approval_request":
                    await self._notice_approval(phone, event)
                elif kind == "turn_error":
                    return None, getattr(event, "error", "falha desconhecida")
        except BaseException as exc:  # noqa: BLE001
            return None, f"falha ao executar: {exc}"
        text = "".join(accumulated).strip()
        return (text or None), None

    async def _notice_approval(self, phone: str, event: Any) -> None:
        """Aprovação de ferramenta não tem botões no WhatsApp — recusa honesta."""
        tool_call = getattr(event, "tool_call", None)
        tool_name = getattr(tool_call, "name", "?") if tool_call is not None else "?"
        if self._adapter is None:
            return
        texto = (
            "Aprovacao necessaria para a ferramenta "
            f"'{tool_name}'.\nO WhatsApp nao tem botoes de aprovacao — "
            "abra o painel Kairos para decidir."
        )
        with suppress(BaseException):
            await self._send_chunked(phone, texto)

    # --- envio final ---

    async def _send_chunked(self, phone: str, text: str) -> None:
        if self._adapter is None:
            return
        for i, chunk in enumerate(_chunk_text(text)):
            if i >= 10:
                await _protect_send(
                    self._adapter,
                    f"whatsapp:{phone}",
                    "Saida longa — consulte o painel para o resultado completo.",
                )
                break
            await _protect_send(self._adapter, f"whatsapp:{phone}", chunk)


async def _protect_send(adapter: Any, target: str, text: str) -> None:
    """Envia com suporte a falhas; adapter.sender é síncrono no gateway."""
    try:
        await asyncio.to_thread(adapter.send, target, text)
    except BaseException as exc:  # noqa: BLE001
        logger.warning("whatsapp inbound: envio para %s falhou: %s", target, exc)


# ---------------------------------------------------------------------------
# Composição


def build_whatsapp_inbound(
    home: Path | str,
    *,
    router: Any = None,
    adapter: Any = None,
    app_secret: str | None = None,
    verify_token: str | None = None,
) -> WhatsAppInbound:
    """Monta o canal de entrada com o ``InteractionRouter`` e destino padrão."""
    if router is None:
        from kairos_integration.composition import build_interaction_router

        router = build_interaction_router(Path(home))
    if adapter is None:
        from kairos_gateway.adapters import build_platform_adapters

        adapter = build_platform_adapters(Path(home)).get("whatsapp")
    return WhatsAppInbound(
        Path(home),
        router,
        app_secret=app_secret,
        verify_token=verify_token,
        adapter=adapter,
    )
