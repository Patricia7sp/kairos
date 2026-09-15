"""Servidor FastAPI e WebSocket para a Interface Web do Kairos."""

from __future__ import annotations

import asyncio
import copy
import html
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing, asynccontextmanager
from hmac import compare_digest
from pathlib import Path
from time import monotonic
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kairos_cli.auth import AuthStore
from kairos_integration import build_interaction_router as build_interaction_service
from kairos_integration.interaction_contract import (
    InteractionServiceError,
    InteractionServiceUnavailableError,
)
from kairos_observability.service_events import record_service_event_async
from kairos_providers.adapters.openrouter import OpenRouterRoutingPolicy
from kairos_providers.catalog import UnknownModelError
from kairos_providers.composition import build_provider_gateway
from kairos_providers.contracts import ProviderModelRef, SelectionReason
from kairos_providers.provider_registry import UnknownProviderError
from kairos_providers.settings import load_config_document, update_config_document
from kairos_runtime import RuntimeErrorInfo, RuntimeEvent, public_error
from kairos_security.credentials import (
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    build_credential_service,
)
from kairos_security.credentials.io import credential_file_lock
from kairos_state.repositories import shares as shares_repo
from kairos_state.repositories.sessions import SessionRepository
from kairos_web.chat_transport import (
    interaction_envelope_from_json,
    interaction_event_to_json,
)
from kairos_web.cron_api import router as cron_router
from kairos_web.logs_api import router as logs_router
from kairos_web.message_metadata import public_message_accounting
from kairos_web.messaging_api import router as messaging_router
from kairos_web.observability_api import router as observability_router
from kairos_web.provider_api import (
    list_models_payload,
    list_providers_payload,
    public_parameters,
    public_profiles,
    serialize_model,
)
from kairos_web.provider_credentials_api import router as provider_credentials_router
from kairos_web.provider_settings_api import router as provider_settings_router
from kairos_web.runtime_api import router as runtime_api_router
from kairos_web.runtime_transport import runtime_event_to_json, runtime_websocket_session
from kairos_web.settings_api import router as settings_router
from kairos_web.tools_api import router as tools_router

logger = logging.getLogger(__name__)


def _application_home(application: FastAPI) -> Path:
    supplied = getattr(application.state, "kairos_home", None)
    if supplied is not None:
        return Path(supplied)
    return Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    service = getattr(application.state, "interaction_service", None)
    owns_service = service is None
    if owns_service:
        service = build_interaction_service(_application_home(application))
        application.state.interaction_service = service
    runtime_client = getattr(application.state, "runtime_client", None)
    installed_runtime_client = False
    if runtime_client is None and hasattr(service, "runtime_client"):
        runtime_client = service.runtime_client
        application.state.runtime_client = runtime_client
        installed_runtime_client = True
    from kairos_cron.scheduler import Scheduler

    home = _application_home(application)
    scheduler = Scheduler(home, service)
    cron_task = asyncio.create_task(scheduler.run(), name="kairos-cron-ticker")
    application.state.cron_scheduler = scheduler
    application.state.cron_task = cron_task
    from kairos_gateway.adapters import build_platform_adapters

    application.state.delivery_adapters = sorted(build_platform_adapters(home))
    try:
        await record_service_event_async(home, "web.started")
        yield
    finally:
        try:
            try:
                cron_task.cancel()
                try:
                    await cron_task
                except asyncio.CancelledError:
                    pass
            finally:
                if owns_service:
                    await service.aclose()
        finally:
            del application.state.cron_task
            del application.state.cron_scheduler
            if getattr(application.state, "delivery_adapters", None) is not None:
                del application.state.delivery_adapters
            if owns_service and getattr(application.state, "interaction_service", None) is service:
                del application.state.interaction_service
            if (
                installed_runtime_client
                and getattr(application.state, "runtime_client", None) is runtime_client
            ):
                del application.state.runtime_client
            await record_service_event_async(_application_home(application), "web.stopped")


app = FastAPI(title="Kairos Web API", version="0.1.0", lifespan=_lifespan)
app.include_router(runtime_api_router)
app.include_router(provider_settings_router)
app.include_router(observability_router)
app.include_router(logs_router)
app.include_router(provider_credentials_router)
app.include_router(tools_router)
app.include_router(settings_router)
app.include_router(cron_router)
app.include_router(messaging_router)


@app.exception_handler(RequestValidationError)
async def _safe_runtime_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/cron/"):
        return JSONResponse({"detail": "requisição de agendamento inválida"}, status_code=422)
    if request.url.path.startswith("/api/runtime/"):
        return JSONResponse({"detail": "requisição de runtime inválida"}, status_code=422)
    return await request_validation_exception_handler(request, exc)


# CORS para desenvolvimento local e SPA
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:9119",
        "http://localhost:9119",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SESSÃO ---


# Token gerado uma vez por processo do servidor. Fica só em memória: reiniciar
# o servidor invalida as abas antigas, que é o comportamento desejado para uma
# UI local. `KAIROS_WEB_TOKEN` existe para quem precisa de um valor estável
# (proxy reverso, teste de integração) e assume o risco conscientemente.
def _token_configurado() -> str:
    """Ambiente, arquivo, ou um por processo — nessa ordem.

    O arquivo existe para que haja um caminho de token estável que NÃO passe
    por variável de ambiente: `printenv` num container, um `docker inspect` ou
    um dump de configuração revelam a variável a quem alcança o host. Um
    arquivo 0600 no KAIROS_HOME não aparece em nenhum deles.
    """
    do_ambiente = os.environ.get("KAIROS_WEB_TOKEN")
    if do_ambiente:
        return do_ambiente
    arquivo = Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos")) / "web-token"
    try:
        if arquivo.is_file():
            guardado = arquivo.read_text(encoding="utf-8").strip()
            if guardado:
                return guardado
    except OSError:
        pass
    return secrets.token_urlsafe(32)


SESSION_TOKEN = _token_configurado()

# Clientes não-browser podem mandar o token de sessão neste header. A SPA usa
# um ticket efêmero e de uso único na query do WebSocket.
TOKEN_HEADER = "X-Kairos-Session-Token"  # noqa: S105 — nome de header, não o segredo

# Cookie de sessão. Guarda o mesmo token, mas `httpOnly`: assim o JavaScript
# da página não consegue lê-lo, e o token deixa de viajar dentro do HTML.
# Antes ele era injetado em toda resposta da raiz — quem alcançasse a porta
# obtinha a credencial só de carregar a página, sem autenticar-se.
SESSION_COOKIE = "kairos_session"

WS_TICKET_TTL_SECONDS = 30
_WS_TICKETS: dict[str, float] = {}
_WS_TICKETS_LOCK = threading.Lock()


def _issue_ws_ticket() -> str:
    now = monotonic()
    ticket = secrets.token_urlsafe(32)
    with _WS_TICKETS_LOCK:
        expired = [value for value, deadline in _WS_TICKETS.items() if deadline <= now]
        for value in expired:
            del _WS_TICKETS[value]
        _WS_TICKETS[ticket] = now + WS_TICKET_TTL_SECONDS
    return ticket


