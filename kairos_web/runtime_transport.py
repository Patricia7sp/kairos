"""WebSocket transport for the canonical Agent Runtime event journal."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from fastapi import WebSocket, WebSocketDisconnect

from kairos_runtime import RuntimeErrorInfo, public_error
from kairos_runtime.wire import runtime_event_to_json

__all__ = ["runtime_event_to_json", "runtime_websocket_session"]


async def runtime_websocket_session(websocket: WebSocket, client) -> None:
    """Serve read-only subscriptions; this transport never admits or replays turns."""
    await websocket.accept()
    try:
        message = await websocket.receive_json()
        session_id, cursor = _subscription(message)
        subscription = client.subscribe(session_id, cursor)

        async def forward_events() -> None:
            async for event in subscription:
                await websocket.send_text(
                    json.dumps(runtime_event_to_json(event), ensure_ascii=False, sort_keys=True)
                )

        async def wait_for_disconnect() -> None:
            message = await websocket.receive()
            if message.get("type") != "websocket.disconnect":
                raise ValueError("a assinatura de runtime aceita apenas um comando")

        forward = asyncio.create_task(forward_events(), name="runtime-websocket-forward")
        disconnected = asyncio.create_task(
            wait_for_disconnect(), name="runtime-websocket-disconnect"
        )
        try:
            done, _pending = await asyncio.wait(
                {forward, disconnected}, return_when=asyncio.FIRST_COMPLETED
            )
            if forward in done:
                await forward
            else:
                await disconnected
        finally:
            for task in (forward, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(forward, disconnected, return_exceptions=True)
            close = getattr(subscription, "aclose", None)
            if close is not None:
                await close()
    except WebSocketDisconnect:
        return
    except RuntimeErrorInfo as exc:
        safe = public_error(exc.code)
        try:
            await websocket.send_json(
                {
                    "error": {
                        "code": safe["code"],
                        "message": safe["message"],
                        "retryable": safe["retryable"],
                    }
                }
            )
            await websocket.close(code=1012 if exc.code in {"unavailable", "transport"} else 1008)
        except Exception:  # noqa: BLE001, S110 - the peer may already be gone
            pass
    except (TypeError, ValueError):
        try:
            await websocket.close(code=1008)
        except Exception:  # noqa: BLE001, S110 - the peer may already be gone
            pass


def _subscription(message: object) -> tuple[str, str | None]:
    if not isinstance(message, Mapping) or set(message) - {"type", "session_id", "cursor"}:
        raise ValueError("assinatura de runtime inválida")
    if message.get("type") != "subscribe":
        raise ValueError("assinatura de runtime inválida")
    session_id = message.get("session_id")
    cursor = message.get("cursor")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id é obrigatório")
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise ValueError("cursor inválido")
    return session_id.strip(), cursor
