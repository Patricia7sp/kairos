"""Servidor FastAPI e WebSocket para a Interface Web do Kairos."""

from __future__ import annotations

import json
import logging
import os
import secrets
from hmac import compare_digest
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kairos_cli.auth import AuthStore
from kairos_cli.config import load_config, save_config
from kairos_providers.manager import ProviderManager
from kairos_state.repositories.messages import MessageRepository
from kairos_state.repositories.sessions import SessionRepository

logger = logging.getLogger(__name__)

app = FastAPI(title="Kairos Web API", version="0.1.0")

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

DIST_DIR = Path(__file__).parent / "web_dist"

# --- SESSÃO ---

# Token gerado uma vez por processo do servidor. Fica só em memória: reiniciar
# o servidor invalida as abas antigas, que é o comportamento desejado para uma
# UI local. `KAIROS_WEB_TOKEN` existe para quem precisa de um valor estável
# (proxy reverso, teste de integração) e assume o risco conscientemente.
SESSION_TOKEN = os.environ.get("KAIROS_WEB_TOKEN") or secrets.token_urlsafe(32)

# O bundle do SPA já manda este header; o WebSocket manda `?token=`.
TOKEN_HEADER = "X-Kairos-Session-Token"  # noqa: S105 — nome de header, não o segredo

# `/api/health` fica aberto: é o que `kairos doctor` e o healthcheck do
# container sondam, e não devolve nada além de "estou de pé".
_OPEN_PATHS = frozenset({"/api/health"})


def _token_ok(supplied: str | None) -> bool:
    """Compara em tempo constante — `==` em segredo vaza por timing."""
    return bool(supplied) and compare_digest(supplied, SESSION_TOKEN)


@app.middleware("http")
async def require_session_token(request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in _OPEN_PATHS:
        supplied = request.headers.get(TOKEN_HEADER) or request.query_params.get("token")
        if not _token_ok(supplied):
            return JSONResponse({"error": "invalid_session_token"}, status_code=401)
    return await call_next(request)


def _get_auth_store() -> AuthStore:
    kairos_home = Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))
    auth_path = kairos_home / "auth.json"
    data: dict[str, Any] = {}
    if auth_path.exists():
        try:
            data = json.loads(auth_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001, S110
            pass
    return AuthStore(profile=data.get("credential_pool", {}))


# --- REST ENDPOINTS ---


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "app": "kairos",
        "version": "0.1.0",
        "web_dist_present": DIST_DIR.exists(),
    }


@app.get("/api/config")
async def get_config():
    config = load_config()
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
        save_config(req.config)
        return {"status": "saved", "config": req.config}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/models")
async def list_models():
    store = _get_auth_store()
    manager = ProviderManager(auth_store=store.profile)
    models = manager.list_all_models()
    config = load_config()
    default_model = config.get("model", "claude-3-7-sonnet-20250219")
    default_provider = config.get("provider", "anthropic")

    return {
        "default_model": default_model,
        "default_provider": default_provider,
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                "context_length": m.context_length,
                "supports_vision": m.supports_vision,
                "supports_tools": m.supports_tools,
            }
            for m in models
        ],
    }


class SetDefaultModelRequest(BaseModel):
    model: str
    provider: str | None = None
    task: str | None = None


@app.post("/api/models/set-default")
async def set_default_model(req: SetDefaultModelRequest):
    config = load_config()
    if req.task:
        aux = config.setdefault("auxiliary_models", {})
        aux[req.task] = req.model
    else:
        config["model"] = req.model
        if req.provider:
            config["provider"] = req.provider
    save_config(config)
    return {"status": "updated", "config": config}


@app.get("/api/providers")
async def get_providers_status():
    store = _get_auth_store()
    manager = ProviderManager(auth_store=store.profile)
    statuses = await manager.test_all_connections()

    result = []
    for prov_name, st in statuses.items():
        key = manager.get_api_key(prov_name)
        masked_key = (
            f"{key[:7]}...{key[-4:]}"
            if (key and len(key) > 12)
            else ("Configurada" if key else None)
        )
        result.append(
            {
                "provider": prov_name,
                "connected": st.ok,
                "message": st.message,
                "models_count": st.models_found,
                "auth_method": st.auth_method,
                "has_key": bool(key),
                "masked_key": masked_key,
            }
        )
    return {"providers": result}


class SaveKeyRequest(BaseModel):
    provider: str
    api_key: str


@app.post("/api/providers/save-key")
async def save_provider_key(req: SaveKeyRequest):
    kairos_home = Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))
    kairos_home.mkdir(parents=True, exist_ok=True)
    auth_path = kairos_home / "auth.json"

    store = _get_auth_store()
    store.profile[req.provider] = [{"api_key": req.api_key.strip()}]
    store.write_atomically(auth_path)

    # Testa nova conexão
    manager = ProviderManager(auth_store=store.profile)
    provider_inst = manager.get_provider(req.provider)
    status = await provider_inst.test_connection()

    return {
        "status": "saved",
        "provider": req.provider,
        "connection": {
            "ok": status.ok,
            "message": status.message,
            "models_count": status.models_found,
        },
    }