def _consume_ws_ticket(ticket: str | None) -> bool:
    if not ticket:
        return False
    now = monotonic()
    with _WS_TICKETS_LOCK:
        deadline = _WS_TICKETS.pop(ticket, None)
    return deadline is not None and deadline > now


def _revoke_ws_tickets() -> None:
    with _WS_TICKETS_LOCK:
        _WS_TICKETS.clear()


# `/api/health` fica aberto: é o que `kairos doctor` e o healthcheck do
# container sondam, e não devolve nada além de "estou de pé".
# `/api/auth/*` precisa ficar aberto pelo motivo óbvio: é onde se autentica, e
# é o que a interface consulta para saber se deve mostrar a tela de login.
_OPEN_PATHS = frozenset({"/api/health", "/api/auth/me", "/api/auth/login", "/api/auth/logout"})


def _token_ok(supplied: str | None) -> bool:
    """Compara em tempo constante — `==` em segredo vaza por timing."""
    return bool(supplied) and compare_digest(supplied, SESSION_TOKEN)


def _credencial(request) -> str | None:
    """A credencial da requisição, venha de onde vier.

    Cookie primeiro porque é o caminho da interface; header e query seguem
    valendo para CLI, WebSocket e integração.
    """
    return (
        request.cookies.get(SESSION_COOKIE)
        or request.headers.get(TOKEN_HEADER)
        or request.query_params.get("token")
    )


def _autenticado(request) -> bool:
    return _token_ok(_credencial(request))


@app.middleware("http")
async def require_session_token(request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in _OPEN_PATHS and not _autenticado(request):
        return JSONResponse({"error": "invalid_session_token"}, status_code=401)
    return await call_next(request)


def _get_auth_store() -> AuthStore:
    kairos_home = _application_home(app)
    auth_path = kairos_home / "auth.json"
    data: dict[str, Any] = {}
    if auth_path.exists():
        try:
            data = json.loads(auth_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001, S110
            pass
    return AuthStore(
        profile=data.get("credential_pool", {}), vault=build_credential_service(kairos_home)
    )


# --- REST ENDPOINTS ---


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "app": "kairos",
        "version": "0.1.0",
        "ui_present": (Path(__file__).parent / "ui").exists(),
    }


@app.get("/api/config")
async def get_config():
    config = load_config_document(_application_home(app))
    return config


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


# A SPA salva a configuração com PUT (`saveConfig` em api-*.js); só POST
# estava registrado, então a troca de idioma batia em 405 e não era aplicada —
# sem erro visível, porque o cliente não trata o status. Os dois verbos ficam
# aceitos: o PUT é o que o cliente usa, o POST é o que já existia.
@app.put("/api/config")
@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    try:

        def mutate(document):
            current_settings = document.get("provider_settings")
            if (
                "provider_settings" in req.config
                and req.config["provider_settings"] != current_settings
            ):
                raise HTTPException(422, "Altere os provedores na tela Provedores.")
            document.clear()
            document.update(req.config)
            if current_settings is not None:
                document["provider_settings"] = current_settings

        config = update_config_document(_application_home(app), mutate)
        return {"status": "saved", "config": config}
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="configuração inválida") from exc


@app.get("/api/models")
async def list_models(
    provider: str | None = None,
    free_only: bool = False,
    include_preview: bool = False,
):
    gateway = build_provider_gateway(_application_home(app))
    config = load_config_document(_application_home(app))
    default_model = config.get("model", "claude-3-7-sonnet-20250219")
    default_provider = config.get("provider", "anthropic")
    try:
        payload = list_models_payload(
            gateway,
            provider=provider,
            free_only=free_only,
            include_preview=include_preview,
            default_provider=default_provider,
            default_model=default_model,
        )
        payload["default_parameters"] = public_parameters(config.get("parameters"))
        payload["profiles"] = public_profiles(config)
        return payload
    finally:
        await gateway.aclose()


class SetDefaultModelRequest(BaseModel):
    model: str
    provider: str | None = None
    task: str | None = None


class ModelSelectionRequest(BaseModel):
    provider: str
    model: str
    scope: Literal["conversation", "profile", "global"]
    session_id: str | None = None
    profile: str | None = None
    parameters: dict[str, Any] | None = None


def _merge_parameter_patch(existing: object, incoming: dict[str, Any]) -> dict[str, Any]:
    if not incoming:
        return {}
    merged = copy.deepcopy(dict(existing)) if isinstance(existing, Mapping) else {}
    for key, value in incoming.items():
        previous = merged.get(key)
        if isinstance(value, Mapping):
            merged[key] = _merge_parameter_patch(previous, dict(value))
        else:
            merged[key] = copy.deepcopy(value)
    return merged


@app.post("/api/models/selection")
async def select_model(req: ModelSelectionRequest):
    if req.parameters is not None and "routing" in req.parameters:
        try:
            OpenRouterRoutingPolicy.from_parameters(req.parameters["routing"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="política de roteamento inválida") from exc
    gateway = build_provider_gateway(_application_home(app))
    ref = ProviderModelRef(req.provider.strip(), req.model.strip())
    try:
        try:
            model = gateway.catalog.find(ref)
        except UnknownModelError as exc:
            raise HTTPException(
                status_code=422, detail="modelo indisponível para o provider"
            ) from exc
        if not model.is_selectable():
            raise HTTPException(status_code=422, detail="modelo indisponível para o provider")

        if req.scope == "conversation":
            if not req.session_id:
                raise HTTPException(status_code=422, detail="session_id é obrigatório")
            db = _get_db(app)
            try:
                sessions = SessionRepository(db)
                if sessions.get(req.session_id) is None:
                    raise HTTPException(status_code=404, detail="sessão não encontrada")
                previous = sessions.selection(req.session_id)
                parameters = (
                    _merge_parameter_patch(previous.parameters if previous else {}, req.parameters)
                    if req.parameters is not None
                    else (dict(previous.parameters) if previous else {})
                )
                sessions.set_selection(
                    req.session_id,
                    ref,
                    parameters,
                    reason=SelectionReason.CONVERSATION_OVERRIDE,
                    profile=req.profile,
                )
            finally:
                db.close()
        else:
            if req.scope == "profile" and (not req.profile or not req.profile.strip()):
                raise HTTPException(status_code=422, detail="profile é obrigatório")

            def mutate(config):
                target = config
                if req.scope == "profile":
                    target = config.setdefault("profiles", {}).setdefault(req.profile.strip(), {})
                target.update({"provider": ref.provider, "model": ref.model})
                if req.parameters is not None:
                    target["parameters"] = _merge_parameter_patch(
                        target.get("parameters"), req.parameters
                    )

            update_config_document(_application_home(app), mutate)
        return {
            "status": "updated",
            "selection": {
                "provider": ref.provider,
                "model": ref.model,
                "scope": req.scope,
            },
        }
    finally:
        await gateway.aclose()


class RefreshModelsRequest(BaseModel):
    provider: str


@app.post("/api/models/refresh")
async def refresh_models(req: RefreshModelsRequest):
    gateway = build_provider_gateway(_application_home(app))
    try:
        try:
            result = await gateway.refresh(req.provider)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider desconhecido") from exc
        return {
            "provider": req.provider,
            "source": result.source.value,
            "models": [serialize_model(model) for model in result.models],
        }
    finally:
        await gateway.aclose()


@app.post("/api/models/set-default")
async def set_default_model(req: SetDefaultModelRequest):
    def mutate(config):
        if req.task:
            aux = config.setdefault("auxiliary_models", {})
            aux[req.task] = req.model
        else:
            config["model"] = req.model
            if req.provider:
                config["provider"] = req.provider

    config = update_config_document(_application_home(app), mutate)
    return {"status": "updated", "config": config}


@app.get("/api/providers")
async def get_providers_status():
    gateway = build_provider_gateway(_application_home(app))
    try:
        return list_providers_payload(gateway)
    finally:
        await gateway.aclose()


class SaveKeyRequest(BaseModel):
    provider: str
    api_key: str


class SaveCredentialRequest(BaseModel):
    secret: str
    auth_method: str = "api_key"


@app.post("/api/providers/{provider}/credentials")
async def save_provider_credential(provider: str, req: SaveCredentialRequest):
    secret = req.secret.strip()
    if not secret:
        raise HTTPException(status_code=422, detail="secret é obrigatório")
    gateway = build_provider_gateway(_application_home(app))
    try:
        try:
            descriptor = gateway.registry.describe(provider)
        except UnknownProviderError as exc:
            raise HTTPException(status_code=404, detail="provider desconhecido") from exc
        if req.auth_method not in descriptor.auth_methods:
            raise HTTPException(status_code=422, detail="método de autenticação inválido")
    finally:
        await gateway.aclose()

    kairos_home = _application_home(app)
    kairos_home.mkdir(parents=True, exist_ok=True)
    auth_path = kairos_home / "auth.json"
    ref = CredentialRef(provider, "primary")
    with credential_file_lock(auth_path):
        store = _get_auth_store()
        previous_profile = store.profile.get(provider)
        if previous_profile is not None and (
            not isinstance(previous_profile, list)
            or not all(isinstance(entry, dict) for entry in previous_profile)
        ):
            raise HTTPException(503, "metadados de credenciais indisponíveis")
        try:
            try:
                previous = store.vault.get(ref)
            except CredentialNotFoundError:
                previous = None
            metadata = store.vault.put(
                ref,
                CredentialSecret({"api_key": secret}),
                auth_method=req.auth_method,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="cofre de credenciais indisponível"
            ) from exc
        store.profile[provider] = [
            {"credential_id": ref.credential_id, "auth_method": metadata.auth_method},
            *(
                entry
                for entry in (previous_profile or [])
                if entry.get("credential_id") != ref.credential_id
            ),
        ]
        try:
            store.write_atomically(auth_path)
        except Exception:
            if previous is None:
                store.vault.delete(ref)
            else:
                store.vault.put(ref, previous, auth_method=metadata.auth_method)
            if previous_profile is None:
                store.profile.pop(provider, None)
            else:
                store.profile[provider] = previous_profile
            raise
    return {
        "provider": provider,
        "credential_id": ref.credential_id,
        "auth_method": metadata.auth_method,
        "state": "configured",
    }


@app.post("/api/providers/{provider}/test")
async def test_provider_connection(provider: str):
    gateway = build_provider_gateway(_application_home(app))
    try:
        try:
            status = await gateway.test_connection(provider)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider desconhecido") from exc
        return {
            "provider": provider,
            "connected": status.ok,
            "message": status.message,
            "models_count": status.models_found,
            "auth_method": status.auth_method,
            "state": status.state,
        }
    finally:
        await gateway.aclose()


@app.post("/api/providers/save-key")
async def save_provider_key(req: SaveKeyRequest):
    result = await save_provider_credential(
        req.provider,
        SaveCredentialRequest(secret=req.api_key, auth_method="api_key"),
    )
    return {
        "status": "saved",
        "provider": req.provider,
        "credential_status": "saved_unverified",
        "credential_id": result["credential_id"],
        "auth_method": result["auth_method"],
    }


@app.get("/api/providers/vault-status")
async def provider_vault_status():
    store = _get_auth_store()
    try:
        credentials = [
            {
                "provider": item.ref.provider,
                "credential_id": item.ref.credential_id,
                "auth_method": item.auth_method,
                "masked_identifier": item.masked_identifier,
            }
            for item in store.vault.list()
        ]
    except Exception:  # noqa: BLE001
        credentials = []
    return {"state": store.vault.state, "credentials": credentials}


def _get_db(application: FastAPI):
    from kairos_state import connect, initialize_schema

    conn = connect(_application_home(application) / "state.db")
    initialize_schema(conn)
    return conn


# --- SESSIONS ENDPOINTS ---


def _public_session_selection(selection) -> dict[str, Any] | None:
    if selection is None:
        return None
    return {
        "provider": selection.ref.provider,
        "model": selection.ref.model,
        "parameters": public_parameters(selection.parameters),
        "reason": selection.reason.value,
        **({"profile": selection.profile} if selection.profile else {}),
    }


def _linha_sessao(s) -> dict:
    """A tabela já guarda contagens e custo — devolvê-los evita que a interface
    peça as mensagens de cada sessão só para saber quantas são."""
    chaves = s.keys()

    def campo(nome, padrao=None):
        return s[nome] if nome in chaves else padrao

    execution_kind = campo("execution_kind", "model") or "model"
    result = {
        "id": s["id"],
        "source": s["source"],
        "title": campo("display_name") or campo("title") or "",
        "model": campo("model") or "",
        "started_at": s["started_at"],
        "ended_at": s["ended_at"],
        "end_reason": s["end_reason"],
        # Sem `ended_at` a sessão nunca foi encerrada: está aberta.
        "status": "encerrada" if s["ended_at"] else "aberta",
        "archived": bool(campo("archived", 0)),
        "pinned": bool(campo("pinned", 0)),
        "hidden": bool(campo("hidden", 0)),
        "message_count": campo("message_count", 0) or 0,
        "tool_call_count": campo("tool_call_count", 0) or 0,
        "input_tokens": campo("input_tokens", 0) or 0,
        "output_tokens": campo("output_tokens", 0) or 0,
        "execution_kind": execution_kind,
    }
    if execution_kind == "agent_runtime":
        capabilities = campo("runtime_capabilities")
        try:
            capabilities = json.loads(capabilities) if capabilities else None
        except (TypeError, json.JSONDecodeError):
            capabilities = None
        result.update(
            {
                "runtime_kind": campo("runtime_kind"),
                "cwd": campo("requested_cwd") or campo("cwd"),
                "canonical_cwd": campo("canonical_cwd"),
                "sandbox": campo("sandbox_profile"),
                "external_thread_id": campo("external_thread_id"),
                "runtime_state": campo("runtime_state"),
                "protocol_version": campo("runtime_protocol_version"),
                "capabilities": capabilities,
                "broad_consent": campo("broad_consent_at") is not None,
            }
        )
    return result


_SESSION_RUNTIME_COLUMNS = (
    "r.runtime_kind,r.external_thread_id,r.requested_cwd,r.canonical_cwd,"
    "r.sandbox_profile,r.broad_consent_at,r.state AS runtime_state,"
    "r.protocol_version AS runtime_protocol_version,r.capabilities_json AS runtime_capabilities"
)


def _contagem_de_mensagens(conn, ids: list[str]) -> dict[str, int]:
    """Conta as mensagens ativas por sessão.

    `sessions.message_count` existe na tabela, mas nenhum caminho de escrita o
    incrementa — a coluna fica em zero enquanto as mensagens estão lá. Contar
    da fonte é uma consulta agregada, não N+1, e não depende de todo produtor
    lembrar de atualizar um contador.
    """
    if not ids:
        return {}
    lugares = ", ".join("?" for _ in ids)
    linhas = conn.execute(
        f"SELECT session_id, COUNT(*) AS n FROM messages "  # noqa: S608 — apenas placeholders são interpolados
        f"WHERE active = 1 AND session_id IN ({lugares}) GROUP BY session_id",
        ids,
    ).fetchall()
    return {linha["session_id"]: linha["n"] for linha in linhas}


def _tags_para_sessoes(conn, ids: list[str]) -> dict[str, list[str]]:
    if not ids:
        return {}
    lugares = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT session_id, tag FROM session_tags WHERE session_id IN ({lugares}) ORDER BY tag",  # noqa: S608 — apenas placeholders são interpolados
        ids,
    ).fetchall()
    tags: dict[str, list[str]] = {}
    for row in rows:
        tags.setdefault(row["session_id"], []).append(row["tag"])
    return tags


