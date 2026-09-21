"""Laço local Telegram->Kairos com Bot API fake (nenhuma rede real).

O teste sobe dois servidores locais de loopback na stdlib:

- ``fake api.telegram.org``: responde ``getMe``/``getUpdates``/``sendMessage``/
  ``sendChatAction`` e serve exatamente um ``update`` autenticado;
- ``fake LLM`` OpenAI-compatible: responde ``GET /v1/models`` e
  ``POST /v1/chat/completions`` (SSE) com uma resposta determinística e sem
  ``tool_calls``.

Com ``kairos_gateway.inbound._TELEGRAM_API`` apontando para o bot fake e um
home descartável (cofre com passphrase sintética, token de bot fake, provider
``custom`` selecionado), o canal de entrada REAL — transporte httpx, long-poll,
autenticação pela lista de autorizados, envio chunkado — percorre o laço
inteiro até o agente pelo caminho de produção:

    update -> TelegramInbound -> InteractionRouter real ->
    ProviderGateway real -> adapter custom -> LLM fake (SSE) ->
    delta -> sendMessage -> bot fake

Aqui o "agente" também é o caminho real até o provedor; apenas o provedor é um
stub determinístico. Nenhuma credencial real, nenhuma conexão externa.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from kairos_gateway.adapters.config import save_platform_secret
from kairos_providers.settings import update_config_document
from kairos_security.credentials import CredentialRef, CredentialSecret, build_credential_service

LLM_MODEL = "stub-chat"
LLM_REPLY = "Conexao telegram->kairos validada localmente (Bot API fake)."
FAKE_LLM_KEY = "chave-fake"
FAKE_TOKEN = "tk-fake"  # noqa: S105 - fixture sintética de teste
TG_USER = 777888999
TG_CHAT = 987654321
UPDATE_ID = 41


def _completions_sse(reply: str) -> str:
    content = json.dumps(
        {
            "id": "chatcmpl-stub-1",
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
            "id": "chatcmpl-stub-1",
            "object": "chat.completion.chunk",
            "model": LLM_MODEL,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    usage = json.dumps(
        {
            "id": "chatcmpl-stub-1",
            "object": "chat.completion.chunk",
            "model": LLM_MODEL,
            "choices": [],
            "usage": {"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12},
        }
    )
    return "\n\n".join(
        [f"data: {content}", f"data: {finish}", f"data: {usage}", "data: [DONE]", ""]
    )


def _update(*, update_id: int, user_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "Teste",
                "language_code": "pt-br",
            },
            "chat": {"id": chat_id, "type": "group", "title": "Teste local"},
            "date": 1730000000,
            "text": text,
        },
    }


@dataclass
class _FakeState:
    """Estado compartilhado entre o servidor fake e a asserção do teste."""

    pending_updates: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    completions: list[dict] = field(default_factory=list)
    sse_reply: str = field(default_factory=lambda: _completions_sse(LLM_REPLY))
    get_me: int = 0
    get_updates: int = 0
    llm_models: int = 0
    llm_chat: int = 0

    def record(self, method: str, **values: object) -> None:
        self.records.append({"method": method, **values})

    async def wait_send_message(self, *, timeout: float = 30.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if any(record["method"] == "sendMessage" for record in self.records):
                return
            await asyncio.sleep(0.05)
        raise AssertionError("nenhum sendMessage registrado no bot fake dentro do prazo")

    async def wait_get_updates(self, *, timeout: float = 30.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.get_updates >= 1:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("nenhuma chamada getUpdates no bot fake dentro do prazo")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def _state(self) -> _FakeState:
        return cast(_LocalServer, self.server).state

    # --- helpers de resposta ----------------------------------------------

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

    # --- rotas -------------------------------------------------------------

    def do_GET(self) -> None:
        state = self._state
        path = urlparse(self.path).path
        if path == "/v1/models":
            state.llm_models += 1
            self._json(
                {
                    "object": "list",
                    "data": [{"id": LLM_MODEL, "object": "model", "display_name": "Stub local"}],
                }
            )
            return
        if "getUpdates" in path:
            state.get_updates += 1
            query = parse_qs(urlparse(self.path).query)
            offset = int(query.get("offset", ["0"])[0])
            updates = [u for u in state.pending_updates if u["update_id"] >= offset]
            state.pending_updates = []
            self._json({"ok": True, "result": updates})
            return
        self._json({"ok": True, "result": []})

    def do_POST(self) -> None:
        state = self._state
        path = urlparse(self.path).path
        if "getMe" in path:
            state.get_me += 1
            self._json(
                {
                    "ok": True,
                    "result": {
                        "id": 1,
                        "is_bot": True,
                        "first_name": "kairos-stub-bot",
                        "username": "kairos_stub_bot",
                    },
                }
            )
            return
        payload = self._read_json()
        if "chat/completions" in path:
            state.llm_chat += 1
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
        if "sendChatAction" in path:
            state.record("sendChatAction", chat_id=payload.get("chat_id"))
            self._json({"ok": True, "result": True})
            return
        if "answerCallbackQuery" in path:
            state.record("answerCallbackQuery")
            self._json({"ok": True, "result": True})
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


def _configure_vault(home: Path, passphrase_file: Path) -> None:
    passphrase_file.write_text("senha-mestra-teste\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    service = build_credential_service(home)
    service.put(CredentialRef("custom", "primary"), CredentialSecret({"api_key": FAKE_LLM_KEY}))
    save_platform_secret(home, "telegram", FAKE_TOKEN)


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
            "inbound": {
                "enabled": True,
                "allowed_user_ids": [TG_USER],
                "poll_interval_seconds": 0.5,
                "experiences": False,
                "mode": "poll",
            },
        }
    )
    save_config(home, doc)


async def _drive(home: Path, state: _FakeState, *, wait_reply: bool) -> None:
    from kairos_gateway.inbound import TelegramInbound, stop_telegram_inbound
    from kairos_integration.composition import build_interaction_router

    router = build_interaction_router(home)
    canal = TelegramInbound(home, router)
    task = asyncio.create_task(canal.run())
    try:
        if wait_reply:
            await state.wait_send_message()
        else:
            await state.wait_get_updates()
            await asyncio.sleep(0.8)
    finally:
        stop_telegram_inbound(home)
        await asyncio.wait_for(task, timeout=20)
        await router.aclose()


def _launch(home: Path, state: _FakeState, *, wait_reply: bool) -> None:
    asyncio.run(_drive(home, state, wait_reply=wait_reply))


def _servidores() -> tuple[_LocalServer, _LocalServer]:
    bot = _LocalServer(_FakeState())
    llm = _LocalServer(_FakeState())
    return bot, llm


def test_laco_local_telegram_agente_com_bot_api_fake(tmp_path, monkeypatch):
    """Uma mensagem de um usuário autorizado percorre o laço inteiro e recebe
    a resposta determinística do provedor fake de volta pelo bot fake."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    passphrase = tmp_path / "passphrase"
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    _configure_vault(home, passphrase)

    bot, llm = _servidores()
    try:
        monkeypatch.setattr("kairos_gateway.inbound._TELEGRAM_API", f"{bot.url()}/bot{{token}}")
        bot.state.pending_updates = [
            _update(
                update_id=UPDATE_ID,
                user_id=TG_USER,
                chat_id=TG_CHAT,
                text="Enviar para o agente",
            )
        ]
        _configure_home(home, llm.url())

        _launch(home, bot.state, wait_reply=True)

        enviadas = [r for r in bot.state.records if r["method"] == "sendMessage"]
        assert enviadas, "o bot fake não recebeu nenhuma sendMessage"
        assert enviadas[-1]["chat_id"] == TG_CHAT
        assert enviadas[-1]["text"] == LLM_REPLY
        assert bot.state.get_me >= 1
        assert llm.state.llm_chat == 1, "o provedor fake não foi chamado para o turno"
        assert llm.state.completions[0]["model"] == LLM_MODEL
        state_path = home / "inbound-telegram.json"
        assert state_path.exists()
        assert json.loads(state_path.read_text(encoding="utf-8"))["consumed"] == UPDATE_ID
    finally:
        bot.shutdown()
        llm.shutdown()


def test_remetente_fora_da_lista_nao_fala_com_o_agente(tmp_path, monkeypatch):
    """Fail-closed: um usuário fora de ``allowed_user_ids`` não vira turno e não
    gera resposta nem chamada ao provedor."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    passphrase = tmp_path / "passphrase"
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    _configure_vault(home, passphrase)

    bot, llm = _servidores()
    try:
        monkeypatch.setattr("kairos_gateway.inbound._TELEGRAM_API", f"{bot.url()}/bot{{token}}")
        bot.state.pending_updates = [
            _update(
                update_id=UPDATE_ID,
                user_id=TG_USER + 1,
                chat_id=TG_CHAT,
                text="quem fala aqui nao esta autorizado",
            )
        ]
        _configure_home(home, llm.url())

        _launch(home, bot.state, wait_reply=False)

        assert not [r for r in bot.state.records if r["method"] == "sendMessage"]
        assert llm.state.llm_chat == 0, "provedor chamado mesmo para remetente não autorizado"
        state_path = home / "inbound-telegram.json"
        assert state_path.exists()
        assert json.loads(state_path.read_text(encoding="utf-8"))["consumed"] == UPDATE_ID
    finally:
        bot.shutdown()
        llm.shutdown()
