from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kairos_runtime import RuntimeErrorInfo
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository
from kairos_web import server


class FakeRuntimeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def status(self):
        return {
            "enabled": True,
            "state": "ready",
            "authorized_projects": ["/srv/project"],
            "sandbox_profiles": ["read_only", "workspace_write"],
        }

    async def account_status(self):
        return {
            "requires_openai_auth": True,
            "authenticated": False,
            "auth_mode": None,
            "email": None,
            "plan_type": None,
            "login_state": "idle",
        }

    async def account_login(self, mode, api_key=None):
        self.calls.append(("login", mode, api_key))
        return {"mode": mode, "state": "succeeded"}

    async def account_cancel(self, login_id):
        self.calls.append(("login_cancel", login_id))

    async def account_logout(self):
        self.calls.append(("logout",))

    async def create(self, cwd, sandbox, **kwargs):
        self.calls.append(("create", cwd, sandbox, kwargs))
        return {"session_id": "runtime-1", "canonical_cwd": cwd, "sandbox_profile": sandbox}

    async def submit(self, session_id, content, idempotency_key):
        self.calls.append(("submit", session_id, content, idempotency_key))
        return "turn-1"

    async def end(self, session_id):
        self.calls.append(("end", session_id))

    async def cancel(self, session_id, turn_id):
        self.calls.append(("cancel", session_id, turn_id))

    async def decide(self, session_id, approval_id, decision):
        self.calls.append(("decide", session_id, approval_id, decision))

    async def aclose(self):
        self.calls.append(("close",))


@pytest.fixture
def runtime_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = server.app.state
    fake = FakeRuntimeClient()
    project = tmp_path / "project"
    project.mkdir()
    db = connect(tmp_path / "state.db")
    initialize_schema(db)
    SessionRepository(db).create("model-1", "test")
    db.close()
    monkeypatch.setattr(state, "kairos_home", tmp_path, raising=False)
    monkeypatch.setattr(state, "runtime_client", fake, raising=False)
    with TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}) as client:
        yield client, fake, project


def test_runtime_rest_roundtrip_is_authenticated_strict_and_returns_durable_turn(runtime_api):
    client, fake, project = runtime_api
    assert client.get("/api/runtime/status").json() == {
        "enabled": True,
        "state": "ready",
        "authorized_projects": ["/srv/project"],
        "sandbox_profiles": ["read_only", "workspace_write"],
    }
    assert client.get("/api/runtime/account").json()["auth_mode"] is None
    created = client.post(
        "/api/runtime/sessions",
        json={"cwd": str(project), "sandbox": "workspace_write", "session_id": "runtime-1"},
    )
    assert created.status_code == 200

    db = connect(project.parent / "state.db")
    with db:
        db.execute(
            "INSERT INTO sessions(id,source,started_at,execution_kind) VALUES ('runtime-1','web',1,'agent_runtime')"
        )
    db.close()
    accepted = client.post(
        "/api/runtime/sessions/runtime-1/turns",
        json={"content": "olá", "idempotency_key": "durable-one"},
    )
    assert accepted.status_code == 202
    assert accepted.json() == {"session_id": "runtime-1", "turn_id": "turn-1", "status": "accepted"}
    assert ("submit", "runtime-1", "olá", "durable-one") in fake.calls

    assert (
        client.post(
            "/api/runtime/login", json={"method": "apiKey", "api_key": "secret", "extra": True}
        ).status_code
        == 422
    )
    assert (
        client.get("/api/runtime/status", headers={server.TOKEN_HEADER: "wrong"}).status_code == 401
    )


def test_invalid_runtime_login_never_echoes_api_key_in_validation_error(runtime_api):
    client, _fake, _project = runtime_api
    secret = "sk-sentinel-must-not-return"  # noqa: S105 - redaction sentinel
    response = client.post(
        "/api/runtime/login",
        json={"method": "chatgpt", "api_key": secret, "unexpected": {"secret": secret}},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "requisição de runtime inválida"}
    assert secret not in response.text


def test_runtime_account_response_is_exact_and_rejects_unknown_host_fields(
    runtime_api, monkeypatch
):
    client, fake, _project = runtime_api
    secret = "host-secret-sentinel"  # noqa: S105 - redaction sentinel

    async def unsafe_status():
        return {
            "requires_openai_auth": True,
            "authenticated": False,
            "auth_mode": None,
            "email": None,
            "plan_type": None,
            "login_state": "idle",
            "api_key": secret,
        }

    monkeypatch.setattr(fake, "account_status", unsafe_status)
    response = client.get("/api/runtime/account")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_event"
    assert secret not in response.text


def test_runtime_status_response_rejects_unknown_or_malformed_metadata(runtime_api, monkeypatch):
    client, fake, _project = runtime_api

    async def unsafe_status():
        return {
            "enabled": True,
            "state": "ready",
            "authorized_projects": ["/srv/project"],
            "sandbox_profiles": ["read_only"],
            "raw_config": {"codex_binary": "/private/codex"},
        }

    monkeypatch.setattr(fake, "status", unsafe_status)
    response = client.get("/api/runtime/status")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_event"
    assert "/private/codex" not in response.text


def test_runtime_rest_maps_identity_missing_policy_and_unavailable(runtime_api, monkeypatch):
    client, fake, _project = runtime_api
    assert (
        client.post(
            "/api/runtime/sessions/missing/turns",
            json={"content": "x", "idempotency_key": "one"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/runtime/sessions/model-1/turns",
            json={"content": "x", "idempotency_key": "one"},
        ).status_code
        == 409
    )

    async def unavailable():
        raise RuntimeErrorInfo("unavailable", "internal host detail", True)

    monkeypatch.setattr(fake, "status", unavailable)
    response = client.get("/api/runtime/status")
    assert response.status_code == 503
    assert "internal host detail" not in response.text


def test_existing_session_reads_join_runtime_metadata_without_changing_model_rows(
    tmp_path, monkeypatch
):
    state = server.app.state
    db = connect(tmp_path / "state.db")
    initialize_schema(db)
    SessionRepository(db).create("model", "cli")
    project = tmp_path / "project"
    project.mkdir()
    with db:
        db.execute(
            "INSERT INTO sessions(id,source,started_at,cwd,execution_kind) VALUES ('runtime','web',2,?,'agent_runtime')",
            (str(project),),
        )
        db.execute(
            "INSERT INTO runtime_sessions(session_id,runtime_kind,requested_cwd,canonical_cwd,sandbox_profile,state,directory_device,directory_inode,created_at,updated_at) VALUES ('runtime','codex',?,?,'read_only','ready',?,?,2,2)",
            (str(project), str(project.resolve()), project.stat().st_dev, project.stat().st_ino),
        )
    db.close()
    monkeypatch.setattr(state, "kairos_home", tmp_path, raising=False)
    with TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}) as client:
        rows = {row["id"]: row for row in client.get("/api/sessions").json()["sessions"]}
        detail = client.get("/api/sessions/runtime").json()
    assert rows["model"]["execution_kind"] == "model"
    assert rows["runtime"]["runtime_kind"] == "codex"
    assert detail["sandbox"] == "read_only"
    assert detail["runtime_state"] == "ready"
