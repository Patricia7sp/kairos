"""Servidor FastAPI e WebSocket para a Interface Web do Kairos."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from hmac import compare_digest
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
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

# O bundle do SPA já manda este header; o WebSocket manda `?token=`.
TOKEN_HEADER = "X-Kairos-Session-Token"  # noqa: S105 — nome de header, não o segredo

# Cookie de sessão. Guarda o mesmo token, mas `httpOnly`: assim o JavaScript
# da página não consegue lê-lo, e o token deixa de viajar dentro do HTML.
# Antes ele era injetado em toda resposta da raiz — quem alcançasse a porta
# obtinha a credencial só de carregar a página, sem autenticar-se.
SESSION_COOKIE = "kairos_session"

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


def _linha_sessao(s) -> dict:
    """A tabela já guarda contagens e custo — devolvê-los evita que a interface
    peça as mensagens de cada sessão só para saber quantas são."""
    chaves = s.keys()

    def campo(nome, padrao=None):
        return s[nome] if nome in chaves else padrao

    return {
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
    }


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
        "SELECT st.tag, COUNT(*) AS n FROM session_tags st "
        "JOIN sessions s ON s.id = st.session_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY st.tag ORDER BY st.tag"
    rows = conn.execute(sql, args).fetchall()
    return [{"tag": row["tag"], "count": row["n"]} for row in rows]


@app.get("/api/sessions")
async def list_sessions(
    limit: int = 50, offset: int = 0, q: str = "", status: str = "todas", tag: str = ""
):
    conn = _get_db()
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
        sql = "SELECT s.* FROM sessions s"
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
async def get_session(session_id: str):
    conn = _get_db()
    try:
        s = SessionRepository(conn).get(session_id)
        if s is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
        linha = _linha_sessao(s)
        linha["message_count"] = _contagem_de_mensagens(conn, [session_id]).get(
            session_id, linha["message_count"]
        )
        linha["tags"] = _tags_para_sessoes(conn, [session_id]).get(session_id, [])
        return linha
    finally:
        conn.close()


@app.patch("/api/sessions/{session_id}")
async def update_session(session_id: str, payload: dict[str, Any]):
    """Atualiza somente metadados de organização; o transcript é imutável aqui."""
    permitidos = {"archived", "pinned", "hidden", "tags"}
    desconhecidos = set(payload) - permitidos
    if desconhecidos or not payload:
        return JSONResponse(
            {"error": "invalid_session_update", "allowed": sorted(permitidos)},
            status_code=400,
        )
    flags = {k: v for k, v in payload.items() if k != "tags"}
    if any(not isinstance(v, bool) for v in flags.values()):
        return JSONResponse({"error": "session_flags_must_be_boolean"}, status_code=400)
    tags_payload = payload.get("tags")
    tags: list[str] | None = None
    if tags_payload is not None:
        if not isinstance(tags_payload, list) or any(not isinstance(v, str) for v in tags_payload):
            return JSONResponse({"error": "session_tags_must_be_a_list_of_strings"}, status_code=400)
        tags = sorted({v.strip().lower() for v in tags_payload if v.strip()})
        if len(tags) > 20 or any(len(v) > 32 for v in tags):
            return JSONResponse({"error": "session_tags_limit_exceeded"}, status_code=400)

    conn = _get_db()
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
            if tags is not None:
                conn.execute("DELETE FROM session_tags WHERE session_id = ?", (session_id,))
                conn.executemany(
                    "INSERT INTO session_tags(session_id, tag) VALUES (?, ?)",
                    [(session_id, value) for value in tags],
                )
        linha = SessionRepository(conn).get(session_id)
        linha_dict = _linha_sessao(linha)
        linha_dict["tags"] = _tags_para_sessoes(conn, [session_id]).get(session_id, [])
        return linha_dict
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    conn = _get_db()
    try:
        if SessionRepository(conn).get(session_id) is None:
            return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
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
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


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


# --- INTERFACE DO KAIROS ---

# A UI própria: HTML, CSS e módulos ES servidos como estão, sem passo de build.
# O `web_dist` herdado continua montado sob /legacy enquanto chat e terminal
# (que dependem dos websockets e ainda não têm equivalente aqui) não migram —
# a ponte é explícita e tem prazo, não é o rosto do produto.
UI_DIR = Path(__file__).parent / "ui"

if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icone = UI_DIR / "favicon.ico"
    if icone.exists():
        return FileResponse(icone)
    return JSONResponse({"error": "not_found"}, status_code=404)


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

    def _resposta_spa(full_path: str, raiz: Path, *, injetar_token: bool = False):
        """Serve um arquivo da raiz, ou o index.

        `injetar_token` existe só para a interface herdada, que lê o token de
        `window` e não sabe usar cookie. A interface do Kairos NÃO recebe o
        token no HTML: ela autentica e passa a usar o cookie httpOnly, e é isso
        que impede que carregar a página já entregue a credencial.
        """
        pedido = raiz / full_path
        if full_path and pedido.is_file():
            return FileResponse(pedido)
        index = raiz / "index.html"
        if not index.exists():
            return JSONResponse({"error": "index.html não encontrado"}, status_code=404)
        html = index.read_text(encoding="utf-8")
        if injetar_token:
            script = (
                "<script>"
                f'window.__KAIROS_SESSION_TOKEN__="{SESSION_TOKEN}";'
                "window.__KAIROS_AUTH_REQUIRED__=false;"
                "</script>"
            )
            if "</head>" in html:
                html = html.replace("</head>", f"{script}</head>")
        return HTMLResponse(content=html, status_code=200)

    @app.get("/legacy/{full_path:path}", include_in_schema=False)
    async def serve_legacy(full_path: str):
        """Interface herdada, mantida só enquanto chat e terminal não migram."""
        return _resposta_spa(full_path, DIST_DIR, injetar_token=True)

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
        # A interface do Kairos é a da raiz; o dist herdado só responde em /legacy.
        return _resposta_spa(full_path, UI_DIR if UI_DIR.exists() else DIST_DIR)
