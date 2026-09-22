"""Cliente MCP `stdio` em stdlib — JSON-RPC 2.0, uma mensagem por linha.

`_reversa_sdd/mcp/` (Tarefa 12). Consome servidores MCP de terceiros: o
transporte `stdio` do protocolo é JSON-RPC 2.0 com cada mensagem serializada
em uma linha UTF-8 terminada em `\n`. Sem dependência do pacote `mcp` — o
cliente é mínimo e honesto, e o pacote continua opcional na imagem.

Fail-closed: violação de protocolo, timeout ou processo morto ⇒
``McpRuntimeError`` nomeado — nunca sucesso inventado. O servidor pode mandar
notificações e requisições enquanto esperamos uma resposta; o laço de leitura
drena e responde `Method not found` para requisições (requisito do protocolo),
sem desviar da resposta que esperamos.

Sessão é **por chamada** (spawn → initialize → call → encerra): sem processo
vivo entre turnos, sem estado compartilhado, sem zombie. Custa um spawn por
uso — proporcional para servidores que a usuária escolheu instalar. Um thread
drena o stderr para nunca travar (pipe cheio) e guarda a cauda para o erro.
"""

from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import threading
import time
from typing import Any

from kairos_mcp.client import MCPServerConfig, Transport, validate_server_config

logger = logging.getLogger(__name__)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "McpRuntimeError",
    "fetch_tool_manifests",
    "invoke_tool",
]

#: Versão de protocolo que anunciamos. O servidor negocia a que responde; para
#: os métodos básicos que usamos, a negociação é informacional.
MCP_PROTOCOL_VERSION = "2024-11-05"

_INIT_TIMEOUT_SECONDS = 15.0
_CALL_TIMEOUT_SECONDS = 60.0
_MAX_TOOLS_LIST_PAGES = 10
_PROCESS_WAIT_SECONDS = 2.0
_STDERR_CAP_CHARS = 4096


class McpRuntimeError(RuntimeError):
    """Falha de protocolo, timeout ou processo — nomeada, fail-closed."""


def _clean_tool_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise McpRuntimeError("manifesto de ferramenta MCP não é um objeto")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise McpRuntimeError("ferramenta MCP sem 'name' textual")
    out: dict[str, Any] = {"name": name}
    if isinstance(entry.get("description"), str):
        out["description"] = entry["description"]
    if isinstance(entry.get("inputSchema"), dict):
        out["inputSchema"] = entry["inputSchema"]
    return out


