"""Webhooks públicos de canais de entrada: WhatsApp Cloud API.

Estas rotas ficam fora da autenticação de sessão (``_OPEN_PATHS``) de
propósito: quem chama é a Meta, não a interface. A segurança é a do próprio
canal — apertão de mão com o Verify Token e assinatura ``X-Hub-Signature-256``
(HMAC-SHA256 do corpo cru com o App Secret) — e falha fechado: sem segredo no
cofre o webhook recusa (503), assinatura divergente rejeita (401), e o acuse
``EVENT_RECEIVED`` só sai após a validação.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

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
