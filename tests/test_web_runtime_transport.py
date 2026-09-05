from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kairos_runtime import RuntimeEvent, runtime_event_to_json
from kairos_web import server
from kairos_web.runtime_transport import runtime_websocket_session


class RuntimeStream:
    def __init__(self, event):
        self.event = event
        self.calls = []

    async def subscribe(self, session_id, cursor=None):
        self.calls.append((session_id, cursor))
        yield self.event


class QuietRuntimeStream:
    def __init__(self) -> None:
        self.subscribed = asyncio.Event()
        self.closed = asyncio.Event()

    async def subscribe(self, _session_id, _cursor=None):
        self.subscribed.set()
        try:
            await asyncio.Event().wait()
            yield  # pragma: no cover - mantém a assinatura de async generator
        finally:
            self.closed.set()


class DisconnectingRuntimeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.disconnect = asyncio.Event()
        self.receive_calls = 0

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self):
        return {"type": "subscribe", "session_id": "s1"}

    async def receive(self):
        self.receive_calls += 1
        await self.disconnect.wait()
        return {"type": "websocket.disconnect"}

    async def send_text(self, _payload):
        raise AssertionError("quiet stream must not send")

    async def send_json(self, _payload):
        raise AssertionError("disconnect must not send an error")

    async def close(self, _code):
        raise AssertionError("peer already disconnected")


@pytest.mark.anyio
async def test_idle_disconnect_closes_only_owned_subscription():
    websocket = DisconnectingRuntimeWebSocket()
    runtime = QuietRuntimeStream()
    handler = asyncio.create_task(runtime_websocket_session(websocket, runtime))
    await runtime.subscribed.wait()

    websocket.disconnect.set()
    await asyncio.wait_for(handler, timeout=0.5)

    assert websocket.accepted
    assert websocket.receive_calls == 1
    assert runtime.closed.is_set()


def test_runtime_websocket_uses_existing_auth_and_read_only_cursor(monkeypatch):
    event = RuntimeEvent(1, "e1", "s1", "t1", 2, "v1:s1:2", "text", {"delta": "oi"})
    runtime = RuntimeStream(event)
    monkeypatch.setattr(server.app.state, "runtime_client", runtime, raising=False)
    with (
        TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}) as client,
        client.websocket_connect("/ws/runtime") as ws,
    ):
        ws.send_json({"type": "subscribe", "session_id": "s1", "cursor": "v1:s1:1"})
        assert ws.receive_json() == runtime_event_to_json(event)
    assert runtime.calls == [("s1", "v1:s1:1")]


def test_runtime_websocket_rejects_unauthenticated_client(monkeypatch):
    monkeypatch.setattr(server.app.state, "runtime_client", RuntimeStream(None), raising=False)
    with TestClient(server.app) as client:
        with (
            pytest.raises(WebSocketDisconnect) as rejected,
            client.websocket_connect("/ws/runtime") as ws,
        ):
            ws.receive_json()
        assert rejected.value.code == 4401


def test_runtime_websocket_accepts_spa_ticket_once_and_rejects_replay(monkeypatch):
    event = RuntimeEvent(1, "e1", "s1", "t1", 1, "v1:s1:1", "turn_end", {"state": "completed"})
    monkeypatch.setattr(server.app.state, "runtime_client", RuntimeStream(event), raising=False)
    with TestClient(server.app) as client:
        ticket = client.post(
            "/api/auth/ws-ticket", headers={server.TOKEN_HEADER: server.SESSION_TOKEN}
        ).json()["ticket"]
        with client.websocket_connect(f"/ws/runtime?token={ticket}") as ws:
            ws.send_json({"type": "subscribe", "session_id": "s1"})
            assert ws.receive_json()["event_id"] == "e1"
        with (
            pytest.raises(WebSocketDisconnect) as replay,
            client.websocket_connect(f"/ws/runtime?token={ticket}") as ws,
        ):
            ws.receive_json()
        assert replay.value.code == 4401