def _get_db():
    from kairos_state import connect, default_db_path, initialize_schema

    conn = connect(default_db_path())
    initialize_schema(conn)
    return conn


# --- SESSIONS ENDPOINTS ---


@app.get("/api/sessions")
async def list_sessions():
    conn = _get_db()
    try:
        repo = SessionRepository(conn)
        sessions = repo.list_recent(limit=50)
        return {
            "sessions": [
                {
                    "id": s["id"],
                    "source": s["source"],
                    "started_at": s["started_at"],
                    "ended_at": s["ended_at"],
                    "end_reason": s["end_reason"],
                }
                for s in sessions
            ]
        }
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp, id",
            (session_id,),
        ).fetchall()
        return {
            "session_id": session_id,
            "messages": [
                {
                    "id": r["id"],
                    "role": r["role"],
                    "content": r["content"],
                    "created_at": r["timestamp"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# --- WEBSOCKET CHAT STREAMING ---


@app.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    supplied = websocket.query_params.get("token") or websocket.headers.get(TOKEN_HEADER)
    if not _token_ok(supplied):
        # 4401 é o código que o SPA reconhece para recarregar e repegar o token.
        await websocket.close(code=4401)
        return
    await _chat_session(websocket)


async def _chat_session(websocket: WebSocket) -> None:  # noqa: PLR0915
    await websocket.accept()
    logger.info("WebSocket chat client connected")

    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
            except Exception:  # noqa: BLE001, S112
                continue

            event_type = data.get("type", "message")
            if event_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if event_type == "message":
                user_text = data.get("content", "").strip()
                session_id = data.get("session_id") or "web-default"
                requested_model = data.get("model")
                requested_provider = data.get("provider")

                # Resolve provedor e modelo
                config = load_config()
                provider_name = requested_provider or config.get("provider", "anthropic")
                model_name = requested_model or config.get("model")

                store = _get_auth_store()
                manager = ProviderManager(auth_store=store.profile)
                provider = manager.get_provider(provider_name, model=model_name)

                # Persiste mensagem do usuário no state.db
                conn = _get_db()
                try:
                    session_repo = SessionRepository(conn)
                    msg_repo = MessageRepository(conn)
                    session_repo.ensure(session_id, source="web")
                    msg_repo.append(session_id=session_id, role="user", content=user_text)

                    # Monta contexto de mensagens ativas
                    active_msgs = msg_repo.for_api(session_id)
                    formatted_msgs = [
                        {"role": m["role"], "content": m["payload"]} for m in active_msgs
                    ]
                finally:
                    conn.close()

                # Notifica início do turno
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "turn_start",
                            "session_id": session_id,
                            "model": model_name or provider_name,
                            "provider": provider_name,
                        }
                    )
                )

                # Executa streaming
                full_reply = ""
                try:
                    async for chunk in provider.stream_chat(formatted_msgs, model=model_name):
                        if chunk.delta_text:
                            full_reply += chunk.delta_text
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "delta",
                                        "text": chunk.delta_text,
                                    }
                                )
                            )
                        if chunk.delta_tool_calls:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "tool_call",
                                        "tool_calls": chunk.delta_tool_calls,
                                    }
                                )
                            )
                except Exception as stream_err:  # noqa: BLE001
                    err_msg = f"\n[Erro durante streaming com {provider_name}: {stream_err}]"
                    full_reply += err_msg
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "delta",
                                "text": err_msg,
                            }
                        )
                    )

                # Salva resposta no SQLite
                conn = _get_db()
                try:
                    msg_repo = MessageRepository(conn)
                    session_repo = SessionRepository(conn)
                    msg_repo.append(session_id=session_id, role="assistant", content=full_reply)
                    session_repo.increment_turn(session_id)
                finally:
                    conn.close()

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "turn_end",
                            "session_id": session_id,
                        }
                    )
                )

    except WebSocketDisconnect:
        logger.info("WebSocket chat client disconnected")
    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)


# --- FRONTEND DASHBOARD REST ENDPOINTS ---


@app.get("/api/status")
async def get_status():
    config = load_config()
    return {
        "status": "healthy",
        "gateway": "running",
        "version": "0.1.0",
        "app": "kairos",
        "authenticated": True,
        "active_profile": "default",
        "model": config.get("model", "claude-3-7-sonnet-20250219"),
        "provider": config.get("provider", "anthropic"),
    }


@app.get("/api/auth/me")
async def get_auth_me():
    return {
        "authenticated": True,
        "auth_required": False,
        "user": "kairos-user",
        "token": SESSION_TOKEN,
        "role": "admin",
    }


@app.post("/api/auth/ws-ticket")
async def get_ws_ticket():
    return {"ticket": SESSION_TOKEN, "expires_in": 86400}


