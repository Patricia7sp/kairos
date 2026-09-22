"""Servidor MCP `stdio` FALSO — JSON-RPC 2.0, uma mensagem por linha.

Não é a ferramenta sob teste: é a **cinta** dos testes de MCP de terceiros. Os
testes comportamentais spawnam este processo de verdade (``Popen`` + pipes) e
conversam pelo protocolo real de linha, exercitando o cliente
``kairos_mcp.runtime`` sem depender de servidor real, de rede ou do pacote
`mcp`. Só stdlib.

Ferramentas do catálogo (algumas só na página 2, com ``KAIROS_FAKE_MCP_PAGES=2``):

- ``sum`` — soma dois números (texto).
- ``echo_notifica`` — manda uma notificação antes da resposta.
- ``ping_probe`` — manda uma requisição iniciada pelo servidor antes da resposta.
- ``com_bloco`` — resposta com bloco text + bloco image (não renderizável).
- ``ruidoso`` — escreve no stderr antes de responder (teste do dreno).
- ``explode`` — responde com ``isError: true`` e segue vivo.
- ``morre`` — encerra a conexão sem responder (falha fail-closed no `tools/call`).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

TOOLS = [
    {
        "name": "sum",
        "description": "Soma dois números e devolve o total.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
    {
        "name": "echo_notifica",
        "description": "Repete o texto recebido.",
        "inputSchema": {"type": "object", "properties": {"texto": {"type": "string"}}},
    },
    {
        "name": "ping_probe",
        "description": "Manda requisição própria antes de responder.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "com_bloco",
        "description": "Resposta com bloco não textual.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ruidoso",
        "description": "Suja o stderr antes de responder.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "explode",
        "description": "Responde erro nomeado no servidor.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "morre",
        "description": "Fecha a conexão sem responder.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def pages() -> int:
    return int(os.environ.get("KAIROS_FAKE_MCP_PAGES", "1"))


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(rid: int, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _call(rid: int, name: str, arguments: dict[str, object]) -> None:
    if name == "sum":
        a_val: Any = arguments.get("a", 0.0)
        b_val: Any = arguments.get("b", 0.0)
        total = float(a_val) + float(b_val)
        texto = str(int(total)) if float(total).is_integer() else str(total)
        _ok(rid, {"content": [{"type": "text", "text": texto}]})
    elif name == "echo_notifica":
        _send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"p": 0.5}})
        _ok(rid, {"content": [{"type": "text", "text": str(arguments.get("texto", ""))}]})
    elif name == "ping_probe":
        _send({"jsonrpc": "2.0", "id": 999, "method": "custom/foo", "params": {}})
        _ok(rid, {"content": [{"type": "text", "text": "ping ok"}]})
    elif name == "com_bloco":
        _ok(
            rid,
            {
                "content": [
                    {"type": "text", "text": "verde"},
                    {"type": "image", "uri": "file:///tmp/x.png", "mimeType": "image/png"},
                ]
            },
        )
    elif name == "ruidoso":
        sys.stderr.write("KAIROS_FAKE_MCP: ruído de teste\n")
        sys.stderr.flush()
        _ok(rid, {"content": [{"type": "text", "text": "ruído ok"}]})
    elif name == "explode":
        _ok(
            rid,
            {
                "isError": True,
                "content": [{"type": "text", "text": "falha deliberada do servidor"}],
            },
        )
    elif name == "morre":
        sys.exit(3)  # sem resposta — o cliente vê a conexão fechar.
    else:
        _ok(
            rid,
            {
                "isError": True,
                "content": [{"type": "text", "text": f"ferramenta desconhecida: {name}"}],
            },
        )


def _tools(rid: int, cursor: str | None) -> None:
    if pages() == 1:
        _ok(rid, {"tools": TOOLS})
        return
    if cursor is None:
        _ok(rid, {"tools": TOOLS[:4], "nextCursor": "p2"})
    elif cursor == "p2":
        _ok(rid, {"tools": TOOLS[4:]})
    else:
        _ok(rid, {"tools": []})


def main() -> int:
    refuse = os.environ.get("KAIROS_FAKE_MCP_REFUSE", "") in {"1", "true", "yes"}
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        rid = msg.get("id")
        if rid is None:
            continue  # notificação do cliente — sem resposta exigida
        if method == "initialize":
            if refuse:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32000, "message": "recusa deliberada do initialize"},
                    }
                )
            else:
                _ok(
                    rid,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "kairos-fake-mcp", "version": "0.0.0"},
                    },
                )
        elif method == "tools/list":
            _tools(rid, (msg.get("params") or {}).get("cursor"))
        elif method == "tools/call":
            params = msg.get("params") or {}
            _call(rid, params.get("name", ""), params.get("arguments") or {})
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
