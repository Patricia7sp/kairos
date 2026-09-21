"""Canal de entrada Slack: Events API (webhook) vira turno do agente.

Fecha a paridade do canal Slack no laço que o gateway entrega (adapter outbound
já existe): este módulo recebe as entregas do Slack Events API, verifica o
apertão de mão ``url_verification`` e a assinatura ``X-Slack-Signature``
(HMAC-SHA256 ``v0`` do corpo cru com o Signing Secret, anti-replay pelo
``X-Slack-Request-Timestamp``), autoriza o remetente pela lista explícita de
user IDs e submete o texto ao mesmo serviço de interação das interfaces web,
terminal e dos demais canais.

Espelha o padrão do WhatsApp (`kairos_gateway/whatsapp_inbound.py`) — mesmo
fail-closed, mesmo envelope, mesma injeção de experiências — com a mecânica
própria do Slack: entrega por POST assinado (o Slack assina **todas** as
entregas, inclusive o desafio de verificação de URL) e turno assíncrono (_async
delivery_). A resposta volta pelo incoming webhook do adapter, no canal da
conversa (`slack:{channel}`).

Princípios (herdados do AGENTS.md):

- **Fail-closed.** Canal desabilitado, lista de user IDs vazia ou Signing Secret
  ausente → ninguém fala com o agente. POST sem assinatura válida (ou com
  timestamp velho — replay) é rejeitado, nunca processado.
- **Verificação real.** ``v0:<timestamp>:<corpo cru>`` HMAC-SHA256 comparado em
  tempo constante; corpo fora dessa base divergente é recusa, não ignorância.
- **Sem efeito fingido.** Sem adapter de envio (webhook URL entrante no cofre)
  não há resposta possível — o turno não roda (nada de efeitos sem canal de
  volta). Ferramenta que exige aprovação não tem botões no Slack: a recusa é
  honesta e aponta para o painel, nunca um "aprovado" falso.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path
from typing import Any

from kairos_gateway.adapters.config import load_config, platform_secret
from kairos_gateway.inbound import _chunk_text

__all__ = [
    "SlackConfigError",
    "SlackError",
    "SlackInbound",
    "SlackMessage",
    "SlackSignatureError",
    "build_slack_inbound",
    "parse_slack_events",
    "verify_slack_signature",
]

logger = logging.getLogger(__name__)

_MAX_TEXT = 4000  # margem confortável; o Slack não impõe limite pequeno
_MAX_SEEN = 256  # janela de dedupe em memória contra reentregas do Slack
_MAX_REPLAY_SKEW = 300  # segundos tolerados entre o relógio do Slack e o nosso


class SlackError(Exception):
    """Erro de configuração ou autenticação no canal de entrada."""


class SlackConfigError(SlackError):
    """Canal desabilitado ou segredo/adapte ausente — falha fechado em 5xx."""


class SlackSignatureError(SlackError):
    """Entrega com assinatura ou desafio inválidos — rejeita."""


# ---------------------------------------------------------------------------
# Verificação (assinatura v0 e anti-replay)


def verify_slack_signature(
    signing_secret: str,
    raw_body: bytes,
    *,
    timestamp_header: str | None,
    signature_header: str | None,
    now: int | None = None,
) -> bool:
    """Valida ``X-Slack-Signature`` (``v0=…``) e o janela de replay.

    A base assinada é ``v0:<timestamp>:<corpo cru>``; o relógio do Slack pode
    divergir do nosso por no máximo ``_MAX_REPLAY_SKEW`` segundos.
    """
    if (
        not raw_body
        or not timestamp_header
        or not signature_header
        or not signature_header.startswith("v0=")
    ):
        return False
    try:
        timestamp = int(timestamp_header)
    except (ValueError, TypeError):
        return False
    atual = int(time.time()) if now is None else now
    if abs(atual - timestamp) > _MAX_REPLAY_SKEW:
        return False
    base = f"v0:{timestamp_header}:{raw_body.decode('utf-8', errors='replace')}"
    esperado = (
        "v0="
        + hmac.new(signing_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    )
    return compare_digest(esperado, signature_header)


# ---------------------------------------------------------------------------
# Payload


@dataclass(frozen=True)
class SlackMessage:
    """Uma mensagem de texto de um usuário entregue pelo Events API."""

    message_id: str  # event_id do envelope — chave da dedupe/idempotência
    user: str  # user ID do Slack (U…)
    channel: str  # canal da conversa (D… privado ou C… público) — destino do reply
    text: str


def parse_slack_events(payload: Any) -> list[SlackMessage]:
    """Extrai mensagens de texto de ``event_callback`` do tipo ``message``.

    Mensagens de bot ou com ``subtype`` (``message_changed`` etc.) são
    ignoradas — o canal responde a texto de usuário humano, no mesmo recorte
    dos demais canais.
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("type") != "event_callback":
        return []
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "message":
        return []
    if "subtype" in event:
        return []
    event_id = payload.get("event_id")
    user = event.get("user")
    channel = event.get("channel")
    texto = event.get("text")
    if not (
        isinstance(event_id, str)
        and event_id
        and isinstance(user, str)
        and user
        and isinstance(channel, str)
        and channel
        and isinstance(texto, str)
        and texto.strip()
    ):
        return []
    return [SlackMessage(event_id, user, channel, texto)]


