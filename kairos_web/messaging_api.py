"""API de mensageria do painel: configurar, testar e enviar por plataforma.

Sem endpoint novo sem efeito: o envio grava a obrigação no ledger durável do
gateway e, se o adapter responder, confirma na hora — a entrega real é o
serviço do gateway (`kairos gateway run`), e a fila sobrevive ao processo.

Segredos passam pelo cofre (`save_platform_secret`); config não-secreta fica
em `messaging.json`. O painel nunca devolve o segredo — só `configured`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictStr

from kairos_gateway.adapters import (
    PLATFORMS,
    build_platform_adapters,
    messaging_status,
)
from kairos_gateway.adapters.config import (
    add_webhook_endpoint,
    drop_platform_secret,
    load_config,
    remove_webhook_endpoint,
    save_config,
    save_platform_secret,
    webhook_endpoints,
)
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.ledger import LedgerRepository

router = APIRouter(prefix="/api")

_PLATFORM_NAMES = {platform.name for platform in PLATFORMS if platform.needs_secret}


def _home(request: Request) -> Path:
    from kairos_web.server import _application_home

    return Path(_application_home(request.app))


def _known(platform: str) -> None:
    if platform not in _PLATFORM_NAMES and platform != "webhook":
        raise HTTPException(404, "plataforma desconhecida")


class ConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: StrictStr


class TestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: StrictStr | None = None


class WebhookBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr
    url: StrictStr


class SendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: StrictStr
    text: StrictStr


@router.get("/messaging")
def status(request: Request):
    return messaging_status(_home(request))


@router.put("/messaging/{platform}")
def update_platform(platform: str, body: ConfigBody, request: Request):
    _known(platform)
    try:
        doc = load_config(_home(request))
        novo = {**doc[platform]}
        novo.update(body.config)
        doc[platform] = novo
        saved = save_config(_home(request), doc)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc) or "configuração inválida") from exc
    return {"platform": platform, "config": saved[platform]}


@router.post("/messaging/{platform}/credential")
def save_credential(platform: str, body: CredentialBody, request: Request):
    _known(platform)
    try:
        return save_platform_secret(_home(request), platform, body.secret)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        from kairos_security.credentials import VaultLockedError

        if isinstance(exc, VaultLockedError):
            raise HTTPException(
                409, "cofre de credenciais bloqueado; desbloqueie ou inicialize"
            ) from exc
        raise HTTPException(503, "cofre de credenciais indisponível") from exc


@router.delete("/messaging/{platform}/credential")
def remove_credential(platform: str, request: Request):
    _known(platform)
    try:
        return drop_platform_secret(_home(request), platform)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        from kairos_security.credentials import VaultLockedError

        if isinstance(exc, VaultLockedError):
            raise HTTPException(409, "cofre de credenciais bloqueado") from exc
        raise HTTPException(503, "cofre de credenciais indisponível") from exc


@router.post("/messaging/{platform}/test")
def test_platform(platform: str, body: TestBody, request: Request):
    """Verifica a conexão com a plataforma; envia um teste quando faz sentido.

    Telegram/WhatsApp têm verificação sem envio (getMe/perfil); Slack e Webhook
    não têm — para eles, testar é despachar uma mensagem de teste de verdade.
    """
    _known(platform)
    adapters = build_platform_adapters(_home(request))
    adapter = adapters.get(platform)
    if adapter is None:
        raise HTTPException(400, "plataforma não está entregável: habilite-a e salve a credencial")
    alvo = body.target or _default_test_target(platform)

    if alvo:
        from kairos_gateway.service import SendResult

        try:
            resultado = adapter.send(alvo, TEST_TEXT)
        except Exception:  # noqa: BLE001
            resultado = SendResult(ok=False, retryable=True, error_kind="excecao")
        return {
            "ok": resultado.ok,
            "sent": True,
            "message": "mensagem de teste enviada" if resultado.ok else "envio de teste falhou",
        }

    verified = _verify(adapter)
    return {"ok": verified["ok"], "sent": False, "message": verified.get("message", "")}


def _default_test_target(platform: str) -> str | None:
    """Slack/Webhook testam com envio real; as demais esperam destino opcional."""
    if platform == "slack":
        return "slack:"
    if platform == "webhook":
        return "webhook:"
    return None


@router.get("/messaging/webhook/endpoints")
def webhook_endpoints_list(request: Request):
    """Endpoint de webhook tem gestão própria: listar, adicionar, remover.

    O webhook não tem segredo — o endpoint é a entrega — então estes endpoints
    valem sem cofre. Testar um endpoint específico é o POST de teste com
    destino `webhook:{nome}`, reutilizando o adapter real.
    """
    home = _home(request)
    return {
        "enabled": load_config(home).get("webhook", {}).get("enabled", False),
        "endpoints": webhook_endpoints(home),
    }


@router.post("/messaging/webhook/endpoints")
def webhook_endpoint_add(body: WebhookBody, request: Request):
    """Adiciona um endpoint ao arquivo; validação falha fechado em 422."""
    try:
        return add_webhook_endpoint(_home(request), body.name, body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/messaging/webhook/endpoints/{name}")
def webhook_endpoint_remove(name: str, request: Request):
    """Remove um endpoint nomeado; nome inexistente é erro (422), não silêncio."""
    try:
        return remove_webhook_endpoint(_home(request), name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


TEST_TEXT = "Messageria Kairos — teste de conexão."


def _verify(adapter) -> dict:
    nao_exige_verify = getattr(adapter, "name", None) in ("slack", "webhook")
    metodo = getattr(adapter, "verify", None)
    if nao_exige_verify:
        return {"ok": True, "message": ""}
    if metodo is None:
        return {"ok": True, "message": ""}
    try:
        return metodo()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc) or "falha de conexão"}


@router.post("/messaging/send")
def send_message(body: SendBody, request: Request):
    """Envia agora via adapter e registra a obrigação no ledger durável.

    Sucesso confirma a obrigação; falha transitória deixa `pending` para o
    gateway reentregar; falha permanente abandona. Nenhum efeito sem rastro.
    """
    home = _home(request)
    adapters = build_platform_adapters(home)

    plataforma, separador, destino = body.target.partition(":")
    if not separador or not plataforma or not destino:
        raise HTTPException(422, "target deve ser plataforma:destino")
    if plataforma not in _PLATFORM_NAMES and plataforma != "webhook":
        raise HTTPException(400, "plataforma desconhecida ou não configurada")
    adapter = adapters.get(plataforma)
    if adapter is None:
        raise HTTPException(400, "plataforma não está entregável: habilite-a e salve a credencial")

    obligation_id = uuid.uuid4().hex
    db = connect(home / "state.db")
    try:
        migrate(db)
        repo = LedgerRepository(db)
        repo.record(obligation_id, body.target, body.text)
        pid, started = _pid(), _started_at()
        if not repo.claim(obligation_id, pid=pid, started_at=started):
            return {"delivered": False, "pending": True, "obligation_id": obligation_id}
        from kairos_gateway.service import SendResult

        try:
            resultado = adapter.send(body.target, body.text)
        except (httpx.HTTPError, OSError, ValueError, KeyError):
            resultado = SendResult(ok=False, retryable=True, error_kind="excecao")
        if resultado.ok:
            repo.confirm(obligation_id)
            return {"delivered": True, "pending": False, "obligation_id": obligation_id}
        if resultado.retryable:
            repo.release(obligation_id)
            return {
                "delivered": False,
                "pending": True,
                "obligation_id": obligation_id,
                "detail": f"falha temporária ({resultado.error_kind})",
            }
        repo.abandon(obligation_id)
        return {
            "delivered": False,
            "pending": False,
            "obligation_id": obligation_id,
            "detail": f"falha permanente ({resultado.error_kind})",
        }
    finally:
        db.close()


def _pid() -> int:
    import os

    return os.getpid()


def _started_at() -> int:
    import time

    return int(time.time())
