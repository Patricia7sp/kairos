"""Servidor MCP `stdio` em stdlib — a frente `kairos mcp serve`.

Espelha o transporte que `kairos_mcp/runtime.py` consome como cliente:
JSON-RPC 2.0, uma mensagem por linha UTF-8, versão de protocolo
`2024-11-05`. Sem o pacote `mcp` (D-MCP.11): o servidor é o caminho inverso
do cliente, mesma família, mesma leveza — e o pacote continua opcional na
imagem.

Fail-closed, como o restante do projeto:

- Erro de ferramenta vira `result.isError` honesto no `tools/call` — nunca
  sucesso inventado. `messages_send` só reporta `delivered` quando o adapter
  confirma; plataforma não entregável é recusa nomeada.
- Método desconhecido responde `-32601`; `params` fora do esperado, `-32602`;
  linha fora do JSON-RPC, `-32700`/`-32600`. O servidor não cai de um
  transporte mal educado — ele responde e segue.
- `attachments_list` não é publicada (nada no Kairos persiste anexo); as
  `permissions_*` seguem não publicadas. Ver `SERVER_TOOLS`/`UNPUBLISHED_TOOLS`.

Baseline do `EventBridge` é tomado no start: sessão que já existia não
despeja histórico, sessão nova emite desde a primeira mensagem (o caso que
um baseline por "marca de tempo de início" perderia).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kairos_mcp.server import EventBridge
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.ledger import LedgerRepository
from kairos_state.repositories.search import SearchIndex
from kairos_state.repositories.sessions import SessionRepository

logger = logging.getLogger(__name__)

__all__ = ["MCPServer", "ToolError", "serve_stdio", "tool_catalog"]

#: Versão de protocolo que anunciamos no handshake — a mesma do cliente.
MCP_PROTOCOL_VERSION = "2024-11-05"

_SERVER_NAME = "kairos"
_SERVER_VERSION = "0.1.0"

#: Corte para o corpo exposto ao cliente, como no legado (content[:2000]).
_MAX_CONTENT_CHARS = 2_000
_MAX_LIMIT = 200


class ToolError(RuntimeError):
    """Falha de ferramenta nomeada — sai como `isError` honesto no result."""


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ToolError(f"'{label}' deve ser um inteiro entre {minimum} e {maximum}")
    try:
        ival = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{label}' deve ser um inteiro entre {minimum} e {maximum}") from None
    if not minimum <= ival <= maximum:
        raise ToolError(f"'{label}' deve estar entre {minimum} e {maximum}")
    return ival


def _require_text(params: dict[str, Any], key: str, *, label: str | None = None) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"'{label or key}' é obrigatório")
    return value.strip()


def _opt_text(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _opt_platform(params: dict[str, Any]) -> str | None:
    plataforma = _opt_text(params, "platform")
    return plataforma.lower() if plataforma else None


def _load_json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _origin_platform(origin: dict[str, Any], chat_type: Any) -> str:
    origem = origin.get("platform")
    if isinstance(origem, str) and origem.strip():
        return origem
    return str(chat_type) if chat_type else ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: str


def tool_catalog() -> tuple[ToolSpec, ...]:
    """As ferramentas publicadas, derivado de `SERVER_TOOLS`.

    Centraliza descrição e schema no servidor; a ordem declara a catálogo.
    Ferramentas não publicadas (`UNPUBLISHED_TOOLS`) nunca entram aqui.
    """
    return _TOOL_CATALOG


def _schema(*, required: tuple[str, ...] = (), properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


def _int_schema(label: str) -> dict[str, str]:
    return {"type": "integer", "description": label}


def _str_schema(label: str) -> dict[str, str]:
    return {"type": "string", "description": label}


#: Ordem e forma da superfície MCP. Os handlers são métodos de ``MCPServer``
#: com a mesma assinatura ``(self, params: dict[str, Any]) -> dict``.
_TOOL_CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec(
        "conversations_list",
        "Lista as conversas do agente (sessões não ocultas), com busca textual e "
        "filtro por plataforma quando a sessão registrou origem.",
        _schema(
            properties={
                "platform": _str_schema("plataforma de origem (lida de origin_json/chat_type)"),
                "limit": _int_schema("máximo de conversas (1–200, padrão 50)"),
                "search": _str_schema("filtro em título, id, fonte, modelo ou mensagem"),
            }
        ),
        "tool_conversations_list",
    ),
    ToolSpec(
        "conversation_read",
        "Lê os últimos usuário/assistente de uma conversa, em ordem cronológica.",
        _schema(
            required=("session_key",),
            properties={
                "session_key": _str_schema("chave da conversa, de conversations_list"),
                "limit": _int_schema("máximo de mensagens (1–200, padrão 50)"),
            },
        ),
        "tool_conversation_read",
    ),
    ToolSpec(
        "session_info",
        "Detalhes de uma conversa: seleção de modelo, contagens, custo, tags.",
        _schema(
            required=("session_key",),
            properties={"session_key": _str_schema("chave da conversa, de conversations_list")},
        ),
        "tool_session_info",
    ),
    ToolSpec(
        "conversation_search",
        "Busca textual nas mensagens (FTS5 → trigram → CJK → varredura).",
        _schema(
            required=("term",),
            properties={
                "term": _str_schema("termo de busca"),
                "limit": _int_schema("máximo de ocorrências (1–200, padrão 50)"),
            },
        ),
        "tool_conversation_search",
    ),
    ToolSpec(
        "events_poll",
        "Novos eventos de mensagem desde um cursor; sessão nova emite desde a "
        "primeira mensagem, histórico pré-start fica no baseline.",
        _schema(
            properties={
                "after_cursor": _int_schema("devolva o next_cursor das chamadas anteriores"),
                "session_key": _str_schema("filtra a uma conversa"),
                "limit": _int_schema("máximo de eventos (1–200, padrão 20)"),
            }
        ),
        "tool_events_poll",
    ),
    ToolSpec(
        "messages_send",
        "Envia uma mensagem a plataforma:destino agora, gravando a obrigação no "
        "ledger durável antes (sucesso só com confirmação do adapter).",
        _schema(
            required=("target", "message"),
            properties={
                "target": _str_schema('destino no formato "plataforma:identificador"'),
                "message": _str_schema("texto a enviar"),
            },
        ),
        "tool_messages_send",
    ),
    ToolSpec(
        "platforms_list",
        "Estado das plataformas de mensageria: entregáveis, obrigações pendentes "
        "e configuração, sem nenhum segredo.",
        _schema(properties={}),
        "tool_platforms_list",
    ),
)


class MCPServer:
    """Servidor stdio sobre a `state.db` do home em questão.

    Um `MCPServer` atende uma sessão (um processo `kairos mcp serve`): abre
    e migra o banco no start, toma o baseline do `EventBridge` e encerra a
    conexão no `close`.
    """

    def __init__(self, home: Path) -> None:
        self._home = Path(home)
        self._db = connect(self._home / "state.db")
        migrate(self._db)
        self._sessions = SessionRepository(self._db)
        self._search = SearchIndex(self._db)
        self._events = EventBridge()
        self._events.baseline_existing(self._baseline_sessions())

    # -- baseline ----------------------------------------------------------

    def _baseline_sessions(self) -> dict[str, int]:
        """sessão → maior id de mensagem no momento do start.

        Depois do start, `EventBridge` decide o que é novidade: sessão fora do
        baseline é nova e emite desde a primeira mensagem; sessão conhecida só
        emite o que passou do máximo de baseline — o histórico pré-start de
        uma conversa viva não vira torrente de eventos.
        """
        rows = self._db.execute(
            "SELECT session_id, MAX(id) AS max_id FROM messages GROUP BY session_id"
        ).fetchall()
        return {row["session_id"]: int(row["max_id"]) for row in rows}

    def close(self) -> None:
        self._db.close()

    # -- protocolo JSON-RPC -------------------------------------------------

    def dispatch(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Responta a uma mensagem; ``None`` = notificação, sem resposta."""
        method = msg.get("method")
        if not isinstance(method, str):
            return _error(None, -32600, "Invalid Request: mensagem sem 'method' textual")
        if "id" not in msg:
            return None
        rid = msg.get("id")
        if method == "initialize":
            return _result(
                rid,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
                },
            )
        if method == "ping":
            return _result(rid, {})
        if method == "tools/list":
            return _result(
                rid,
                {
                    "tools": [
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "inputSchema": spec.input_schema,
                        }
                        for spec in _TOOL_CATALOG
                    ]
                },
            )
        if method == "tools/call":
            return self._call_tool(rid, msg.get("params"))
        return _error(rid, -32601, "Method not found")

    def _call_tool(self, rid: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return _error(rid, -32602, "Invalid params: 'tools/call' exige objeto de params")
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return _result(rid, _tool_error("tools/call sem 'name' textual"))
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _result(rid, _tool_error("'arguments' deve ser um objeto"))
        specs = {spec.name: spec for spec in _TOOL_CATALOG}
        spec = specs.get(name.strip())
        if spec is None:
            return _result(rid, _tool_error(f"ferramenta desconhecida: {name}"))
        try:
            payload = getattr(self, spec.handler)(arguments)
        except ToolError as exc:
            return _result(rid, _tool_error(str(exc)))
        except Exception as exc:  # servidor segue vivo; erro honesto ao cliente
            logger.exception("tools/call falhou para %s", name)
            return _result(rid, _tool_error(f"{name} falhou: {type(exc).__name__}: {exc}"))
        return _result(
            rid,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    }
                ],
                "isError": False,
            },
        )

    # -- ferramentas ---------------------------------------------------------

    def _active_message_counts(self, ids: list[str]) -> dict[str, int]:
        """Mensagens ativas por sessão, da fonte.

        `sessions.message_count` existe na tabela, mas nenhum caminho de
        escrita o incrementa — contar da fonte é a verdade; o mesmo raciocínio
        da tela web (`_contagem_de_mensagens`).
        """
        if not ids:
            return {}
        lugares = ", ".join("?" for _ in ids)
        rows = self._db.execute(
            f"SELECT session_id, COUNT(*) AS n FROM messages "  # noqa: S608 — apenas placeholders interpolados
            f"WHERE active = 1 AND session_id IN ({lugares}) GROUP BY session_id",
            ids,
        ).fetchall()
        return {row["session_id"]: int(row["n"]) for row in rows}

    def tool_conversations_list(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = _coerce_int(
            params.get("limit"), default=50, minimum=1, maximum=_MAX_LIMIT, label="limit"
        )
        plataforma = _opt_platform(params)
        termo = _opt_text(params, "search")

        where = ["s.hidden = 0"]
        args: list[Any] = []
        if termo:
            like = f"%{termo}%"
            where.append(
                "(s.id LIKE ? OR COALESCE(NULLIF(s.display_name, ''), s.title, '') LIKE ? "
                "OR COALESCE(s.source, '') LIKE ? OR COALESCE(s.model, '') LIKE ? "
                "OR EXISTS (SELECT 1 FROM messages mq WHERE mq.session_id = s.id "
                "AND mq.content LIKE ?))"
            )
            args.extend([like] * 5)
        sql = (
            "SELECT * FROM sessions s WHERE "  # noqa: S608 - predicados dos ramos constantes acima; valores parametrizados
            + " AND ".join(where)
            + " ORDER BY COALESCE(s.last_activity_at, s.started_at) DESC LIMIT ?"
        )
        rows = self._db.execute(sql, (*args, limit)).fetchall()

        conversas: list[dict[str, Any]] = []
        for row in rows:
            origin = _load_json_object(row["origin_json"])
            origem = _origin_platform(origin, row["chat_type"])
            if plataforma and origem.lower() != plataforma:
                continue
            conversas.append(
                {
                    "session_key": row["id"],
                    "session_id": row["id"],
                    "platform": origem or None,
                    "chat_type": row["chat_type"] or None,
                    "chat_name": origin.get("chat_name") if origin else None,
                    "user_name": origin.get("user_name") if origin else None,
                    "display_name": row["display_name"] or row["title"] or "",
                    "source": row["source"],
                    "model": row["model"] or "",
                    "status": "encerrada" if row["ended_at"] else "aberta",
                    "message_count": row["message_count"] or 0,
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "last_activity_at": row["last_activity_at"] or row["started_at"],
                }
            )
        conversas.sort(key=lambda c: c["last_activity_at"] or 0.0, reverse=True)
        conversas = conversas[:limit]
        contagens = self._active_message_counts([c["session_id"] for c in conversas])
        for conversa in conversas:
            conversa["message_count"] = contagens.get(
                conversa["session_id"], conversa["message_count"]
            )
        return {"count": len(conversas), "conversations": conversas}

    def tool_conversation_read(self, params: dict[str, Any]) -> dict[str, Any]:
        chave = _require_text(params, "session_key")
        sessao = self._sessions.get(chave)
        if sessao is None:
            raise ToolError(f"Conversa não encontrada: {chave}")
        limit = _coerce_int(
            params.get("limit"), default=50, minimum=1, maximum=_MAX_LIMIT, label="limit"
        )
        rows = self._db.execute(
            "SELECT id, role, content, timestamp FROM messages "
            "WHERE session_id = ? AND active = 1 AND role IN ('user', 'assistant') "
            "AND COALESCE(content, '') <> '' ORDER BY timestamp, id",
            (chave,),
        ).fetchall()
        mensagens: list[dict[str, Any]] = []
        for row in rows:
            texto = (row["content"] or "")[:_MAX_CONTENT_CHARS]
            if not texto.strip():
                continue
            mensagens.append(
                {
                    "id": int(row["id"]),
                    "role": row["role"],
                    "content": texto,
                    "timestamp": row["timestamp"],
                }
            )
        total = len(mensagens)
        return {
            "session_key": chave,
            "count": min(total, limit),
            "total_in_session": total,
            "messages": mensagens[-limit:],
        }

    def tool_session_info(self, params: dict[str, Any]) -> dict[str, Any]:
        chave = _require_text(params, "session_key", label="session_key")
        row = self._sessions.get(chave)
        if row is None:
            raise ToolError(f"Conversa não encontrada: {chave}")
        selecao = self._sessions.selection(chave)
        origin = _load_json_object(row["origin_json"])
        tags = [
            t["tag"]
            for t in self._db.execute(
                "SELECT tag FROM session_tags WHERE session_id = ?", (chave,)
            ).fetchall()
        ]
        contagem = self._active_message_counts([chave]).get(chave, row["message_count"] or 0)
        info: dict[str, Any] = {
            "session_key": chave,
            "id": chave,
            "source": row["source"],
            "user_id": row["user_id"],
            "display_name": row["display_name"] or row["title"] or "",
            "title": row["title"] or "",
            "model": row["model"] or "",
            "platform": _origin_platform(origin, row["chat_type"]) or None,
            "chat_id": row["chat_id"],
            "chat_type": row["chat_type"],
            "thread_id": row["thread_id"],
            "session_route_key": row["session_key"],
            "status": "encerrada" if row["ended_at"] else "aberta",
            "archived": bool(row["archived"]),
            "pinned": bool(row["pinned"]),
            "hidden": bool(row["hidden"]),
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "end_reason": row["end_reason"],
            "last_activity_at": row["last_activity_at"],
            "message_count": contagem,
            "tool_call_count": row["tool_call_count"] or 0,
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cache_read_tokens": row["cache_read_tokens"] or 0,
            "cache_write_tokens": row["cache_write_tokens"] or 0,
            "estimated_cost_usd": row["estimated_cost_usd"],
            "actual_cost_usd": row["actual_cost_usd"],
            "tags": tags,
        }
        if selecao is not None:
            info["model_selection"] = {
                "provider": selecao.ref.provider,
                "model": selecao.ref.model,
                "reason": selecao.reason.value,
                "parameters": selecao.parameters,
                **({"profile": selecao.profile} if selecao.profile else {}),
            }
        if origin:
            info["origin"] = origin
        return {"session": info}

    def tool_conversation_search(self, params: dict[str, Any]) -> dict[str, Any]:
        termo = _require_text(params, "term", label="term")
        limit = _coerce_int(
            params.get("limit"), default=50, minimum=1, maximum=_MAX_LIMIT, label="limit"
        )
        rows = self._search.search(termo, limit=limit)
        resultados: list[dict[str, Any]] = []
        for row in rows:
            meta = self._db.execute(
                "SELECT role, timestamp FROM messages WHERE id = ?", (int(row["id"]),)
            ).fetchone()
            resultados.append(
                {
                    "session_id": row["session_id"],
                    "message_id": int(row["id"]),
                    "role": meta["role"] if meta is not None else "",
                    "content": (row["content"] or "")[:_MAX_CONTENT_CHARS],
                    "timestamp": meta["timestamp"] if meta is not None else None,
                }
            )
        return {"query": termo, "count": len(resultados), "results": resultados}

    def tool_events_poll(self, params: dict[str, Any]) -> dict[str, Any]:
        after = _coerce_int(
            params.get("after_cursor"), default=0, minimum=0, maximum=10**18, label="after_cursor"
        )
        limit = _coerce_int(
            params.get("limit"), default=20, minimum=1, maximum=_MAX_LIMIT, label="limit"
        )
        chave = _opt_text(params, "session_key")
        if chave is not None and self._sessions.get(chave) is None:
            raise ToolError(f"Conversa não encontrada: {chave}")

        if chave is not None:
            rows = self._db.execute(
                "SELECT id, session_id, role, content, timestamp FROM messages "
                "WHERE session_id = ? AND id > ? AND active = 1 ORDER BY id LIMIT ?",
                (chave, after, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id, session_id, role, content, timestamp FROM messages "
                "WHERE id > ? AND active = 1 ORDER BY id LIMIT ?",
                (after, limit),
            ).fetchall()

        eventos: list[dict[str, Any]] = []
        last = after
        for row in rows:
            message_id = int(row["id"])
            if not self._events.should_emit(row["session_id"], message_id):
                continue
            self._events.note_emitted(row["session_id"], message_id)
            last = message_id
            eventos.append(
                {
                    "type": "message",
                    "session_key": row["session_id"],
                    "session_id": row["session_id"],
                    "message_id": message_id,
                    "role": row["role"],
                    "content": (row["content"] or "")[:_MAX_CONTENT_CHARS],
                    "timestamp": row["timestamp"],
                }
            )
        return {"events": eventos, "count": len(eventos), "next_cursor": last}

    def tool_platforms_list(self, params: dict[str, Any]) -> dict[str, Any]:
        from kairos_gateway.adapters import messaging_status

        return messaging_status(self._home)

    def tool_messages_send(self, params: dict[str, Any]) -> dict[str, Any]:
        from kairos_gateway.adapters import PLATFORMS, build_platform_adapters
        from kairos_gateway.service import SendResult

        target = _require_text(params, "target")
        message = _require_text(params, "message")
        plataforma, separador, destino = target.partition(":")
        if not separador or not plataforma or not destino:
            raise ToolError("target deve ser plataforma:destino (ex.: telegram:6308981865)")
        conhecidas = {p.name for p in PLATFORMS if p.needs_secret}
        if plataforma not in conhecidas and plataforma != "webhook":
            raise ToolError(f"plataforma desconhecida: {plataforma}")

        adapters = build_platform_adapters(self._home)
        adapter = adapters.get(plataforma)
        if adapter is None:
            raise ToolError(
                "plataforma não está entregável: habilite-a e salve a credencial no cofre"
            )

        obligation_id = uuid.uuid4().hex
        repo = LedgerRepository(self._db)
        repo.record(obligation_id, target, message)
        pid = os.getpid()
        started = int(time.time())
        if not repo.claim(obligation_id, pid=pid, started_at=started):
            return {"delivered": False, "pending": True, "obligation_id": obligation_id}

        try:
            import httpx

            falhas = (httpx.HTTPError, OSError, ValueError, KeyError)
        except ImportError:  # pragma: no cover - httpx é dependência do gateway
            falhas = (OSError, ValueError, KeyError)
        try:
            resultado = adapter.send(target, message)
        except falhas:
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


# ---------------------------------------------------------------------------
# Daemon de transporte
# ---------------------------------------------------------------------------


def serve_stdio(home: Path) -> int:
    """Atende requisições JSON-RPC no stdin até o EOF; sai 0 (que é a morte
    limpa esperada pelo cliente stdio, que encerra o processo).

    Nada além do JSON-RPC toca o stdout — qualquer `print` do resto da
    aplicação aqui corromperia o transporte.
    """
    server = MCPServer(Path(home))
    try:
        stdin = sys.stdin.buffer
        stdout = sys.stdout.buffer
        while True:
            raw = stdin.readline()
            if raw == b"":
                return 0
            linha = raw.decode("utf-8", errors="replace").strip()
            if not linha:
                continue
            try:
                msg = json.loads(linha)
            except json.JSONDecodeError:
                stdout.write(_encode(_error(None, -32700, "Parse error")))
                stdout.flush()
                continue
            if not isinstance(msg, dict):
                stdout.write(_encode(_error(None, -32600, "Invalid Request: não é objeto")))
                stdout.flush()
                continue
            resposta = server.dispatch(msg)
            if resposta is not None:
                stdout.write(_encode(resposta))
                stdout.flush()
    finally:
        server.close()


def _encode(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _result(result_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": result_id, "result": result}


def _error(result_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": result_id,
        "error": {"code": code, "message": message},
    }


def _tool_error(message: str) -> dict[str, Any]:
    """Falha honesta de ferramenta: texto + `isError`, não um JSON-RPC error
    — é o que o protocolo MCP espera de uma `tools/call` mal sucedida."""
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }
