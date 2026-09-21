"""Entrega externa do cron por canal — harness local offline (nenhuma rede real).

Fecha o ponto declarado "entrega a canais" da entrega externa do cron sem
credencial nem rede externa: um job agendado com ``delivery`` roda o turno pelo
caminho real (interaction service real + LLM fake determinístico) e a obrigação
durável que nasce no ledger é drenada pelo processo do gateway ``kairos
gateway`` — ``GatewayService`` real com o ``TelegramAdapter`` real construído do
mesmo home (config + cofre) — contra um servidor de Bot API fake em loopback.

Únicos pontos "fingidos": o endpoint do provedor e o da Bot API (monkeypatch de
``kairos_gateway.adapters.telegram._TELEGRAM_API``), exatamente como no harness
de entrada Telegram→Kairos. O turno, o ledger durável, o claim cross-process e o
envelope HTTP do adapter são o código de produção.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.parse import urlparse

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler
from kairos_gateway.adapters import build_platform_adapters
from kairos_gateway.adapters.config import save_platform_secret
from kairos_gateway.service import GatewayService
from kairos_integration.composition import build_interaction_service
from kairos_providers.settings import update_config_document
from kairos_security.credentials import CredentialRef, CredentialSecret, build_credential_service
from kairos_state import connect, migrate
from kairos_state.repositories.ledger import LedgerRepository

LLM_MODEL = "stub-chat"
CRON_REPLY = "Relatorio diario gerado e entregue pelo cron."
FAKE_LLM_KEY = "chave-fake"
FAKE_TOKEN = "tk-fake"  # noqa: S105 - fixture sintética de teste
TG_CHAT = 987654321
NOW = datetime(2026, 9, 21, 9, tzinfo=UTC)


def _completions_sse(reply: str) -> str:
    content = json.dumps(
        {
            "id": "chatcmpl-stub-cron-1",
            "object": "chat.completion.chunk",
            "model": LLM_MODEL,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": reply},
                    "finish_reason": None,
                }
            ],
        }
    )
    finish = json.dumps(
        {
            "id": "chatcmpl-stub-cron-1",
            "object": "chat.completion.chunk",
            "model": LLM_MODEL,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    usage = json.dumps(
        {
            "id": "chatcmpl-stub-cron-1",
            "object": "chat.completion.chunk",
            "model": LLM_MODEL,
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 7, "total_tokens": 9},
        }
    )
    return "\n\n".join(
        [f"data: {content}", f"data: {finish}", f"data: {usage}", "data: [DONE]", ""]
    )


class _FakeState:
    """Estado compartilhado entre o servidor fake e a asserção do teste."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.completions: list[dict] = []
        self.sse_reply: str = _completions_sse(CRON_REPLY)

    def record(self, method: str, **values: object) -> None:
        self.records.append({"method": method, **values})


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def _state(self) -> _FakeState:
        return cast(_LocalServer, self.server).state

    def _json(self, document: object) -> None:
        body = json.dumps(document).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, payload: str) -> None:
        body = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:
        if "/v1/models" in urlparse(self.path).path:
            self._json(
                {
                    "object": "list",
                    "data": [{"id": LLM_MODEL, "object": "model", "display_name": "Stub local"}],
                }
            )
            return
        self._json({"ok": True, "result": []})

    def do_POST(self) -> None:
        state = self._state
        path = urlparse(self.path).path
        if "getMe" in path:
            self._json(
                {
                    "ok": True,
                    "result": {
                        "id": 1,
                        "is_bot": True,
                        "first_name": "kairos-cron-stub-bot",
                        "username": "kairos_cron_stub_bot",
                    },
                }
            )
            return
        payload = self._read_json()
        if "chat/completions" in path:
            state.completions.append(payload)
            self._sse(state.sse_reply)
            return
        if "sendMessage" in path:
            state.record("sendMessage", chat_id=payload.get("chat_id"), text=payload.get("text"))
            self._json(
                {
                    "ok": True,
                    "result": {
                        "message_id": 1,
                        "chat": {"id": payload.get("chat_id")},
                        "text": payload.get("text"),
                    },
                }
            )
            return
        self._json({"ok": True, "result": {}})


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, state: _FakeState) -> None:
        self.state = state
        self._thread = Thread(target=self.serve_forever, daemon=True)
        super().__init__(("127.0.0.1", 0), _Handler)
        self._thread.start()

    def url(self, path: str = "") -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}{path}"

    def shutdown(self) -> None:
        super().shutdown()
        self.server_close()
        self._thread.join(timeout=5)


