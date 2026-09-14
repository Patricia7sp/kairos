"""Rota HTTP de decisão de aprovação das ferramentas do Chat (WS-2)."""

import pytest
from fastapi.testclient import TestClient

from kairos_integration.interaction_contract import InteractionServiceError
from kairos_web import server
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


class StubService:
    def __init__(self, error_kind=None):
        self.error_kind = error_kind
        self.decisions = []

    def decide_tool_approval(self, *, approval_id, session_id, decision):
        self.decisions.append((approval_id, session_id, decision))
        if self.error_kind is not None:
            raise InteractionServiceError(
                self.error_kind,
                "rejeitada pelo stub",
                retryable=False,
            )


def post(service, *, approval_id="a1", decision="allow", session_id="s1"):
    server.app.state.interaction_service = service
    with TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN}) as client:
        return client.post(
            f"/api/chat/sessions/{session_id}/tool-approvals/{approval_id}",
            json={"decision": decision},
        )


def test_allow_decision_resolves_pending_approval():
    service = StubService()
    response = post(service)
    assert response.status_code == 200
    assert response.json() == {"status": "decided"}
    assert service.decisions == [("a1", "s1", "allow")]


def test_deny_decision_is_forwarded_verbatim():
    service = StubService()
    response = post(service, decision="deny")
    assert response.status_code == 200
    assert service.decisions == [("a1", "s1", "deny")]


def test_unknown_decision_is_rejected_before_the_service():
    service = StubService()
    response = post(service, decision="maybe")
    assert response.status_code == 400
    assert response.json() == {"error": "approval_invalid_decision"}
    assert service.decisions == []


def test_missing_body_decision_is_rejected():
    server.app.state.interaction_service = StubService()
    with TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN}) as client:
        response = client.post("/api/chat/sessions/s1/tool-approvals/a1", json={})
    assert response.status_code == 400
    assert response.json() == {"error": "approval_invalid_decision"}


@pytest.mark.parametrize(
    ("error_kind", "status"),
    [
        ("approval_not_found", 404),
        ("approval_session_mismatch", 403),
        ("approval_already_decided", 409),
        ("approval_invalid_decision", 400),
    ],
)
def test_service_error_kinds_map_to_http_statuses(error_kind, status):
    response = post(StubService(error_kind=error_kind))
    assert response.status_code == status
    assert response.json() == {"error": error_kind}


def test_missing_service_reports_unavailable():
    server.app.state.interaction_service = object()
    with TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN}) as client:
        response = client.post(
            "/api/chat/sessions/s1/tool-approvals/a1", json={"decision": "allow"}
        )
    assert response.status_code == 503
    assert response.json() == {"error": "unavailable"}