@app.get("/api/profiles")
async def list_profiles():
    return {
        "profiles": [
            {
                "name": "default",
                "description": "Perfil Principal do Kairos",
                "is_active": True,
            }
        ]
    }


@app.get("/api/profiles/active")
async def get_active_profile():
    return {"name": "default", "description": "Perfil Principal"}


@app.get("/api/model/info")
async def get_model_info():
    config = load_config()
    model = config.get("model", "claude-3-7-sonnet-20250219")
    provider = config.get("provider", "anthropic")
    return {
        "model": model,
        "provider": provider,
        "capabilities": {
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
            "supports_reasoning": True,
        },
    }


@app.get("/api/model/options")
async def get_model_options():
    store = _get_auth_store()
    manager = ProviderManager(auth_store=store.profile)
    models = manager.list_all_models()
    return {
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                # A chave da resposta é o que a SPA lê; o atributo do descritor
                # é `context_length`. Ler `m.context_window` levantava
                # AttributeError e a rota inteira devolvia 500.
                "context_window": m.context_length,
                "supports_tools": m.supports_tools,
                "supports_vision": m.supports_vision,
            }
            for m in models
        ]
    }


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
    """Nome e descrição saem do frontmatter; o diretório é só o fallback."""
    name, description = path.parent.name, ""
    try:
        from kairos_skills.frontmatter import parse_frontmatter

        fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        name = fm.name or name
        description = fm.description or ""
    except Exception as exc:  # noqa: BLE001 — uma skill malformada não derruba a lista
        logger.warning("skills: frontmatter ilegível em %s: %s", path, exc)
    return {"name": name, "description": description}


@app.get("/api/skills")
async def list_skills():
    desabilitadas = _disabled_skills()
    encontradas: dict[str, dict] = {}
    for origem, raiz in _skill_roots():
        if not raiz.is_dir():
            continue
        for d in sorted(raiz.iterdir()):
            md = d / "SKILL.md"
            if not (d.is_dir() and md.exists()):
                continue
            info = _read_skill(md)
            # A do usuário sobrepõe a embarcada de mesmo nome.
            encontradas[d.name] = {
                **info,
                "id": d.name,
                "enabled": d.name not in desabilitadas,
                "source": origem,
                "path": str(md),
            }
    return {"skills": list(encontradas.values())}


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


@app.get("/api/tools/toolsets")
async def list_toolsets():
    return {
        "toolsets": [
            {
                "name": "core",
                "enabled": True,
                "tools": ["bash", "read_file", "write_file", "edit_file", "list_dir", "web_search"],
            }
        ]
    }


@app.get("/api/env")
async def get_env_vars():
    return {"env": {}}


@app.get("/api/cron/jobs")
async def get_cron_jobs():
    return {"jobs": []}


@app.get("/api/analytics/usage")
async def get_analytics_usage():
    return {"daily": [], "totals": {"requests": 0, "tokens": 0}}


@app.get("/api/logs")
async def get_logs():
    return {"logs": []}


# --- WEBSOCKET ALIASES ---


@app.websocket("/api/ws")
@app.websocket("/api/events")
@app.websocket("/api/pty")
async def websocket_alias_endpoint(websocket: WebSocket):
    await websocket_chat_endpoint(websocket)


# --- SPA STATIC FILES ---

if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")
    if (DIST_DIR / "fonts").exists():
        app.mount("/fonts", StaticFiles(directory=str(DIST_DIR / "fonts")), name="fonts")
    if (DIST_DIR / "fonts-terminal").exists():
        app.mount(
            "/fonts-terminal",
            StaticFiles(directory=str(DIST_DIR / "fonts-terminal")),
            name="fonts-terminal",
        )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Uma rota /api/ que chegou até aqui NÃO existe. Devolver o index.html
        # com 200 — o que este handler fazia — entrega `<!doctype html>` a quem
        # chamou `res.json()`: a página quebra num SyntaxError de parse, sem
        # status de erro e sem nada nos logs do servidor. Era isto que fazia o
        # menu Skills abrir em branco. 404 em JSON transforma falha silenciosa
        # em erro legível dos dois lados.
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not_found", "path": f"/{full_path}"}, status_code=404)
        requested = DIST_DIR / full_path
        if requested.is_file() and full_path != "":
            return FileResponse(requested)
        index_file = DIST_DIR / "index.html"
        if index_file.exists():
            html_content = index_file.read_text(encoding="utf-8")
            injected_script = (
                "<script>"
                f'window.__KAIROS_SESSION_TOKEN__="{SESSION_TOKEN}";'
                "window.__KAIROS_AUTH_REQUIRED__=false;"
                "</script>"
            )
            if "</head>" in html_content:
                html_content = html_content.replace("</head>", f"{injected_script}</head>")
            return HTMLResponse(content=html_content, status_code=200)
        return JSONResponse({"error": "web_dist index.html not found"}, status_code=404)
