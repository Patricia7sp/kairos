"""Webhooks públicos de canais de entrada: WhatsApp, Telegram, Slack e Webhook.

Estas rotas ficam fora da autenticação de sessão (``_OPEN_PATHS``) de
propósito: quem chama é a Meta, o Telegram, o Slack ou o seu serviço de
integração, não a interface. A segurança é a do próprio canal — o WhatsApp
exige o apertão de mão com o Verify Token e a assinatura
``X-Hub-Signature-256`` (HMAC-SHA256 do corpo cru com o App Secret); o Telegram
o ``X-Telegram-Bot-Api-Secret-Token``; o Slack o ``X-Slack-Signature`` (v0 com
anti-replay por relógio); e o webhook genérico o token de ingestão no cofre —
e sempre fail-closed: sem segredo no cofre o webhook recusa (503), autenticação
divergente rejeita (401), e o acuse só sai após a validação.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from kairos_gateway.inbound import TelegramError, TelegramUnauthorizedError, build_telegram_inbound
from kairos_gateway.slack_inbound import (
    SlackConfigError,
    SlackSignatureError,
    build_slack_inbound,
)
from kairos_gateway.webhook_inbound import (
    WebhookConfigError,
    WebhookUnauthorizedError,
    build_webhook_inbound,
)
from kairos_gateway.whatsapp_inbound import (
    WhatsAppConfigError,
    WhatsAppSignatureError,
    build_whatsapp_inbound,
)

router = APIRouter(prefix="/api/inbound", tags=["inbound"])


def _whatsapp_inbound(request: Request):
    """Instância compartilhada (dedupe entre entregas); monta na primeira vez."""
    state = request.app.state
    inbound = getattr(state, "whatsapp_inbound", None)
    if inbound is None:
        from kairos_integration import build_interaction_router
        from kairos_web.server import _application_home

        home = _application_home(request.app)
        service = getattr(state, "interaction_service", None)
        if service is None:
            service = build_interaction_router(home)
        inbound = build_whatsapp_inbound(home, router=service)
        state.whatsapp_inbound = inbound
    return inbound


def _telegram_inbound(request: Request):
    """Instância compartilhada, recriada quando o modo configurado mudar."""
    from kairos_gateway.adapters.config import load_config
    from kairos_integration import build_interaction_router
    from kairos_web.server import _application_home

    state = request.app.state
    home = _application_home(request.app)
    mode = str(load_config(home)["telegram"].get("inbound", {}).get("mode", "poll"))
    inbound = getattr(state, "telegram_inbound", None)
    if inbound is None or getattr(inbound, "mode", None) != mode:
        service = getattr(state, "interaction_service", None)
        if service is None:
            service = build_interaction_router(home)
        inbound = build_telegram_inbound(home, router=service)
        state.telegram_inbound = inbound
    return inbound


def _slack_inbound(request: Request):
    """Instância compartilhada; monta na primeira entrega."""
    from kairos_integration import build_interaction_router
    from kairos_web.server import _application_home

    state = request.app.state
    inbound = getattr(state, "slack_inbound", None)
    if inbound is None:
        home = _application_home(request.app)
        service = getattr(state, "interaction_service", None)
        if service is None:
            service = build_interaction_router(home)
        inbound = build_slack_inbound(home, router=service)
        state.slack_inbound = inbound
    return inbound


def _webhook_inbound(request: Request):
    """Instância compartilhada; monta na primeira entrega."""
    from kairos_integration import build_interaction_router
    from kairos_web.server import _application_home

    state = request.app.state
    inbound = getattr(state, "webhook_inbound", None)
    if inbound is None:
        home = _application_home(request.app)
        service = getattr(state, "interaction_service", None)
        if service is None:
            service = build_interaction_router(home)
        inbound = build_webhook_inbound(home, router=service)
        state.webhook_inbound = inbound
    return inbound


@router.get("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_verify(request: Request):
    """Apertão de mão do subscribe: devolve ``hub.challenge`` quando confere."""
    hub = request.query_params
    inbound = _whatsapp_inbound(request)
    try:
        challenge = inbound.verify_handshake(
            mode=hub.get("hub.mode"),
            verify_token=hub.get("hub.verify_token"),
            challenge=hub.get("hub.challenge"),
        )
    except WhatsAppConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WhatsAppSignatureError as exc:
        raise HTTPException(401, str(exc)) from exc
    return PlainTextResponse(challenge)


@router.post("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_receive(request: Request):
    """Entrega de mensagem: valida a assinatura e acusa ``EVENT_RECEIVED``."""
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    inbound = _whatsapp_inbound(request)
    try:
        inbound.accept(raw_body, signature)
    except WhatsAppConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WhatsAppSignatureError as exc:
        raise HTTPException(401, str(exc)) from exc
    return PlainTextResponse("EVENT_RECEIVED")


@router.post("/telegram", response_class=PlainTextResponse)
async def telegram_receive(request: Request):
    """Entrega do Telegram: secret_token conferido e ``update`` acusado.

    Só aceita no modo ``webhook``; o processamento roda em task com a mesma
    autenticação (allowlist) e idempotência do long-poll.
    """
    raw_body = await request.body()
    header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    try:
        inbound = _telegram_inbound(request)
    except TelegramError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        await inbound.accept_webhook(raw_body, header_token)
    except TelegramUnauthorizedError as exc:
        raise HTTPException(401, str(exc)) from exc
    except TelegramError as exc:
        raise HTTPException(503, str(exc)) from exc
    return PlainTextResponse("ok")


@router.post("/slack", response_class=PlainTextResponse)
async def slack_receive(request: Request):
    """Entrega do Slack Events API: assinatura ``v0`` conferida e evento acusado.

    ``url_verification`` devolve o desafio no corpo (o Slack exige em
    segundos); ``event_callback`` agenda os turnos e acusa ``ok``. Todas as
    entregas são assinadas — inclusive o desafio.
    """
    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    signature = request.headers.get("X-Slack-Signature")
    try:
        inbound = _slack_inbound(request)
        challenge = inbound.accept(raw_body, timestamp_header=timestamp, signature_header=signature)
    except SlackConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except SlackSignatureError as exc:
        raise HTTPException(401, str(exc)) from exc
    if challenge is not None:
        return PlainTextResponse(challenge)
    return PlainTextResponse("ok")


@router.post("/webhook", response_class=PlainTextResponse)
async def webhook_receive(request: Request):
    """Ingestão genérica por token: autentica, valida a fonte e agenda o turno."""
    raw_body = await request.body()
    token = request.headers.get("X-Kairos-Webhook-Token")
    source = request.headers.get("X-Kairos-Webhook-Source")
    try:
        inbound = _webhook_inbound(request)
        inbound.accept(raw_body, token_header=token, source_header=source)
    except WebhookConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WebhookUnauthorizedError as exc:
        raise HTTPException(401, str(exc)) from exc
    return PlainTextResponse("ok")