def _contagens_de_tags(conn, where: list[str], args: list[Any]) -> list[dict[str, Any]]:
    sql = (
        "SELECT st.tag, COUNT(*) AS n FROM session_tags st JOIN sessions s ON s.id = st.session_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY st.tag ORDER BY st.tag"
    rows = conn.execute(sql, args).fetchall()
    return [{"tag": row["tag"], "count": row["n"]} for row in rows]


@app.get("/api/sessions")
async def list_sessions(
    request: Request,
    *,
    limit: int = 50,
    offset: int = 0,
    q: str = "",
    status: str = "todas",
    tag: str = "",
):
    conn = _get_db(request.app)
    try:
        limite = max(1, min(limit, 100))
        deslocamento = max(0, offset)
        termo = q.strip()
        tag_filtro = tag.strip().lower()
        if len(tag_filtro) > 32:
            return JSONResponse({"error": "invalid_session_tag"}, status_code=400)
        estados = {"todas", "abertas", "encerradas", "arquivadas", "ocultas"}
        if status not in estados:
            return JSONResponse(
                {"error": "invalid_session_status", "allowed": sorted(estados)},
                status_code=400,
            )
        where: list[str] = []
        args: list[Any] = []
        if status == "ocultas":
            where.append("s.hidden = 1")
        else:
            where.append("s.hidden = 0")
        if status == "abertas":
            where.append("s.ended_at IS NULL AND s.archived = 0")
        elif status == "encerradas":
            where.append("s.ended_at IS NOT NULL AND s.archived = 0")
        elif status == "arquivadas":
            where.append("s.archived = 1")
        if termo:
            like = f"%{termo}%"
            where.append(
                "(s.id LIKE ? OR COALESCE(NULLIF(s.display_name, ''), s.title, '') LIKE ? "
                "OR COALESCE(s.source, '') LIKE ? OR COALESCE(s.model, '') LIKE ? "
                "OR EXISTS (SELECT 1 FROM messages mq WHERE mq.session_id = s.id "
                "AND mq.content LIKE ?))"
            )
            args.extend([like, like, like, like, like])
        tag_where = list(where)
        tag_args = list(args)
        if tag_filtro:
            where.append(
                "EXISTS (SELECT 1 FROM session_tags stf WHERE stf.session_id = s.id AND stf.tag = ?)"
            )
            args.append(tag_filtro)
        sql = f"SELECT s.*,{_SESSION_RUNTIME_COLUMNS} FROM sessions s LEFT JOIN runtime_sessions r ON r.session_id=s.id"  # noqa: S608 - fixed internal projection
        if where:
            sql += " WHERE " + " AND ".join(where)
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM sessions s WHERE " + " AND ".join(where),  # noqa: S608 — predicados vêm apenas dos ramos constantes acima; valores ficam parametrizados
                args,
            ).fetchone()[0]
        )
        sql += " ORDER BY s.pinned DESC, s.started_at DESC LIMIT ? OFFSET ?"
        args.extend([limite, deslocamento])
        rows = conn.execute(sql, args).fetchall()
        ids = [s["id"] for s in rows]
        contagens = _contagem_de_mensagens(conn, ids)
        tags_por_sessao = _tags_para_sessoes(conn, ids)
        sessoes = []
        for s in rows:
            linha = _linha_sessao(s)
            linha["message_count"] = contagens.get(linha["id"], linha["message_count"])
            linha["tags"] = tags_por_sessao.get(linha["id"], [])
            sessoes.append(linha)
        return {
            "sessions": sessoes,
            "total": total,
            "offset": deslocamento,
            "limit": limite,
            "has_more": deslocamento + len(sessoes) < total,
            "abertas": sum(1 for s in sessoes if s["status"] == "aberta"),
            "tag_counts": _contagens_de_tags(conn, tag_where, tag_args),
            "filtro": {"q": termo, "status": status, "tag": tag_filtro},
        }
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    conn = _get_db(request.app)
    try:
        s = conn.execute(
            f"SELECT s.*,{_SESSION_RUNTIME_COLUMNS} FROM sessions s LEFT JOIN runtime_sessions r ON r.session_id=s.id WHERE s.id=?",  # noqa: S608 - fixed internal projection
            (session_id,),
        ).fetchone()
        if s is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
        linha = _linha_sessao(s)
        linha["message_count"] = _contagem_de_mensagens(conn, [session_id]).get(
            session_id, linha["message_count"]
        )
        linha["tags"] = _tags_para_sessoes(conn, [session_id]).get(session_id, [])
        linha["selection"] = _public_session_selection(
            SessionRepository(conn).selection(session_id)
        )
        return linha
    finally:
        conn.close()


_ALTERACAO_AUSENTE = object()


