import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from kairos_web import server
from scripts.e03.seeder import install_gateway_seam


def profile(home: Path):
    home.chmod(0o700)
    marker = home / ".e03-temporary"
    marker.write_text(
        json.dumps(
            {"profile": "e03-synthetic", "run_id": "a" * 32, "expires_at": time.time() + 600}
        )
    )
    marker.chmod(0o600)
    (home / "config.yaml").write_text("{}\n")
    return home


def test_seeder_refuses_without_explicit_temporary_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIROS_E03_TEMPORARY", raising=False)
    with (
        pytest.raises(ValueError, match="perfil temporário"),
        install_gateway_seam(profile(tmp_path)),
    ):
        pytest.fail("seeder habilitado sem flag")


def test_seeder_refuses_unmarked_or_public_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_E03_TEMPORARY", "1")
    with pytest.raises(ValueError), install_gateway_seam(tmp_path):
        pytest.fail("home sem identidade aceita")
    profile(tmp_path).chmod(0o755)
    with pytest.raises(ValueError), install_gateway_seam(tmp_path):
        pytest.fail("home público aceito")


def test_seeder_refuses_provider_credentials_before_patching(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_E03_TEMPORARY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-only")
    with pytest.raises(ValueError, match="credencial"), install_gateway_seam(profile(tmp_path)):
        pytest.fail("credencial herdada aceita")


def test_gateway_seam_drives_real_chat_deny_http_and_persisted_record(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_E03_TEMPORARY", "1")
    execute = AsyncMock(side_effect=AssertionError("ferramenta negada não pode executar"))
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", execute)
    with install_gateway_seam(profile(tmp_path)):
        monkeypatch.delattr(server.app.state, "interaction_service", raising=False)
        monkeypatch.delattr(server.app.state, "runtime_client", raising=False)
        monkeypatch.setattr(server.app.state, "kairos_home", tmp_path, raising=False)
        with TestClient(server.app, headers={server.TOKEN_HEADER: server.SESSION_TOKEN}) as client:
            sid = "e03-synthetic-" + "c" * 32
            with client.websocket_connect(
                "/ws/chat", headers={server.TOKEN_HEADER: server.SESSION_TOKEN}
            ) as ws:
                ws.send_json(
                    {
                        "type": "message",
                        "session_id": sid,
                        "content": "e03-synthetic:controle",
                        "tools": True,
                        "provider": "openrouter",
                        "model": "e03-synthetic",
                    }
                )
                approval = None
                for _ in range(20):
                    event = ws.receive_json()
                    assert event["type"] != "turn_error", event
                    if event["type"] == "tool_approval_request":
                        approval = event
                        break
                assert approval is not None
                aid = approval["approval_id"]
                call_id = approval["tool_call"]["id"]
                response = client.post(
                    f"/api/chat/sessions/{sid}/tool-approvals/{aid}", json={"decision": "deny"}
                )
                assert response.status_code == 200
                assert response.json() == {"status": "decided"}
                result = None
                for _ in range(20):
                    event = ws.receive_json()
                    if event["type"] == "tool_result":
                        result = event["tool_result"]
                    if event["type"] == "turn_end":
                        break
                assert result is not None
                assert result["tool_call_id"] == call_id
                assert json.loads(result["content"])["status"] == "denied"
                rows = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
                persisted = [row for row in rows if row.get("tool_call_id") == call_id]
                assert len(persisted) == 1
                assert persisted[0]["role"] == "tool"
                assert persisted[0]["is_error"] is True
                assert json.loads(persisted[0]["content"])["status"] == "denied"
                assert persisted[0]["id"] > 0
    execute.assert_not_awaited()


def test_seeder_restores_factories_and_refuses_foreign_or_expired_profile(tmp_path, monkeypatch):
    from kairos_providers import composition

    monkeypatch.setenv("KAIROS_E03_TEMPORARY", "1")
    previous = composition.build_provider_gateway
    home = profile(tmp_path)
    with install_gateway_seam(home):
        assert composition.build_provider_gateway is not previous
        with pytest.raises(ValueError):
            composition.build_provider_gateway(home / "foreign")
    assert composition.build_provider_gateway is previous
    marker = home / ".e03-temporary"
    record = json.loads(marker.read_text())
    record["expires_at"] = time.time() - 1
    marker.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="expirada"), install_gateway_seam(home):
        pytest.fail("perfil expirado aceito")
