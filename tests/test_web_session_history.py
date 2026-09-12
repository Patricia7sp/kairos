"""Session organization and tool identity across persisted history reads."""

import pytest
from fastapi.testclient import TestClient

from kairos_state import connect, initialize_schema
from kairos_state.repositories.messages import MessageRepository
from kairos_state.repositories.sessions import SessionRepository
from kairos_web import server


@pytest.fixture
def history_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    conn = connect(tmp_path / "state.db")
    initialize_schema(conn)
    sessions = SessionRepository(conn)
    messages = MessageRepository(conn)
    for kind in ("model", "agent_runtime"):
        sessions.create(kind, source="web", execution_kind=kind)
        messages.append(kind, "user", content="Run the check", timestamp=1)
        messages.append(kind, "tool", content="Check passed", tool_name="shell", timestamp=2)
        messages.append(
            kind, "assistant", content="Earlier history", active=0, compacted=1, timestamp=3
        )
    conn.close()
    with TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}) as client:
        yield client


def test_null_tags_are_rejected_without_applying_other_metadata(history_client):
    history_client.patch("/api/sessions/model", json={"tags": ["keep"]})

    response = history_client.patch("/api/sessions/model", json={"tags": None, "archived": True})

    assert response.status_code == 400
    detail = history_client.get("/api/sessions/model").json()
    assert detail["tags"] == ["keep"]
    assert detail["archived"] is False


@pytest.mark.parametrize("kind", ["model", "agent_runtime"])
def test_history_keeps_tool_identity_and_compacted_content(history_client, kind):
    response = history_client.get(f"/api/sessions/{kind}/messages")

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[1].get("tool_name") == "shell"
    assert [message["content"] for message in messages] == [
        "Run the check",
        "Check passed",
        "Earlier history",
    ]
