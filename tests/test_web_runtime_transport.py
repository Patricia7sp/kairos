from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kairos_runtime import RuntimeEvent, runtime_event_to_json
from kairos_web import server


class RuntimeStream:
    def __init__(self, event):
        self.event = event
        self.calls = []

    async def subscribe(self, session_id, cursor=None):
        self.calls.append((session_id, cursor))
        yield self.event


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
