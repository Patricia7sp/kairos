"""Testes reais de scripts/komodo-deploy.sh e scripts/smoke-deploy.sh.

Rodam os scripts de verdade via subprocess contra um servidor HTTP fake que
imita a API REST do Komodo (POST /execute/DeployStack e /read/GetUpdate) e o
/api/health da stack — sem tocar o Komodo de produção nem uma URL pública.

É o mesmo padrão de tests/test_compose_config.py: o comportamento em teste é o
script shell executando, não a lógica duplicada em Python.
"""

import json
import os
import socket
import subprocess
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KOMODO_DEPLOY = ROOT / "scripts" / "komodo-deploy.sh"
SMOKE_DEPLOY = ROOT / "scripts" / "smoke-deploy.sh"

API_KEY = "K-teste-de-suite"


def update_for(logic):
    """Monta o Update JSON do estado atual do fake."""
    return {
        "_id": {"$oid": logic.update_id},
        "operation": "DeployStack",
        "status": logic.current_status,
        "success": logic.current_success,
        "operator": "github",
        "target": {"type": "stack", "name": logic.stack_name, "id": logic.stack_name},
        "start_ts": 1,
        "end_ts": None if logic.current_status != "Complete" else 2,
        "logs": [
            {"stage": "Deploy", "success": True, "content": "estágio de teste"},
        ],
        "version": "",
        "commit_hash": "c" * 40,
        "other_data": "",
        "prev_toml": "",
        "current_toml": "",
    }