def _separar_atualizacao(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str] | None, object, JSONResponse | None]:
    """Separa flags, tags e apelido do payload de atualização da sessão.

    Retorna ``(flags, tags, display_name, erro)``. ``display_name`` é
    ``_ALTERACAO_AUSENTE`` quando a chave não veio; vazio/``None`` significam
    "limpar o apelido". Quando ``erro`` volta preenchido, os demais campos não
    devem ser aplicados.
    """
    permitidos = {"archived", "pinned", "hidden", "tags", "display_name"}
    desconhecidos = set(payload) - permitidos
    if desconhecidos or not payload:
        return (
            {},
            None,
            _ALTERACAO_AUSENTE,
            JSONResponse(
                {"error": "invalid_session_update", "allowed": sorted(permitidos)},
                status_code=400,
            ),
        )
    flags = {k: v for k, v in payload.items() if k not in ("tags", "display_name")}
    if any(not isinstance(v, bool) for v in flags.values()):
        return (
            {},
            None,
            _ALTERACAO_AUSENTE,
            JSONResponse({"error": "session_flags_must_be_boolean"}, status_code=400),
        )
    tags: list[str] | None = None
    if "tags" in payload:
        tags_payload = payload.get("tags")
        if not isinstance(tags_payload, list) or any(not isinstance(v, str) for v in tags_payload):
            return (
                {},
                None,
                _ALTERACAO_AUSENTE,
                JSONResponse({"error": "session_tags_must_be_a_list_of_strings"}, status_code=400),
            )
        tags = sorted({v.strip().lower() for v in tags_payload if v.strip()})
        if len(tags) > 20 or any(len(v) > 32 for v in tags):
            return (
                {},
                None,
                _ALTERACAO_AUSENTE,
                JSONResponse({"error": "session_tags_limit_exceeded"}, status_code=400),
            )
    display_name: object = _ALTERACAO_AUSENTE
    if "display_name" in payload:
        valor_nome = payload["display_name"]
        if valor_nome is not None and not isinstance(valor_nome, str):
            return (
                {},
                None,
                _ALTERACAO_AUSENTE,
                JSONResponse({"error": "session_display_name_must_be_string"}, status_code=400),
            )
        display_name = (valor_nome or "").strip()
        if isinstance(display_name, str) and len(display_name) > 120:
            return (
                {},
                None,
                _ALTERACAO_AUSENTE,
                JSONResponse(
                    {"error": "session_display_name_too_long", "max": 120}, status_code=400
                ),
            )
    return flags, tags, display_name, None


