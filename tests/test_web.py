"""Testes para o servidor FastAPI e rotas da interface web do Kairos."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from kairos_web.server import app


class WebServerApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name
        self.client = TestClient(app)

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


if __name__ == "__main__":
    unittest.main()