class DeployLogic:
    """Máquina de estados: o disparo devolve Queued e cada GetUpdate avança a
    transição seguinte da lista; esgotada a lista, o último estado fica fixo
    (como o Komodo mantendo o Update final)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.current_status = "Queued"
        self.current_success = False
        self.update_id = "f" * 24
        self.stack_name = None
        self.executions = 0
        self.polls = 0

    def advance(self):
        if not self.steps:
            return
        self.current_status, self.current_success = self.steps.pop(0)


class FakeServer(ThreadingHTTPServer):
    """Server tipado: o handler acessa os atributos direto (sem getattr)."""

    logic: DeployLogic
    health: Callable[[], object] | None
    health_code: int
    health_raw: bytes | None


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FakeServer

    def log_message(self, format, *args):  # silencia o log de acesso do stdlib
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    def do_GET(self):
        raw = self.server.health_raw
        if self.path == "/api/health" and raw is not None:
            self.send_response(self.server.health_code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        health = self.server.health
        if self.path == "/api/health" and health is not None:
            self._send(self.server.health_code, health())
            return
        self._send(404, {"error": "não encontrado"})

    def do_POST(self):
        logic = self.server.logic
        if self.headers.get("X-Api-Key") != API_KEY:
            self._send(401, {"error": "api key inválida"})
            return
        body = self._json_body()
        if self.path == "/execute/DeployStack":
            logic.executions += 1
            logic.stack_name = body.get("stack")
            self._send(200, update_for(logic))
        elif self.path == "/read/GetUpdate":
            logic.polls += 1
            logic.advance()
            self._send(200, update_for(logic))
        else:
            self._send(404, {"error": "rota desconhecida"})


@pytest.fixture
def fake_server():
    """Sobe um servidor fake e devolve uma fábrica server+logic; derruba no fim."""

    started = []

    def start(logic=None, health=None, health_code=200, health_raw=None):
        server = FakeServer(("127.0.0.1", 0), FakeHandler)
        server.logic = logic or DeployLogic([("Complete", True)])
        server.health = health
        server.health_code = health_code
        server.health_raw = health_raw
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append(server)
        port = server.server_address[1]
        return f"http://127.0.0.1:{port}", server.logic

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def run_deploy(base_url, logic, *, stack="kairos", key=API_KEY, timeout="900", interval="1"):
    env = {
        **os.environ,
        "KOMODO_HOST": base_url,
        "KOMODO_API_KEY": key,
        "KOMODO_STACK": stack,
        "KOMODO_DEPLOY_TIMEOUT": timeout,
        "KOMODO_POLL_INTERVAL": interval,
    }
    return subprocess.run(
        [str(KOMODO_DEPLOY)], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def run_smoke(url):
    env = {**os.environ, "KAIROS_HEALTH_URL": url}
    return subprocess.run(
        [str(SMOKE_DEPLOY)], env=env, capture_output=True, text=True, timeout=30, check=False
    )


class TestKomodoDeploy:
    @pytest.mark.parametrize("stack", ["kairos", "kairos-staging"])
    def test_deploy_ok_aguarda_o_update_e_reporta_sucesso(self, fake_server, stack):
        base_url, logic = fake_server(DeployLogic([("InProgress", False), ("Complete", True)]))
        result = run_deploy(base_url, logic, stack=stack)

        assert result.returncode == 0, result.stderr
        assert "deploy ok" in result.stdout
        assert logic.executions == 1
        assert logic.stack_name == stack
        assert logic.polls >= 2

    def test_deploy_falho_fecha_com_erro_e_mostra_logs(self, fake_server):
        base_url, logic = fake_server(DeployLogic([("InProgress", False), ("Complete", False)]))
        result = run_deploy(base_url, logic)

        assert result.returncode == 1
        assert "deploy falhou" in result.stderr
        assert "estágio de teste" in result.stderr

    def test_deploy_que_nao_termina_fecha_por_timeout(self, fake_server):
        base_url, logic = fake_server(DeployLogic([("InProgress", False)]))
        result = run_deploy(base_url, logic, timeout="3", interval="1")

        assert result.returncode == 1
        assert "não terminou em 3s" in result.stderr

    def test_deploy_recusado_por_chave_invalida(self, fake_server):
        base_url, logic = fake_server()
        result = run_deploy(base_url, logic, key="K-errada")

        assert result.returncode == 1
        assert "HTTP 401" in result.stderr

    def test_deploy_sem_gatilho_falha_como_erro_de_configuracao(self, fake_server):
        base_url, _logic = fake_server()
        env = {
            **os.environ,
            "KOMODO_HOST": base_url,
            "KOMODO_DEPLOY_TIMEOUT": "3",
            "KOMODO_POLL_INTERVAL": "1",
        }
        env.pop("KOMODO_API_KEY", None)
        result = subprocess.run(
            [str(KOMODO_DEPLOY)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 1
        assert "KOMODO_API_KEY ausente" in result.stderr

    def test_deploy_sem_host_falha_como_erro_de_configuracao(self):
        env = {**os.environ, "KOMODO_API_KEY": API_KEY}
        env.pop("KOMODO_HOST", None)
        result = subprocess.run(
            [str(KOMODO_DEPLOY)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 1
        assert "KOMODO_HOST ausente" in result.stderr


class TestSmokeDeploy:
    def test_smoke_ok_quando_health_responde_saudavel(self, fake_server):
        base_url, _logic = fake_server(health=lambda: {"status": "ok", "app": "kairos"})
        result = run_smoke(f"{base_url}/api/health")

        assert result.returncode == 0, result.stderr
        assert "smoke ok: ok" in result.stdout

    def test_smoke_fecha_quando_status_divergente(self, fake_server):
        base_url, _logic = fake_server(health=lambda: {"status": "degraded"})
        result = run_smoke(f"{base_url}/api/health")

        assert result.returncode == 1
        assert '"degraded"' in result.stderr

    def test_smoke_fecha_com_endpoint_fora_do_ar(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        result = run_smoke(f"http://127.0.0.1:{port}/api/health")

        assert result.returncode == 1
        assert "não respondeu" in result.stderr

    def test_smoke_fecha_com_corpo_invalido(self, fake_server):
        base_url, _logic = fake_server(health_raw=b"<html>proxy baixou</html>", health_code=200)
        result = run_smoke(f"{base_url}/api/health")

        assert result.returncode == 1
        assert "JSON" in result.stderr

    def test_smoke_respeita_esperado_personalizado(self, fake_server):
        base_url, _logic = fake_server(health=lambda: {"status": "compartilhado", "app": "kairos"})
        env = {
            **os.environ,
            "KAIROS_HEALTH_URL": f"{base_url}/api/health",
            "KAIROS_EXPECTED_STATUS": "compartilhado",
        }
        result = subprocess.run(
            [str(SMOKE_DEPLOY)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "smoke ok: compartilhado" in result.stdout