def _configure_vault(
    home: Path, passphrase_file: Path, *, telegram_token: str = FAKE_TOKEN
) -> None:
    passphrase_file.write_text("senha-mestra-teste\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    service = build_credential_service(home)
    service.put(CredentialRef("custom", "primary"), CredentialSecret({"api_key": FAKE_LLM_KEY}))
    if telegram_token:
        save_platform_secret(home, "telegram", telegram_token)


def _configure_home(home: Path, llm_url: str) -> None:
    from kairos_gateway.adapters.config import load_config, save_config

    update_config_document(
        home,
        lambda document: document.update(
            {
                "provider": "custom",
                "model": LLM_MODEL,
                "provider_settings": {"custom": {"base_url": f"{llm_url}/v1"}},
            }
        ),
    )
    doc = load_config(home)
    doc["telegram"].update(
        {
            "enabled": True,
            "chat_id_default": "",
            "inbound": {
                "enabled": False,
                "allowed_user_ids": [],
                "poll_interval_seconds": 2.0,
                "experiences": False,
                "mode": "poll",
            },
        }
    )
    save_config(home, doc)


def _pending(home: Path):
    db = connect(home / "state.db")
    try:
        migrate(db)
        return LedgerRepository(db).pending()
    finally:
        db.close()


async def _run_cron_turn(home: Path) -> None:
    service = build_interaction_service(home)
    try:
        await Scheduler(home, service).tick(now=NOW)
    finally:
        await service.aclose()


def test_cron_entrega_externa_telegram_percorre_o_caminho_de_producao(tmp_path, monkeypatch):
    """Um job com ``delivery`` vira obrigação no ledger e o gateway drena para o
    canal real, com o adapter real, até o bot fake — nenhuma rede externa."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    passphrase = tmp_path / "passphrase"
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    _configure_vault(home, passphrase)

    bot = _LocalServer(_FakeState())
    llm = _LocalServer(_FakeState())
    gateway_conn = None
    try:
        monkeypatch.setattr(
            "kairos_gateway.adapters.telegram._TELEGRAM_API", f"{bot.url()}/bot{{token}}"
        )
        _configure_home(home, llm.url())

        store = JobStore(home)
        store.create(
            name="Relatório diário",
            prompt="Gere o relatório diário.",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
            delivery={"target": f"telegram:{TG_CHAT}"},
        )

        asyncio.run(_run_cron_turn(home))

        pendentes = _pending(home)
        assert len(pendentes) == 1, "o turno completado deveria ter gerado uma obrigação"
        assert pendentes[0].target == f"telegram:{TG_CHAT}"
        assert pendentes[0].payload == CRON_REPLY

        gateway_conn = connect(home / "state.db")
        migrate(gateway_conn)
        svc = GatewayService(home, conn=gateway_conn)
        for adapter in build_platform_adapters(home).values():
            svc.register_adapter(adapter)
        assert "telegram" in cast(list, svc.status()["adapters"])

        entregues = svc.tick()

        assert entregues == 1
        assert svc.ledger.counts_by_state()["delivered"] == 1
        enviadas = [r for r in bot.state.records if r["method"] == "sendMessage"]
        assert enviadas, "o bot fake não recebeu a entrega do cron"
        assert enviadas[-1]["chat_id"] == str(TG_CHAT)
        assert enviadas[-1]["text"] == CRON_REPLY
        assert llm.state.completions[0]["model"] == LLM_MODEL
    finally:
        if gateway_conn is not None:
            gateway_conn.close()
        bot.shutdown()
        llm.shutdown()


def test_cron_sem_adapter_registrado_nao_finge_entrega(tmp_path, monkeypatch):
    """Fail-closed: sem adapter (telegram habilitado mas sem segredo no cofre), o
    gateway não entrega em silêncio — a obrigação continua pendente e nada chega
    ao canal."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    passphrase = tmp_path / "passphrase"
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    _configure_vault(home, passphrase, telegram_token="")

    bot = _LocalServer(_FakeState())
    llm = _LocalServer(_FakeState())
    gateway_conn = None
    try:
        monkeypatch.setattr(
            "kairos_gateway.adapters.telegram._TELEGRAM_API", f"{bot.url()}/bot{{token}}"
        )
        _configure_home(home, llm.url())

        store = JobStore(home)
        store.create(
            name="Relatório diário",
            prompt="Gere o relatório diário.",
            schedule={"kind": "once", "run_at": NOW.isoformat()},
            delivery={"target": f"telegram:{TG_CHAT}"},
        )

        asyncio.run(_run_cron_turn(home))
        assert len(_pending(home)) == 1

        gateway_conn = connect(home / "state.db")
        migrate(gateway_conn)
        svc = GatewayService(home, conn=gateway_conn)
        assert cast(list, svc.status()["adapters"]) == [], "sem segredo no cofre não há adapter"

        entregues = svc.tick()

        assert entregues == 0
        assert svc.ledger.counts_by_state()["delivered"] == 0
        assert svc.ledger.counts_by_state()["pending"] == 1
        assert not [r for r in bot.state.records if r["method"] == "sendMessage"]
    finally:
        if gateway_conn is not None:
            gateway_conn.close()
        bot.shutdown()
        llm.shutdown()
