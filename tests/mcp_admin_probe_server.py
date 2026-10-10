"""Servidor stdio controlado com journal para contratos de administração MCP."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    journal = Path(sys.argv[1])
    mode = sys.argv[2]

    def record(event: dict) -> None:
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")

    def send(payload: dict) -> None:
        print(json.dumps(payload), flush=True)

    record({"event": "spawn"})
    for line in sys.stdin:
        request = json.loads(line)
        record({"event": "request", **request})
        method = request.get("method")
        rid = request.get("id")
        if rid is None:
            continue
        if method == "initialize":
            if mode == "refuse":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32000, "message": "REMOTE_SECRET_ABC"},
                    }
                )
                continue
            if mode == "die":
                print("REMOTE_SECRET_ABC", file=sys.stderr, flush=True)
                return 3
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "admin-probe", "version": "1"},
            }
        elif method == "tools/list":
            cursor = request.get("params", {}).get("cursor")
            if cursor is None:
                tools = [
                    {
                        "name": "first",
                        "description": "Primeira página",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
                result = {"tools": tools, "nextCursor": "second-page"}
            elif cursor == "second-page":
                tools = [
                    {
                        "name": "last",
                        "description": "Última página",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                        },
                    }
                ]
                result = {"tools": tools}
            else:
                return 4
            record({"event": "page", "tools": tools})
        else:
            return 5
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