# ---------------------------------------------------------------------------
# SlackInbound — verificação + despacho assíncrono


class SlackInbound:
    """Canal de entrada dirigido por webhook: valida e despacha turnos."""

    def __init__(
        self,
        home: Path | str,
        router: Any,
        *,
        signing_secret: str | None = None,
        adapter: Any = None,
    ) -> None:
        self.home = Path(home)
        doc = load_config(self.home)
        inbound = doc["slack"].get("inbound", {})
        self._enabled: bool = bool(inbound.get("enabled", False))
        self._allowed: frozenset[str] = frozenset(inbound.get("allowed_user_ids", []))
        self._experiences: bool = bool(inbound.get("experiences", True))
        self._router = router
        self._signing_secret = (
            signing_secret
            if signing_secret is not None
            else platform_secret(self.home, "slack", field="signing_secret")
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
    def allowed_user_ids(self) -> frozenset[str]:
        return self._allowed

    @property
    def experiences_enabled(self) -> bool:
        return self._experiences

    def has_valid_signature(
        self,
        raw_body: bytes,
        *,
        timestamp_header: str | None,
        signature_header: str | None,
    ) -> bool:
        if not self._signing_secret:
            return False
        return verify_slack_signature(
            self._signing_secret,
            raw_body,
            timestamp_header=timestamp_header,
            signature_header=signature_header,
        )

    def accept(
        self,
        raw_body: bytes,
        *,
        timestamp_header: str | None,
        signature_header: str | None,
    ) -> str | None:
        """Valida a entrega e devolve o desafio da URL ou processa os eventos.

        ``url_verification`` devolve a string de ``challenge`` (o Slack exige o
        corpo de volta em segundos); ``event_callback`` agenda os turnos e
        devolve ``None`` (o acuse é o ``200`` rápido). Sem adapter de envio o
        turno não roda (nada de efeito fingido), mas a entrega é ackada e o
        aviso fica no log.
        """
        if not self._enabled:
            raise SlackConfigError("canal de entrada desabilitado (slack.inbound.enabled = false)")
        if not self._signing_secret:
            raise SlackConfigError("signing_secret do Slack não configurado no cofre")
        if not self.has_valid_signature(
            raw_body, timestamp_header=timestamp_header, signature_header=signature_header
        ):
            raise SlackSignatureError("assinatura X-Slack-Signature inválida ou fora da janela")
        try:
            payload = json.loads(raw_body or b"")
        except (ValueError, TypeError) as exc:
            raise SlackSignatureError("payload do Slack inválido") from exc
        if not isinstance(payload, dict) or payload.get("type") == "url_verification":
            return self._challenge(payload)
        for mensagem in parse_slack_events(payload):
            if mensagem.user not in self._allowed:
                continue  # fail-closed: usuário fora da lista não fala
            if self._is_seen(mensagem.message_id):
                continue
            if not self._adapter:
                logger.warning(
                    "slack inbound: entrega de %s sem adapter de envio (não responderá): %s",
                    mensagem.user,
                    mensagem.message_id,
                )
                continue
            task = asyncio.get_running_loop().create_task(self._dispatch(mensagem))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return None

    def _challenge(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            raise SlackSignatureError("payload do Slack inválido")
        challenge = payload.get("challenge")
        if not isinstance(challenge, str) or not challenge:
            raise SlackSignatureError("desafio de verificação de URL ausente")
        return challenge

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

    async def _dispatch(self, mensagem: SlackMessage) -> None:
        conversation_id = f"slack:{mensagem.user}"
        lock = self._chat_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            try:
                envelope = self._build_envelope(conversation_id, mensagem)
                text, error = await _stream_turn(self, envelope, mensagem.channel)
            except BaseException as exc:  # noqa: BLE001
                logger.warning("slack inbound: turno falhou: %s", exc)
                final = "Falha ao processar sua mensagem — abra o painel Kairos."
            else:
                final = text or error or "Sem resposta."
            await self._send_chunked(mensagem.channel, final)

    def _build_envelope(self, conversation_id: str, mensagem: SlackMessage) -> Any:
        from kairos_integration.interaction_contract import InteractionEnvelope

        content = self._augment(mensagem.text)
        return InteractionEnvelope(
            conversation_id=conversation_id,
            source="slack",
            content=content,
            idempotency_key=f"slack:{mensagem.message_id}",
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

    async def _notice_approval(self, channel: str, event: Any) -> None:
        """Aprovação de ferramenta não tem botões no Slack — recusa honesta."""
        tool_call = getattr(event, "tool_call", None)
        tool_name = getattr(tool_call, "name", "?") if tool_call is not None else "?"
        if self._adapter is None:
            return
        texto = (
            "Aprovacao necessaria para a ferramenta "
            f"'{tool_name}'.\nO Slack nao tem botoes de aprovacao — "
            "abra o painel Kairos para decidir."
        )
        with suppress(BaseException):
            await self._send_chunked(channel, texto)

    # --- envio final ---

    async def _send_chunked(self, channel: str, text: str) -> None:
        if self._adapter is None:
            return
        for i, chunk in enumerate(_chunk_text(text)):
            if i >= 10:
                await _protect_send(
                    self._adapter,
                    f"slack:{channel}",
                    "Saida longa — consulte o painel para o resultado completo.",
                )
                break
            await _protect_send(self._adapter, f"slack:{channel}", chunk)


async def _protect_send(adapter: Any, target: str, text: str) -> None:
    """Envia com suporte a falhas; adapter.sender é síncrono no gateway."""
    try:
        await asyncio.to_thread(adapter.send, target, text)
    except BaseException as exc:  # noqa: BLE001
        logger.warning("slack inbound: envio para %s falhou: %s", target, exc)


def _event_kind(event: Any) -> str | None:
    kind = getattr(event, "kind", None)
    if isinstance(kind, str):
        return kind
    value = getattr(kind, "value", None)
    return value if isinstance(value, str) else None


async def _stream_turn(
    inbound: SlackInbound, envelope: Any, destinatario: str
) -> tuple[str | None, str | None]:
    """Roda o turno pelo router e recolhe o texto; o destinatário é o canal."""
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


def build_slack_inbound(
    home: Path | str,
    *,
    router: Any = None,
    adapter: Any = None,
    signing_secret: str | None = None,
) -> SlackInbound:
    """Monta o canal de entrada com o ``InteractionRouter`` e destino padrão."""
    if router is None:
        from kairos_integration.composition import build_interaction_router

        router = build_interaction_router(Path(home))
    if adapter is None:
        from kairos_gateway.adapters import build_platform_adapters

        adapter = build_platform_adapters(Path(home)).get("slack")
    return SlackInbound(
        Path(home),
        router,
        signing_secret=signing_secret,
        adapter=adapter,
    )
