"""Bounded, credential-free worker protocol; upstream authentication stays in transport.

JSONL protocol v1 (UTF-8, each line at most MAX_FRAME_BYTES, excluding newline):
worker -> broker: {"type":"ready","protocol":1}, then
{"type":"request","id":positive monotonically increasing int,"body":object}.
broker -> worker: {"type":"start","id":id,"status":200}, zero or more
{"type":"chunk","id":id,"data":base64}, then {"type":"end","id":id};
or {"type":"error","id":id,"status":400|403|429|502,"error":generic string}.
No headers, routes, credentials, or upstream exception messages cross this protocol.
The bridge mirrors the constants below because it runs standalone with Python -I.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing

PROTOCOL_VERSION = 1
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765
MODEL_PATH = "/responses"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = MAX_BODY_BYTES + 4096
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024
MAX_REQUESTS_PER_TURN = 32
START_TIMEOUT = 10.0
IO_TIMEOUT = 10.0
REQUEST_TIMEOUT = 120.0
LOCAL_TOOL_TYPES = frozenset({"function", "custom"})
FORBIDDEN_FIELDS = frozenset(
    {
        "url",
        "base_url",
        "endpoint",
        "path",
        "method",
        "headers",
        "extra_headers",
        "authorization",
        "api_key",
        "query",
        "extra_query",
        "extra_body",
    }
)
Transport = Callable[[dict], AsyncIterator[bytes]]


def _local_tool(tool: object) -> bool:
    return (
        isinstance(tool, dict)
        and isinstance(tool.get("type"), str)
        and (
            tool["type"] in LOCAL_TOOL_TYPES
            # Pinned Codex's tool discovery handler executes inside the worker.
            or (tool["type"] == "tool_search" and tool.get("execution") == "client")
        )
    )


def _local_tool_choice(choice: object) -> bool:
    if isinstance(choice, str):
        return choice in {"auto", "none", "required"}
    if _local_tool(choice):
        return True
    if isinstance(choice, dict) and choice.get("type") == "allowed_tools":
        local_tools = choice.get("tools")
        return isinstance(local_tools, list) and all(_local_tool(tool) for tool in local_tools)
    return False


def _local_input(items: object) -> bool:
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        # Responses Lite carries executable tool definitions on developer items.
        extra = item.get("additional_tools", [])
        if not isinstance(extra, list) or not all(_local_tool(tool) for tool in extra):
            return False
        if item.get("type") == "tool_search_output" and item.get("execution") != "client":
            return False
        if item.get("type") in ("additional_tools", "tool_search_output"):
            tools = item.get("tools")
            if not isinstance(tools, list) or not all(_local_tool(tool) for tool in tools):
                return False
    return True


class ModelRelay:
    """One bridge, at most one admitted request, with admission revoked per turn."""

    def __init__(self, reader, writer, transport: Transport, model: str):
        if not isinstance(model, str) or not model:
            raise ValueError("model is required")
        self.reader = reader
        self.writer = writer
        self.transport = transport
        self.model = model
        self._buffer = bytearray()
        self._runner: asyncio.Task | None = None
        self._request: asyncio.Task | None = None
        self._turn: str | None = None
        self._count = 0
        self._last_id = 0
        self._closed = False

    async def start(self) -> None:
        if self._closed or self._runner is not None:
            raise RuntimeError("relay cannot start")
        try:
            async with asyncio.timeout(START_TIMEOUT):
                frame = await self._read()
            if frame != {"type": "ready", "protocol": PROTOCOL_VERSION}:
                raise ValueError("invalid bridge handshake")
        except asyncio.CancelledError:
            await self.aclose()
            raise
        except (ValueError, EOFError, OSError, TimeoutError):
            await self.aclose()
            raise RuntimeError("model bridge unavailable") from None
        self._runner = asyncio.create_task(self._run())

    def allow_turn(self, turn_id: str) -> None:
        if self._closed or self._runner is None or self._runner.done():
            raise RuntimeError("relay unavailable")
        if not turn_id or self._turn is not None:
            raise RuntimeError("relay already has a turn")
        self._turn = turn_id
        self._count = 0

    async def end_turn(self, turn_id: str) -> None:
        if self._turn != turn_id:
            return
        self._turn = None
        await self._cancel_request()

    async def _cancel_request(self) -> None:
        if self._request is not None:
            self._request.cancel()
            try:
                await self._request
            except asyncio.CancelledError:
                pass
            self._request = None

    async def aclose(self) -> None:
        self._closed = True
        self._turn = None
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
        await self._cancel_request()
        self.writer.close()
        try:
            async with asyncio.timeout(IO_TIMEOUT):
                await self.writer.wait_closed()
        except (OSError, TimeoutError):
            pass

    async def _read(self) -> dict:
        while True:
            position = self._buffer.find(b"\n")
            if position >= 0:
                if position > MAX_FRAME_BYTES:
                    raise ValueError("frame too large")
                line = bytes(self._buffer[:position])
                del self._buffer[: position + 1]
                try:
                    frame = json.loads(line)
                except (ValueError, RecursionError):
                    raise ValueError("invalid frame") from None
                if not isinstance(frame, dict):
                    raise ValueError("invalid frame")
                return frame
            if len(self._buffer) > MAX_FRAME_BYTES:
                raise ValueError("frame too large")
            if self._buffer:
                async with asyncio.timeout(IO_TIMEOUT):
                    chunk = await self.reader.read(MAX_CHUNK_BYTES)
            else:
                chunk = await self.reader.read(MAX_CHUNK_BYTES)
            if not chunk:
                raise EOFError
            self._buffer.extend(chunk)

    async def _send(self, frame: dict) -> None:
        self.writer.write(json.dumps(frame, separators=(",", ":")).encode() + b"\n")
        async with asyncio.timeout(IO_TIMEOUT):
            await self.writer.drain()

    async def _error(self, request_id: int, status: int) -> None:
        await self._send(
            {
                "type": "error",
                "id": request_id,
                "status": status,
                "error": "model request failed" if status == 502 else "model request rejected",
            }
        )

    def _validate(self, frame: dict) -> int | None:
        if set(frame) != {"type", "id", "body"} or frame["type"] != "request":
            return 400
        if self._turn is None:
            return 403
        if self._count >= MAX_REQUESTS_PER_TURN:
            return 429
        if self._request is not None and not self._request.done():
            return 429
        body = frame["body"]
        if not isinstance(body, dict) or body.get("model") != self.model:
            return 400
        if body.get("stream") is not True or FORBIDDEN_FIELDS.intersection(body):
            return 400
        local_tools = body.get("tools", [])
        if not isinstance(local_tools, list) or not all(_local_tool(tool) for tool in local_tools):
            return 400
        if not _local_tool_choice(body.get("tool_choice", "auto")):
            return 400
        if not _local_input(body.get("input", [])):
            return 400
        try:
            encoded = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError):
            return 400
        if len(encoded) > MAX_BODY_BYTES:
            return 400
        return None

    async def _run(self) -> None:
        try:
            while True:
                frame = await self._read()
                request_id = frame.get("id")
                if type(request_id) is not int or not self._last_id < request_id <= 2**53:
                    raise ValueError("invalid request id")
                self._last_id = request_id
                status = self._validate(frame)
                if status is not None:
                    await self._error(request_id, status)
                    continue
                self._count += 1
                self._request = asyncio.create_task(self._forward(request_id, frame["body"]))
        except (ValueError, EOFError, OSError, TimeoutError):
            pass
        finally:
            self._closed = True
            self._turn = None
            await self._cancel_request()
            self.writer.close()

    async def _forward(self, request_id: int, body: dict) -> None:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT), aclosing(self.transport(body)) as stream:
                started, total = False, 0
                async for chunk in stream:
                    if not isinstance(chunk, bytes):
                        raise ValueError("invalid upstream data")
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ValueError("response too large")
                    if not started:
                        await self._send({"type": "start", "id": request_id, "status": 200})
                        started = True
                    for offset in range(0, len(chunk), MAX_CHUNK_BYTES):
                        await self._send(
                            {
                                "type": "chunk",
                                "id": request_id,
                                "data": base64.b64encode(
                                    chunk[offset : offset + MAX_CHUNK_BYTES]
                                ).decode("ascii"),
                            }
                        )
                if not started:
                    await self._send({"type": "start", "id": request_id, "status": 200})
                await self._send({"type": "end", "id": request_id})
        except asyncio.CancelledError:
            await self._try_error(request_id)
            raise
        except Exception:  # noqa: BLE001 — transport errors must never expose credentials.
            await self._try_error(request_id)

    async def _try_error(self, request_id: int) -> None:
        try:
            await self._error(request_id, 502)
        except (OSError, TimeoutError):
            pass
