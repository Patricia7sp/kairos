"""Testes para o servidor FastAPI e rotas da interface web do Kairos."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


class WebServerApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name
        self.client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def tearDown(self):
        if self._orig_home:
            os.environ["KAIROS_HOME"] = self._orig_home
        else:
            os.environ.pop("KAIROS_HOME", None)
        self._tmp.cleanup()

    def test_health_endpoint(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("app"), "kairos")

    def test_models_endpoint(self):
        res = self.client.get("/api/models")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("default_model", data)
        self.assertIn("models", data)
        self.assertTrue(len(data["models"]) > 0)
        # Verifica se gpt-4o, claude e gemini estao listados
        ids = [m["id"] for m in data["models"]]
        self.assertIn("gpt-4o", ids)
        self.assertIn("claude-3-7-sonnet-20250219", ids)
        self.assertIn("gemini-2.0-flash", ids)

    def test_providers_status_endpoint(self):
        res = self.client.get("/api/providers")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("providers", data)
        prov_names = [p["provider"] for p in data["providers"]]
        self.assertIn("openai", prov_names)
        self.assertIn("anthropic", prov_names)
        self.assertIn("gemini", prov_names)

    def test_set_default_model_and_task_override(self):
        # 1. Altera modelo global
        res = self.client.post(
            "/api/models/set-default",
            json={"model": "gemini-2.0-flash", "provider": "gemini"},
        )
        self.assertEqual(res.status_code, 200)
        cfg_res = self.client.get("/api/config")
        self.assertEqual(cfg_res.json().get("model"), "gemini-2.0-flash")

        # 2. Altera modelo por tarefa (ex: vision)
        res2 = self.client.post(
            "/api/models/set-default",
            json={"model": "gpt-4o", "task": "vision"},
        )
        self.assertEqual(res2.status_code, 200)
        cfg_res2 = self.client.get("/api/config")
        self.assertEqual(cfg_res2.json().get("auxiliary_models", {}).get("vision"), "gpt-4o")

    def test_save_key_and_auth_persistence(self):
        res = self.client.post(
            "/api/providers/save-key",
            json={"provider": "anthropic", "api_key": "sk-ant-test-1234567890"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "saved")

        # Verifica se auth.json foi gravado com segurança
        auth_file = Path(self._tmp.name) / "auth.json"
        self.assertTrue(auth_file.exists())
        content = json.loads(auth_file.read_text(encoding="utf-8"))
        self.assertIn("anthropic", content.get("credential_pool", {}))

    def test_sessions_list_endpoint(self):
        res = self.client.get("/api/sessions")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("sessions", data)


class SessionTokenTests(unittest.TestCase):
    """O dashboard escuta em loopback, mas qualquer processo local alcança a
    porta — o token é o que separa a UI de um curl de outro usuário da máquina."""

    def setUp(self):
        self.anon = TestClient(app)
        self.auth = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})

    def test_token_is_not_the_old_hardcoded_constant(self):
        self.assertNotEqual(SESSION_TOKEN, "kairos-session-token")
        self.assertGreaterEqual(len(SESSION_TOKEN), 32)

    def test_api_rejects_missing_token(self):
        res = self.anon.get("/api/sessions")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"], "invalid_session_token")

    def test_api_rejects_wrong_token(self):
        res = self.anon.get("/api/sessions", headers={TOKEN_HEADER: "kairos-session-token"})
        self.assertEqual(res.status_code, 401)

    def test_api_accepts_token_via_query_param(self):
        res = self.anon.get("/api/sessions", params={"token": SESSION_TOKEN})
        self.assertEqual(res.status_code, 200)

    def test_health_stays_open_for_probes(self):
        self.assertEqual(self.anon.get("/api/health").status_code, 200)

    def test_auth_me_reports_the_live_token(self):
        res = self.auth.get("/api/auth/me")
        self.assertEqual(res.json()["token"], SESSION_TOKEN)

    def test_ws_ticket_reports_the_live_token(self):
        res = self.auth.post("/api/auth/ws-ticket")
        self.assertEqual(res.json()["ticket"], SESSION_TOKEN)

    def test_websocket_refuses_connection_without_token(self):
        from starlette.websockets import WebSocketDisconnect as WSDisconnect

        with self.assertRaises(WSDisconnect) as ctx, self.anon.websocket_connect("/ws/chat"):
            pass
        self.assertEqual(ctx.exception.code, 4401)

    def test_websocket_accepts_token_in_query(self):
        with self.auth.websocket_connect(f"/ws/chat?token={SESSION_TOKEN}") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            self.assertEqual(json.loads(ws.receive_text())["type"], "pong")


if __name__ == "__main__":
    unittest.main()