class _StderrSink:
    """Drena o stderr do servidor em thread e guarda a cauda (sem travar o pipe)."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._lines: list[str] = []
        self._proc = proc
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._drain,
            name="kairos-mcp-stderr",
            daemon=True,
        )
        self._thread.start()

    def _drain(self) -> None:
        assert self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, ""):
            line = raw.strip()
            if line:
                with self._lock:
                    self._lines.append(line)
                    total = sum(len(entry) for entry in self._lines)
                    while total > _STDERR_CAP_CHARS and len(self._lines) > 1:
                        total -= len(self._lines.pop(0))

    def tail(self) -> str:
        with self._lock:
            return " | ".join(self._lines)

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)


class _Session:
    """Spawn + leitura/escrita JSON-RPC linha a linha com timeout."""

    def __init__(self, config: MCPServerConfig) -> None:
        validate_server_config(config)
        if config.transport is not Transport.STDIO:
            raise McpRuntimeError(
                f"{config.name}: transporte {config.transport.value} não é stdio — "
                "o cliente desta entrega é stdio puro"
            )
        assert config.command is not None
        env = dict(os.environ)
        env.update(config.env)
        self._config = config
        # argv vem da config do operador e é validado nas DUAS pontas
        # (salvamento e spawn) por validate_server_config; a linha é
        # executável, nunca shell.
        self._proc = subprocess.Popen(  # noqa: S603 -- validate_server_config nas duas pontas
            [config.command, *config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        self._stderr = _StderrSink(self._proc)
        self._next_id = 0
        self._buf = b""

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def name(self) -> str:
        return self._config.name

    def close(self) -> None:
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=_PROCESS_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=_PROCESS_WAIT_SECONDS)
        finally:
            self._stderr.join(_PROCESS_WAIT_SECONDS)

    # -- primitivas do transporte -----------------------------------------

    def _stderr_tail(self) -> str:
        tail = self._stderr.tail()
        return (": " + tail) if tail else ""

    def _write(self, payload: dict[str, Any]) -> None:
        if self._proc.poll() is not None:
            raise McpRuntimeError(
                f"{self.name}: servidor encerrou antes da escrita{self._stderr_tail()}"
            )
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpRuntimeError(f"{self.name}: escrita falhou — servidor morto ({exc})") from exc

    def _read_line(self, timeout: float) -> str:
        """Uma linha JSON completa, lida crua do fd (sem double-buffer).

        `select` + `readline()` do wrapper textual é uma armadilha: quando o
        servidor manda duas mensagens numa tacada só (ex.: notificação +
        resposta), o wrapper bufferiza a segunda e o `select` no fd cru não
        acorda nunca — timeout espúrio. Por isso a leitura é `os.read` no fd e
        a montagem de linha é nossa.
        """
        assert self._proc.stdout is not None
        fd = self._proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while True:
            newline = self._buf.find(b"\n")
            if newline != -1:
                raw = self._buf[:newline]
                self._buf = self._buf[newline + 1 :]
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    return text
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpRuntimeError(
                    f"{self.name}: timeout aguardando resposta{self._stderr_tail()}"
                )
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(fd, 65_536)
            if not chunk:
                code = self._proc.poll()
                raise McpRuntimeError(
                    f"{self.name}: servidor fechou a conexão (exit {code}){self._stderr_tail()}"
                )
            self._buf += chunk

    def _read_message(self, timeout: float) -> dict[str, Any]:
        raw = self._read_line(timeout)
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpRuntimeError(f"{self.name}: resposta fora do JSON-RPC: {raw[:80]!r}") from exc
        if not isinstance(msg, dict):
            raise McpRuntimeError(f"{self.name}: resposta JSON não é um objeto")
        return msg

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)
        return self._await(method, rid)

    def _await(self, method: str, rid: int) -> dict[str, Any]:
        while True:
            msg = self._read_message(_CALL_TIMEOUT_SECONDS)
            if msg.get("id") == rid:
                if "error" in msg:
                    err = msg["error"]
                    message = err.get("message") if isinstance(err, dict) else err
                    raise McpRuntimeError(f"{self.name}: {method} falhou no servidor: {message}")
                return msg
            if not msg.get("method"):
                raise McpRuntimeError(
                    f"{self.name}: mensagem sem method nem id esperado — protocolo violado"
                )
            if "id" in msg:
                # Requisição iniciada pelo servidor (ping, sampling...): o
                # protocolo exige resposta; não reconhecemos o método, recusamos
                # nomeado e seguimos aguardando a nossa.
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
            # Notificação do servidor: ignora e segue esperando a resposta.

    def initialize(self) -> dict[str, Any]:
        response = self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kairos", "version": "0.1.0"},
            },
        )
        result = response.get("result")
        self.notify("notifications/initialized")
        if not isinstance(result, dict):
            raise McpRuntimeError(f"{self.name}: initialize sem result de objeto")
        return result

    def tools_list(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: Any = None
        for _ in range(_MAX_TOOLS_LIST_PAGES):
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            response = self.request("tools/list", params)
            result = response.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise McpRuntimeError(f"{self.name}: tools/list sem lista de ferramentas")
            for entry in result["tools"]:
                tools.append(_clean_tool_entry(entry))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise McpRuntimeError(
            f"{self.name}: catálogo excede {_MAX_TOOLS_LIST_PAGES} páginas — abortado"
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpRuntimeError(f"{self.name}: tools/call sem result de objeto")
        if result.get("isError"):
            raise McpRuntimeError(f"{self.name}: {name} retornou erro no servidor")
        content = result.get("content")
        if content is None:
            return ""
        if not isinstance(content, list):
            raise McpRuntimeError(f"{self.name}: content fora de lista")
        return self._render_content(name, content)

    def _render_content(self, name: str, content: list[Any]) -> str:
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                raise McpRuntimeError(f"{self.name}: bloco de conteúdo sem objeto")
            if block.get("type") == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise McpRuntimeError(f"{self.name}: bloco text sem texto")
                parts.append(text)
                continue
            # Blocos não-textuais (image/resource/audio...) existem no protocolo,
            # mas não são renderizáveis como texto: registramos a presença, sem
            # fingir tê-los decodificado.
            kind = block.get("type", "desconhecido")
            note = f"[bloco MCP {kind} — não renderizado como texto]"
            if isinstance(block.get("uri"), str):
                note = f"[bloco MCP {kind} — {block['uri']}]"
            parts.append(note)
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def fetch_tool_manifests(config: MCPServerConfig) -> list[dict[str, Any]]:
    """`initialize` + `tools/list` (paginado). Spawn por chamada."""
    with _Session(config) as session:
        session.initialize()
        return session.tools_list()


def invoke_tool(config: MCPServerConfig, tool_name: str, arguments: dict[str, Any] | None) -> str:
    """`initialize` + `tools/call`. Spawn por chamada; encerra sempre."""
    with _Session(config) as session:
        session.initialize()
        return session.call_tool(tool_name, arguments or {})