@app.patch("/api/sessions/{session_id}")
async def update_session(session_id: str, payload: dict[str, Any], request: Request):
    """Atualiza metadados de organização; o transcript é imutável aqui.

    Além das flags e das tags, aceita ``display_name`` (renomear): valor vazio
    ou só de espaços limpa o apelido e o catálogo volta ao ``title`` gerado.
    """
    flags, tags, display_name, erro = _separar_atualizacao(payload)
    if erro is not None:
        return erro

    conn = _get_db(request.app)
    try:
        if SessionRepository(conn).get(session_id) is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
        colunas = {"archived": "archived", "pinned": "pinned", "hidden": "hidden"}
        with conn:
            if flags:
                assignments = ", ".join(f"{colunas[k]} = ?" for k in flags)
                valores = [int(flags[k]) for k in flags]
                valores.append(session_id)
                conn.execute(f"UPDATE sessions SET {assignments} WHERE id = ?", valores)  # noqa: S608 — colunas vêm da lista permitida acima
            if display_name is not _ALTERACAO_AUSENTE:
                conn.execute(
                    "UPDATE sessions SET display_name = ? WHERE id = ?",
                    (display_name or None, session_id),
                )
            if tags is not None:
                conn.execute("DELETE FROM session_tags WHERE session_id = ?", (session_id,))
                conn.executemany(
                    "INSERT INTO session_tags(session_id, tag) VALUES (?, ?)",
                    [(session_id, value) for value in tags],
                )
        linha = conn.execute(
            f"SELECT s.*,{_SESSION_RUNTIME_COLUMNS} FROM sessions s "  # noqa: S608 - fixed internal projection
            "LEFT JOIN runtime_sessions r ON r.session_id=s.id WHERE s.id=?",
            (session_id,),
        ).fetchone()
        linha_dict = _linha_sessao(linha)
        linha_dict["tags"] = _tags_para_sessoes(conn, [session_id]).get(session_id, [])
        return linha_dict
    finally:
        conn.close()


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Apaga a conversa e seus dependentes numa transação.

    Sessões de agent runtime são recusadas: a identidade do runtime é
    imutável após o vínculo e o próprio schema bloqueia a remoção com um
    trigger. O caminho da conversa model é o que o chat usa — e é o único que
    esta rota apaga.
    """
    conn = _get_db(request.app)
    try:
        linha = conn.execute(
            "SELECT execution_kind FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if linha is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
        runtime = conn.execute(
            "SELECT 1 FROM runtime_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if runtime is not None or linha["execution_kind"] == "agent_runtime":
            return JSONResponse(
                {
                    "error": "session_runtime_not_deletable",
                    "detail": (
                        "Sessões de agent runtime são gerenciadas pelo supervisor; "
                        "use Arquivar para tirá-las da lista."
                    ),
                },
                status_code=409,
            )
        filhos = conn.execute(
            "SELECT 1 FROM sessions WHERE parent_session_id = ? LIMIT 1", (session_id,)
        ).fetchone()
        if filhos is not None:
            return JSONResponse(
                {
                    "error": "session_has_children",
                    "detail": (
                        "Esta conversa tem sessões filhas (compactação/ramificação); "
                        "arquive-a em vez de excluir, para não romper a linhagem."
                    ),
                },
                status_code=409,
            )
        with conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_model_usage WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_tags WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM compression_locks WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_turn_leases WHERE conversation_id = ?", (session_id,))
            conn.execute("DELETE FROM gateway_routing WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM delivery_obligations WHERE session_id = ?", (session_id,))
            conn.execute(
                "DELETE FROM async_delegations "
                "WHERE origin_session = ? OR origin_ui_session_id = ?",
                (session_id, session_id),
            )
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return {"status": "deleted", "id": session_id}
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request):
    conn = _get_db(request.app)
    try:
        session = SessionRepository(conn).get(session_id)
        if session is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
        execution_kind = (
            session["execution_kind"]
            if "execution_kind" in session.keys()  # noqa: SIM118 - sqlite3.Row membership checks values
            else "model"
        ) or "model"
        if execution_kind == "agent_runtime":
            rows = conn.execute(
                "SELECT m.id,m.role,m.content,m.timestamp,m.tool_name,"
                "COALESCE(u.id,a.id) AS runtime_turn_id,"
                "COALESCE(u.state,a.state) AS runtime_turn_state "
                "FROM messages m LEFT JOIN runtime_turns u ON u.user_message_id=m.id "
                "LEFT JOIN runtime_turns a ON a.assistant_message_id=m.id "
                "WHERE m.session_id=? ORDER BY m.timestamp,m.id",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, role, content, timestamp, display_metadata, tool_name, tool_call_id FROM messages "
                "WHERE session_id = ? ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()

        def message_payload(row):
            payload = {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["timestamp"],
            }
            if row["tool_name"] is not None:
                payload["tool_name"] = row["tool_name"]
            if execution_kind == "agent_runtime":
                payload.update(
                    {
                        "execution_kind": "agent_runtime",
                        "turn_id": row["runtime_turn_id"],
                        "turn_state": row["runtime_turn_state"],
                    }
                )
            elif row["role"] == "assistant":
                payload.update(public_message_accounting(row["display_metadata"]))
                payload.update(public_message_accounting(row["display_metadata"], prefix="turn_"))
            elif row["role"] == "tool":
                payload["tool_call_id"] = row["tool_call_id"]
                try:
                    metadata = json.loads(row["display_metadata"] or "{}")
                except (TypeError, ValueError):
                    metadata = {}
                payload["is_error"] = (
                    isinstance(metadata, dict) and metadata.get("is_error") is True
                )
            return payload

        return {
            "session_id": session_id,
            "execution_kind": execution_kind,
            "messages": [message_payload(row) for row in rows],
        }
    finally:
        conn.close()


# --- COMPARTILHAMENTO DE CONVERSAS ---

_SHARE_MAX_HORIZON_DAYS = 365 * 5
_SHARE_MIN_HORIZON_DAYS = 1


class ShareCreateRequest(BaseModel):
    expires_at: float | None = None


def _share_payload(row, *, agora: float) -> dict[str, Any]:
    expira = row["expires_at"]
    ativo = row["revoked_at"] is None and (expira is None or expira > agora)
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "expires_at": expira,
        "revoked_at": row["revoked_at"],
        "active": ativo,
    }


@app.post("/api/sessions/{session_id}/shares", status_code=201)
async def create_share(
    session_id: str,
    payload: ShareCreateRequest | None,
    request: Request,
) -> JSONResponse:
    """Cria um link de leitura desta conversa.

    O token em texto claro é devolvido **uma única vez**, na criação. O banco
    guarda apenas o digest; por isso a lista/revogação usam o ``id``, e o link
    não é recuperável depois de criado.
    """
    conn = _get_db(request.app)
    try:
        if SessionRepository(conn).get(session_id) is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
        agora = time.time()
        expira = None
        if payload is not None and payload.expires_at is not None:
            expira = payload.expires_at
            if expira <= agora:
                return JSONResponse({"error": "share_expiry_in_past"}, status_code=400)
            if expira > agora + _SHARE_MAX_HORIZON_DAYS * 86400:
                return JSONResponse(
                    {"error": "share_expiry_too_far", "max_days": _SHARE_MAX_HORIZON_DAYS},
                    status_code=400,
                )
        token = shares_repo.new_token()
        with conn:
            share_id = shares_repo.create_share(
                conn, session_id, token, expires_at=expira, created_at=agora
            )
        return JSONResponse(
            {
                "share": {
                    "id": share_id,
                    "session_id": session_id,
                    "created_at": agora,
                    "expires_at": expira,
                    "revoked_at": None,
                    "active": True,
                    "url": f"{str(request.base_url).rstrip('/')}/shared/{token}",
                    "token": token,
                }
            },
            status_code=201,
        )
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/shares")
async def list_shares(session_id: str, request: Request) -> JSONResponse:
    conn = _get_db(request.app)
    try:
        if SessionRepository(conn).get(session_id) is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
        agora = time.time()
        linhas = [
            _share_payload(row, agora=agora) for row in shares_repo.list_shares(conn, session_id)
        ]
        return {"session_id": session_id, "shares": linhas}
    finally:
        conn.close()


@app.delete("/api/sessions/{session_id}/shares/{share_id}")
async def revoke_share(session_id: str, share_id: int, request: Request) -> JSONResponse:
    """Revoga um link. Marca suave: a linha fica para auditoria de quem viu."""
    conn = _get_db(request.app)
    try:
        linha = shares_repo.get_share(conn, share_id)
        if linha is None or linha["session_id"] != session_id:
            return JSONResponse({"error": "share_not_found", "id": share_id}, status_code=404)
        with conn:
            revogado = shares_repo.revoke_share(conn, share_id)
        if not revogado:
            return JSONResponse({"error": "share_already_revoked", "id": share_id}, status_code=409)
        return {"status": "revoked", "id": share_id}
    finally:
        conn.close()


def _pagina_share_nao_encontrada() -> HTMLResponse:
    corpo = (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Link indisponível · Kairos</title></head><body>"
        "<main style='max-width:38rem;margin:3rem auto;padding:0 1rem;font-family:sans-serif'>"
        "<h1>Link indisponível</h1><p>Este link de conversa está expirado, foi revogado "
        "ou nunca existiu.</p></main></body></html>"
    )
    return HTMLResponse(
        content=corpo,
        status_code=404,
        headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"},
    )


def _pagina_de_share_html(sessao, mensagens, *, expira) -> str:
    titulo = sessao["display_name"] or sessao["title"] or sessao["id"]
    cabecalho = [html.escape(str(titulo))]
    if sessao["source"]:
        cabecalho.append(html.escape(f"origem: {sessao['source']}"))

    def msg(m) -> str:
        papel = str(m["role"])
        rotulo = {"user": "Você", "assistant": "Kairos"}.get(papel, papel)
        return (
            "<article style='margin:0 0 1.25rem'>"
            f"<h2 style='margin:0 0 .25rem;font-size:.8rem;letter-spacing:.04em;"
            f"text-transform:uppercase;color:#667085'>{html.escape(rotulo)}</h2>"
            f"<pre style='white-space:pre-wrap;word-wrap:break-word;margin:0;font:inherit'>"
            f"{html.escape(m['content'])}</pre></article>"
        )

    aviso = ""
    if expira is not None:
        quando = time.strftime("%d/%m/%Y %H:%M", time.localtime(expira))
        aviso = (
            f"<p style='color:#667085;font-size:.85rem'>Este link expira em "
            f"{html.escape(quando)} (fuso do servidor).</p>"
        )
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Kairos — conversa compartilhada</title></head><body>"
        "<main style='max-width:44rem;margin:2.5rem auto;padding:0 1rem;"
        "font-family:system-ui,-apple-system,sans-serif;line-height:1.55;color:#17202a'>"
        "<header style='border-bottom:1px solid #e4e7ec;padding-bottom:1rem;margin-bottom:1.5rem'>"
        f"<h1 style='margin:0 0 .25rem;font-size:1.4rem'>{cabecalho[0]}</h1>"
        f"<p style='margin:0;color:#667085;font-size:.85rem'>{html.escape(cabecalho[1] or '')}</p>"
        "</header>"
        + "".join(msg(m) for m in mensagens)
        + aviso
        + "<footer style='margin-top:2.5rem;padding-top:1rem;border-top:1px solid #e4e7ec;"
        "color:#98a2b3;font-size:.8rem'>Compartilhado via Kairos · leitura apenas</footer>"
        "</main></body></html>"
    )


@app.get("/shared/{share_token}")
async def shared_session_view(share_token: str, request: Request) -> Response:
    """Página pública de leitura de uma conversa compartilhada.

    Rota fora de ``/api``: um link de compartilhamento é, por natureza, acessível
    a quem não possui o token de sessão. A página sai sem scripts e sem assets —
    o conteúdo é renderizado e escapado no servidor (fail-closed), e o acesso é
    negado quando o link está revogado ou expirado.
    """
    conn = _get_db(request.app)
    try:
        linha = shares_repo.find_active_share(conn, share_token)
        if linha is None:
            return _pagina_share_nao_encontrada()
        sessao = SessionRepository(conn).get(linha["session_id"])
        if sessao is None:
            return _pagina_share_nao_encontrada()
        mensagens = conn.execute(
            "SELECT id, role, content, timestamp FROM messages "
            "WHERE session_id = ? AND role IN ('user','assistant') "
            "AND content IS NOT NULL AND content != '' ORDER BY timestamp, id",
            (linha["session_id"],),
        ).fetchall()
        corpo = _pagina_de_share_html(sessao, mensagens, expira=linha["expires_at"])
        return HTMLResponse(
            content=corpo,
            headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"},
        )
    finally:
        conn.close()


@app.post("/api/chat/sessions/{session_id}/tool-approvals/{approval_id}")
async def chat_tool_approval(
    session_id: str, approval_id: str, payload: dict[str, Any], request: Request
):
    """Resolve a confirmação por turno de uma ferramenta mutadora do Chat.

    Só existe enquanto o turno aguarda a decisão; depois de decidida ou
    expirada, responde 404 para que o SPA reaja a uma execução já consumida.
    """
    service = getattr(request.app.state, "interaction_service", None)
    decide = getattr(service, "decide_tool_approval", None)
    if decide is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    decision = payload.get("decision")
    if decision not in {"allow", "deny"}:
        return JSONResponse({"error": "approval_invalid_decision"}, status_code=400)
    try:
        decide(approval_id=approval_id, session_id=session_id, decision=decision)
    except InteractionServiceError as exc:
        status = {
            "approval_not_found": 404,
            "approval_session_mismatch": 403,
            "approval_already_decided": 409,
            "approval_invalid_decision": 400,
        }.get(exc.error_kind, 400)
        return JSONResponse({"error": exc.error_kind}, status_code=status)
    return JSONResponse({"status": "decided"})


@app.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    ticket_ok = _consume_ws_ticket(websocket.query_params.get("token"))
    header_ok = _token_ok(websocket.headers.get(TOKEN_HEADER))
    if not ticket_ok and not header_ok:
        # 4401 é o código que o SPA reconhece para recarregar e repegar o token.
        await websocket.close(code=4401)
        return
    await _chat_session(websocket)


@app.websocket("/ws/runtime")
async def websocket_runtime_endpoint(websocket: WebSocket):
    ticket_ok = _consume_ws_ticket(websocket.query_params.get("token"))
    header_ok = _token_ok(websocket.headers.get(TOKEN_HEADER))
    if not ticket_ok and not header_ok:
        await websocket.close(code=4401)
        return
    client = getattr(websocket.app.state, "runtime_client", None)
    if client is None:
        service = getattr(websocket.app.state, "interaction_service", None)
        client = getattr(service, "runtime_client", None)
    if client is None:
        await websocket.close(code=1012)
        return
    await runtime_websocket_session(websocket, client)


async def _serve_chat_messages(websocket: WebSocket) -> None:
    while True:
        raw_data = await websocket.receive_text()
        try:
            data = json.loads(raw_data)
        except Exception:  # noqa: BLE001, S112
            continue
        if not isinstance(data, Mapping):
            continue

        event_type = data.get("type", "message")
        if event_type == "ping":
            await websocket.send_text(json.dumps({"type": "pong"}))
            continue
        if event_type != "message":
            continue

        try:
            envelope = interaction_envelope_from_json(data)
        except (TypeError, ValueError):
            continue
        service = websocket.app.state.interaction_service
        async with aclosing(service.stream(envelope)) as stream:
            async for event in stream:
                if isinstance(event, RuntimeEvent):
                    payload = runtime_event_to_json(event)
                else:
                    payload = interaction_event_to_json(
                        event,
                        conversation_id=envelope.conversation_id,
                    )
                await websocket.send_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


async def _chat_session(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WebSocket chat client connected")

    try:
        await _serve_chat_messages(websocket)

    except WebSocketDisconnect:
        logger.info("WebSocket chat client disconnected")
    except InteractionServiceUnavailableError:
        try:
            await websocket.close(code=1012)
        except Exception:  # noqa: BLE001, S110 - socket pode já estar fechado
            pass
    except RuntimeErrorInfo as exc:
        safe = public_error(exc.code)
        try:
            await websocket.send_text(json.dumps({"error": safe}, ensure_ascii=False))
            await websocket.close(code=1012 if exc.code in {"unavailable", "transport"} else 1008)
        except Exception:  # noqa: BLE001, S110 - socket pode já estar fechado
            pass
    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:  # noqa: BLE001, S110 - socket pode já estar fechado
            pass


# --- FRONTEND DASHBOARD REST ENDPOINTS ---


class LoginRequest(BaseModel):
    token: str


@app.get("/api/auth/me")
async def get_auth_me(request: Request):
    """Quem está falando. NUNCA devolve o token.

    A versão anterior respondia `authenticated: True` fixo e incluía o token
    no corpo — quem alcançasse a rota levava a credencial junto.
    """
    return {
        "authenticated": _autenticado(request),
        "auth_required": True,
        "user": "kairos" if _autenticado(request) else None,
    }


@app.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    if not _token_ok(req.token.strip()):
        # Uma pausa curta encarece a tentativa em série sem punir quem erra
        # de verdade uma vez.
        await asyncio.sleep(0.4)
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    response.set_cookie(
        SESSION_COOKIE,
        SESSION_TOKEN,
        httponly=True,  # fora do alcance de qualquer script da página
        samesite="lax",  # não acompanha requisição vinda de outro site
        max_age=60 * 60 * 12,
        path="/",
    )
    return {"authenticated": True, "user": "kairos"}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    if _autenticado(request):
        _revoke_ws_tickets()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


@app.post("/api/auth/ws-ticket")
async def get_ws_ticket():
    return {"ticket": _issue_ws_ticket(), "expires_in": WS_TICKET_TTL_SECONDS}


@app.get("/api/model/options")
async def get_model_options():
    gateway = build_provider_gateway(_application_home(app))
    try:
        return {
            "models": [
                {
                    "id": model.ref.model,
                    "name": model.display_name,
                    "provider": model.ref.provider,
                    "context_window": model.capabilities.context_length or 128_000,
                    "supports_tools": model.capabilities.tools is True,
                    "supports_vision": model.capabilities.vision is True,
                }
                for model in gateway.catalog.list_models()
            ]
        }
    finally:
        await gateway.aclose()


def _skill_roots() -> list[tuple[str, Path]]:
    """Onde as skills vivem, na ordem em que se sobrepõem.

    `Path.cwd()` — o que este módulo usava — é o diretório de onde o processo
    foi lançado. Sob s6 isso é `/`, então a lista vinha sempre vazia e o menu
    Skills abria sem nada, com as skills a poucos diretórios dali.
    """
    home = Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))
    return [
        ("builtin", Path(__file__).resolve().parent.parent / "skills"),
        ("user", home / "skills"),
    ]


def _disabled_skills_file() -> Path:
    home = Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))
    return home / "skills-disabled.json"


def _disabled_skills() -> set[str]:
    f = _disabled_skills_file()
    if not f.exists():
        return set()
    try:
        return set(json.loads(f.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return set()


def _read_skill(path: Path) -> dict:
    """Metadados do frontmatter; o nome do diretório é só o fallback.

    A lista carrega o suficiente para filtrar, buscar e mostrar o detalhe sem
    um segundo request por skill — com 201 delas, uma chamada por cartão seria
    uma tempestade de requisições a cada troca de filtro.
    """
    dados = {
        "name": path.parent.name,
        "description": "",
        "version": "",
        "author": "",
        "license": "",
        "tags": [],
        "platforms": [],
        "related": [],
    }
    try:
        from kairos_skills.frontmatter import parse_frontmatter

        fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        dados.update(
            name=fm.name or dados["name"],
            description=fm.description or "",
            version=fm.version or "",
            author=fm.author or "",
            license=fm.license or "",
            tags=list(fm.tags or ()),
            platforms=list(fm.platforms or ()),
            related=list(fm.related_skills or ()),
        )
    except Exception as exc:  # noqa: BLE001 — uma skill malformada não derruba a lista
        logger.warning("skills: frontmatter ilegível em %s: %s", path, exc)
    return dados


@app.get("/api/skills")
async def list_skills():
    """Lista as skills das duas raízes, com a categoria vinda do caminho.

    A varredura é recursiva porque o catálogo é organizado por categoria
    (`skills/github/github-auth/`), e as skills se referem umas às outras por
    caminho completo — achatar quebraria essas referências.
    """
    desabilitadas = _disabled_skills()
    encontradas: dict[str, dict] = {}
    for origem, raiz in _skill_roots():
        if not raiz.is_dir():
            continue
        for md in sorted(raiz.rglob("SKILL.md")):
            rel = md.parent.relative_to(raiz)
            ident = rel.as_posix()
            info = _read_skill(md)
            # A do usuário sobrepõe a embarcada de mesmo identificador.
            encontradas[ident] = {
                **info,
                "id": ident,
                "category": rel.parts[0] if len(rel.parts) > 1 else "geral",
                "enabled": ident not in desabilitadas,
                "source": origem,
                "path": str(md),
            }
    skills = sorted(encontradas.values(), key=lambda s: (s["category"], s["name"]))
    categorias: dict[str, int] = {}
    for s in skills:
        categorias[s["category"]] = categorias.get(s["category"], 0) + 1
    return {
        "skills": skills,
        "categorias": [{"id": nome, "total": n} for nome, n in sorted(categorias.items())],
        "total": len(skills),
    }


def _skill_file(name: str, *, criar: bool = False) -> Path | None:
    """Resolve `name` para um SKILL.md, recusando o que escapa das raízes.

    `resolve()` antes da comparação é o que fecha a porta para `../..`: sem
    isso, o nome vindo da query controlaria qualquer caminho do disco.
    """
    for _, raiz in reversed(_skill_roots()):
        if not raiz.exists():
            continue
        md = (raiz / name / "SKILL.md").resolve()
        if raiz.resolve() not in md.parents:
            continue
        if md.exists() or criar:
            return md
    return None


@app.get("/api/skills/content")
async def skill_content(name: str):
    md = _skill_file(name)
    if md is None:
        return JSONResponse({"error": "skill_not_found", "name": name}, status_code=404)
    return {"name": name, "content": md.read_text(encoding="utf-8")}


class SkillContentRequest(BaseModel):
    name: str
    content: str


@app.put("/api/skills/content")
async def save_skill_content(req: SkillContentRequest):
    # Escrita vai para a raiz do usuário: as embarcadas vêm da imagem e um
    # redeploy as sobrescreveria sem avisar.
    home = Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))
    destino = (home / "skills" / req.name / "SKILL.md").resolve()
    if (home / "skills").resolve() not in destino.parents:
        return JSONResponse({"error": "invalid_name", "name": req.name}, status_code=400)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(req.content, encoding="utf-8")
    return {"name": req.name, "saved": True}


class SkillToggleRequest(BaseModel):
    name: str
    enabled: bool


# A SPA alterna com PUT; POST fica aceito para chamadas diretas e scripts.
@app.put("/api/skills/toggle")
@app.post("/api/skills/toggle")
async def toggle_skill(req: SkillToggleRequest):
    desabilitadas = _disabled_skills()
    if req.enabled:
        desabilitadas.discard(req.name)
    else:
        desabilitadas.add(req.name)
    f = _disabled_skills_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(desabilitadas)), encoding="utf-8")
    return {"name": req.name, "enabled": req.enabled}


@app.get("/api/env")
async def get_env_vars():
    return JSONResponse({"error": "retired", "replacement": "/api/providers"}, status_code=410)


# --- WEBSOCKET ALIASES ---


@app.websocket("/api/ws")
@app.websocket("/api/events")
@app.websocket("/api/pty")
async def websocket_alias_endpoint(websocket: WebSocket):
    await websocket_chat_endpoint(websocket)


# --- INTERFACE DO KAIROS ---

# A UI própria: HTML, CSS e módulos ES servidos como estão, sem passo de build.
UI_DIR = Path(__file__).parent / "ui"

if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icone = UI_DIR / "favicon.ico"
    if icone.exists():
        return FileResponse(icone)
    return JSONResponse({"error": "not_found"}, status_code=404)


if UI_DIR.exists():

    def _resposta_spa(full_path: str, raiz: Path):
        """Serve um arquivo da raiz, ou o index.

        A interface não recebe token no HTML: autentica e usa cookie httpOnly.
        """
        pedido = raiz / full_path
        if full_path and pedido.is_file():
            return FileResponse(pedido)
        index = raiz / "index.html"
        if not index.exists():
            return JSONResponse({"error": "index.html não encontrado"}, status_code=404)
        html = index.read_text(encoding="utf-8")
        return HTMLResponse(content=html, status_code=200)

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Uma rota /api/ que chegou até aqui NÃO existe. Devolver o index.html
        # com 200 — o que este handler fazia — entrega `<!doctype html>` a quem
        # chamou `res.json()`: a página quebra num SyntaxError de parse, sem
        # status de erro e sem nada nos logs do servidor. Era isto que fazia o
        # menu Skills abrir em branco. 404 em JSON transforma falha silenciosa
        # em erro legível dos dois lados.
        if full_path.startswith("api/") or full_path == "legacy" or full_path.startswith("legacy/"):
            return JSONResponse({"error": "not_found", "path": f"/{full_path}"}, status_code=404)
        return _resposta_spa(full_path, UI_DIR)
