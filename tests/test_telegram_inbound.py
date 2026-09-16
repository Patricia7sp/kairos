"""Canal de entrada Telegram: updates da Bot API viram turnos do agente.

Fecha o laço que o gateway não tinha: mensagens recebidas passam pela mesma
autenticação fail-closed (allowlist) e pelo mesmo ``InteractionRouter`` das
outras superfícies. Tudo via ``httpx.MockTransport`` — nenhuma rede real.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from kairos_gateway.adapters.config import save_config
from kairos_gateway.inbound import (
    TelegramChannel,
    TelegramError,
    TelegramInbound,
    TelegramUpdate,
    _chunk_text,
    _parse_update,
)

FAKE_ACCOUNT = "123456:ABC-DEF"
CHAT = 987654321
AUTH_USER = 111222333
STRANGER = 999000111


class FakeCall:
    def __init__(self, name: str, arguments: str = "") -> None:
        self.name = name
        self.arguments = arguments


class FakeEvent:
    def __init__(self, kind: str, **kwargs: Any) -> None:
        self.kind = kind
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeRouter:
    def __init__(self, events: list[Any] | None = None) -> None:
        self.events = list(events or [])
        self.envelopes: list[Any] = []
        self.decisions: list[tuple[str, str, str]] = []

    async def stream(self, envelope: Any) -> Any:
        self.envelopes.append(envelope)
        for event in self.events:
            yield event
        yield FakeEvent("turn_end")

    def decide_tool_approval(self, *, approval_id: str, session_id: str, decision: str) -> None:
        self.decisions.append((approval_id, session_id, decision))


def make_handler(log: list[tuple[str, str, dict]] | None = None, *, extra: None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        if log is not None:
            log.append((request.method, str(request.url.path), payload))
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "kairos_test_bot"}})
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": []})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/sendChatAction"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"ok": False, "description": "endpoint desconhecido"})

    return handler


def message_update(update_id: int, text: str, *, user_id: int = AUTH_USER) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "chat": {"id": CHAT, "type": "private"},
            "from": {"id": user_id},
            "text": text,
        },
    }


class ParseTests(unittest.TestCase):
    def test_mensagem_normal(self):
        u = _parse_update(message_update(7, "oi"))
        self.assertIsNotNone(u)
        assert u is not None
        self.assertEqual(u.update_id, 7)
        self.assertEqual(u.chat_id, CHAT)
        self.assertEqual(u.user_id, AUTH_USER)
        self.assertEqual(u.text, "oi")

    def test_callback_query(self):
        raw = {
            "update_id": 8,
            "callback_query": {
                "id": "cq1",
                "from": {"id": AUTH_USER},
                "data": "ka:ap1:allow",
                "message": {"message_id": 3, "chat": {"id": CHAT}},
            },
        }
        u = _parse_update(raw)
        self.assertIsNotNone(u)
        assert u is not None
        self.assertEqual(u.callback_data, "ka:ap1:allow")
        self.assertEqual(u.callback_chat_id, CHAT)
        self.assertEqual(u.chat_id, CHAT)

    def test_update_irrelevante_ignorado(self):
        self.assertIsNone(_parse_update({"update_id": 9}))
        self.assertIsNone(_parse_update({"message": {"message_id": 1}}))
        self.assertIsNone(_parse_update({}))

    def test_chunk_text_segmenta_sem_perder_dados(self):
        self.assertEqual(_chunk_text("oi"), ["oi"])
        frag = _chunk_text("a" * 9000, limit=100)
        self.assertGreater(len(frag), 1)
        self.assertTrue(all(len(p) <= 100 for p in frag))
        self.assertEqual("".join(frag), "a" * 9000)


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_get_me(self):
        channel = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler()))
        )
        info = await channel.verify()
        self.assertTrue(info["ok"])
        self.assertIn("kairos_test_bot", info["bot"])

    async def test_erro_de_api_e_devidamente_classificado(self):
        def failing(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request"})

        channel = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(failing))
        )
        with self.assertRaises(TelegramError):
            await channel.verify()

    async def test_falha_de_rede_e_devidamente_classificada(self):
        def offline(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline")

        channel = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(offline))
        )
        with self.assertRaises(TelegramError):
            await channel.verify()


class InboundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["KAIROS_DISABLE_KEYRING"] = "1"

    async def asyncTearDown(self) -> None:
        os.environ.pop("KAIROS_DISABLE_KEYRING", None)
        self._tmp.cleanup()

    def _write_config(
        self,
        *,
        enabled: bool = True,
        allowed: tuple[int, ...] = (AUTH_USER,),
        experiences: bool = False,
    ) -> None:
        save_config(
            self.home,
            {
                "telegram": {
                    "enabled": True,
                    "chat_id_default": "",
                    "inbound": {
                        "enabled": enabled,
                        "allowed_user_ids": list(allowed),
                        "poll_interval_seconds": 1.0,
                        "experiences": experiences,
                    },
                },
                "whatsapp": {"enabled": False, "phone_number_id": "", "number_default": ""},
                "slack": {"enabled": False, "channel_default": ""},
                "webhook": {"enabled": False, "endpoints": []},
            },
        )

    def _make(
        self,
        router: FakeRouter | None = None,
        channel: TelegramChannel | None = None,
        *,
        enabled: bool = True,
        allowed: tuple[int, ...] = (AUTH_USER,),
        token: str | None = FAKE_ACCOUNT,
        experiences: bool = False,
    ) -> tuple[TelegramInbound, FakeRouter]:
        self._write_config(enabled=enabled, allowed=allowed, experiences=experiences)
        fake = router or FakeRouter()
        chan = channel or TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler()))
        )
        inbound = TelegramInbound(self.home, fake, token=token, channel=chan)
        return inbound, fake

    async def test_allowlist_vazia_derruba_turnos(self):
        inbound, fake = self._make(allowed=())
        await inbound._dispatch(
            TelegramUpdate(update_id=10, message_id=1, chat_id=CHAT, user_id=AUTH_USER, text="oi")
        )
        self.assertEqual(fake.envelopes, [])

    async def test_remetente_nao_autorizado_ignorado(self):
        inbound, fake = self._make()
        await inbound._dispatch(
            TelegramUpdate(update_id=11, message_id=2, chat_id=CHAT, user_id=STRANGER, text="oi")
        )
        self.assertEqual(fake.envelopes, [])

    async def test_canal_desabilitado_ignora_apos_config(self):
        inbound, fake = self._make(enabled=False)
        await inbound._dispatch(
            TelegramUpdate(update_id=12, message_id=3, chat_id=CHAT, user_id=AUTH_USER, text="oi")
        )
        self.assertEqual(fake.envelopes, [])

    async def test_turno_envelope_vai_para_o_router(self):
        fake = FakeRouter(
            events=[FakeEvent("delta", text="Olá "), FakeEvent("delta", text="mundo")]
        )
        log: list[tuple[str, str, dict]] = []
        chan = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(log)))
        )
        inbound, _ = self._make(fake, chan)
        await inbound._dispatch(
            TelegramUpdate(update_id=13, message_id=4, chat_id=CHAT, user_id=AUTH_USER, text="oi")
        )
        self.assertEqual(len(fake.envelopes), 1)
        env = fake.envelopes[0]
        self.assertEqual(env.conversation_id, f"telegram:{CHAT}")
        self.assertEqual(env.source, "telegram")
        self.assertEqual(env.idempotency_key, "telegram:13")
        self.assertEqual(env.content, "oi")
        self.assertTrue(env.tools)
        self.assertTrue(env.web_search)
        envios = [p for _, path, p in log if path.endswith("/sendMessage")]
        self.assertTrue(any(p.get("text") == "Olá mundo" for p in envios))

    async def test_turno_com_erro_entrega_mensagem_de_falha(self):
        fake = FakeRouter(events=[FakeEvent("turn_error", error="provedor fora")])
        log: list[tuple[str, str, dict]] = []
        chan = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(log)))
        )
        inbound, _ = self._make(fake, chan)
        await inbound._dispatch(
            TelegramUpdate(update_id=14, message_id=5, chat_id=CHAT, user_id=AUTH_USER, text="oi")
        )
        envios = [p for _, path, p in log if path.endswith("/sendMessage")]
        self.assertTrue(any("provedor fora" in p.get("text", "") for p in envios))

    async def test_aprovacao_emite_teclado_inline(self):
        fake = FakeRouter(
            events=[
                FakeEvent(
                    "tool_approval_request",
                    tool_approval_id="ap1",
                    tool_call=FakeCall(name="bash", arguments="rm -rf /tmp/x"),
                )
            ]
        )
        log: list[tuple[str, str, dict]] = []
        chan = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(log)))
        )
        inbound, _ = self._make(fake, chan)
        await inbound._dispatch(
            TelegramUpdate(
                update_id=15, message_id=6, chat_id=CHAT, user_id=AUTH_USER, text="limpe"
            )
        )
        envios = [p for _, path, p in log if path.endswith("/sendMessage")]
        aprova = [p for p in envios if p.get("reply_markup")]
        self.assertEqual(len(aprova), 1)
        dados = aprova[0]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["callback_data"] for b in dados], ["ka:ap1:allow", "ka:ap1:deny"])

    async def test_callback_allow_decide_e_responde(self):
        inbound, fake = self._make()
        await inbound._dispatch(
            TelegramUpdate(
                update_id=16,
                callback_data="ka:ap1:allow",
                callback_query_id="cq1",
                callback_chat_id=CHAT,
                user_id=AUTH_USER,
            )
        )
        self.assertEqual(fake.decisions, [("ap1", f"telegram:{CHAT}", "allow")])

    async def test_callback_deny_decide_e_responde(self):
        inbound, fake = self._make()
        await inbound._dispatch(
            TelegramUpdate(
                update_id=17,
                callback_data="ka:ap1:deny",
                callback_query_id="cq2",
                callback_chat_id=CHAT,
                user_id=AUTH_USER,
            )
        )
        self.assertEqual(fake.decisions, [("ap1", f"telegram:{CHAT}", "deny")])

    async def test_callback_desconhecida_ignorada(self):
        inbound, fake = self._make()
        await inbound._dispatch(
            TelegramUpdate(
                update_id=18,
                callback_data="lixo",
                callback_query_id="cq3",
                callback_chat_id=CHAT,
                user_id=AUTH_USER,
            )
        )
        self.assertEqual(fake.decisions, [])

    async def test_slash_start_envia_ajuda_sem_turno(self):
        log: list[tuple[str, str, dict]] = []
        chan = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(log)))
        )
        inbound, fake = self._make(channel=chan)
        await inbound._dispatch(
            TelegramUpdate(
                update_id=19, message_id=7, chat_id=CHAT, user_id=AUTH_USER, text="/start"
            )
        )
        self.assertEqual(fake.envelopes, [])
        envios = [p for _, path, p in log if path.endswith("/sendMessage")]
        self.assertTrue(any("Kairos Agent" in p.get("text", "") for p in envios))

    async def test_typing_enviado_durante_o_turno(self):
        class SlowRouter(FakeRouter):
            async def stream(self, envelope: Any) -> Any:
                self.envelopes.append(envelope)
                yield FakeEvent("delta", text="processando...")
                await asyncio.sleep(0.01)
                yield FakeEvent("turn_end")

        fake_stream = SlowRouter()
        log: list[tuple[str, str, dict]] = []
        chan = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(log)))
        )
        inbound, _ = self._make(fake_stream, chan)
        await inbound._dispatch(
            TelegramUpdate(
                update_id=20, message_id=8, chat_id=CHAT, user_id=AUTH_USER, text="relatório"
            )
        )
        acoes = [p for _, path, p in log if path.endswith("/sendChatAction")]
        self.assertTrue(
            any(p.get("chat_id") == CHAT and p.get("action") == "typing" for p in acoes)
        )

    async def test_saida_longa_fica_chunkada(self):
        corpo = "x" * 9000
        fake = FakeRouter(events=[FakeEvent("delta", text=corpo)])
        log: list[tuple[str, str, dict]] = []
        chan = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(log)))
        )
        inbound, _ = self._make(fake, chan)
        await inbound._dispatch(
            TelegramUpdate(update_id=21, message_id=9, chat_id=CHAT, user_id=AUTH_USER, text="puxe")
        )
        envios = [p for _, path, p in log if path.endswith("/sendMessage")]
        textos = [p["text"] for p in envios if p.get("text")]
        self.assertGreater(len(textos), 1)
        self.assertTrue(all(len(t) <= 4000 for t in textos))
        self.assertEqual("".join(textos), corpo)

    async def test_drain_marker_encerra_o_loop(self):
        inbound, _ = self._make()
        (self.home / ".drain_request.json").write_text("{}", encoding="utf-8")
        await inbound._poll_loop()  # não deve levantar

    async def test_offset_avanca_e_persiste(self):
        calls: list[int] = []

        def scripted(request: httpx.Request) -> httpx.Response:
            if not request.url.path.endswith("/getUpdates"):
                return make_handler()(request)
            offset = int(request.url.params.get("offset", 1))
            calls.append(offset)
            if offset == 1:
                return httpx.Response(200, json={"ok": True, "result": [message_update(1, "oi")]})
            (self.home / ".drain_request.json").write_text("{}", encoding="utf-8")
            return httpx.Response(200, json={"ok": True, "result": []})

        inbound, _ = self._make(
            channel=TelegramChannel(
                FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(scripted))
            )
        )
        await inbound._poll_loop()
        self.assertEqual(calls, [1, 2])
        estado = json.loads((self.home / "inbound-telegram.json").read_text(encoding="utf-8"))
        self.assertEqual(estado["consumed"], 1)

    async def test_run_recusa_canal_desabilitado(self):
        inbound, _ = self._make(enabled=False)
        with self.assertRaises(TelegramError):
            await inbound.run()

    async def test_sem_token_no_cofre_erro_fechado(self):
        self._write_config()
        with self.assertRaises(TelegramError):
            TelegramInbound(self.home, FakeRouter(), token=None)

    async def test_erro_no_get_updates_nao_derruba_o_loop(self):
        calls = {"n": 0}

        def failing_then_ok(request: httpx.Request) -> httpx.Response:
            if not request.url.path.endswith("/getUpdates"):
                return make_handler()(request)
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, json={"ok": False, "description": "boom"})
            (self.home / ".drain_request.json").write_text("{}", encoding="utf-8")
            return httpx.Response(200, json={"ok": True, "result": []})

        inbound, _ = self._make(
            channel=TelegramChannel(
                FAKE_ACCOUNT,
                client=httpx.AsyncClient(transport=httpx.MockTransport(failing_then_ok)),
            )
        )
        await inbound._poll_loop()  # falha transitória é tolerada no ciclo
        self.assertGreaterEqual(calls["n"], 2)


class InboundExperienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["KAIROS_DISABLE_KEYRING"] = "1"

    async def asyncTearDown(self) -> None:
        os.environ.pop("KAIROS_DISABLE_KEYRING", None)
        self._tmp.cleanup()

    def _write_inbound_config(self, *, experiences: bool) -> None:
        from kairos_gateway.adapters.config import save_config

        save_config(
            self.home,
            {
                "telegram": {
                    "enabled": True,
                    "chat_id_default": "",
                    "inbound": {
                        "enabled": True,
                        "allowed_user_ids": [AUTH_USER],
                        "poll_interval_seconds": 1.0,
                        "experiences": experiences,
                    },
                },
                "whatsapp": {"enabled": False, "phone_number_id": "", "number_default": ""},
                "slack": {"enabled": False, "channel_default": ""},
                "webhook": {"enabled": False, "endpoints": []},
            },
        )

    def _make(self, *, experiences: bool) -> tuple[TelegramInbound, FakeRouter]:
        self._write_inbound_config(experiences=experiences)
        fake = FakeRouter()
        chan = TelegramChannel(
            FAKE_ACCOUNT, client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler()))
        )
        inbound = TelegramInbound(self.home, fake, token=FAKE_ACCOUNT, channel=chan)
        return inbound, fake

    async def test_off_nao_injeta_experiencia(self):
        from kairos_memory import ExperienceStatus, ExperienceStore

        store = ExperienceStore(self.home)
        store.add(
            trigger="rede caiu",
            observation="sem internet",
            correction="restart no roteador",
            status=ExperienceStatus.ATIVA,
        )
        inbound, fake = self._make(experiences=False)
        await inbound._dispatch(
            TelegramUpdate(
                update_id=1, message_id=1, chat_id=CHAT, user_id=AUTH_USER, text="rede caiu"
            )
        )
        self.assertEqual(len(fake.envelopes), 1)
        self.assertEqual(fake.envelopes[0].content, "rede caiu")

    async def test_on_injeta_correcao_no_turno(self):
        from kairos_memory import ExperienceStatus, ExperienceStore

        store = ExperienceStore(self.home)
        store.add(
            trigger="rede caiu",
            observation="sem internet",
            correction="restart no roteador",
            status=ExperienceStatus.ATIVA,
        )
        inbound, fake = self._make(experiences=True)
        await inbound._dispatch(
            TelegramUpdate(
                update_id=2, message_id=2, chat_id=CHAT, user_id=AUTH_USER, text="rede caiu"
            )
        )
        self.assertEqual(len(fake.envelopes), 1)
        content = fake.envelopes[0].content
        self.assertIn("restart no roteador", content)
        self.assertTrue(content.rstrip().endswith("rede caiu"))

    async def test_on_sem_correcoes_nao_muda_conteudo(self):
        inbound, fake = self._make(experiences=True)
        await inbound._dispatch(
            TelegramUpdate(update_id=3, message_id=3, chat_id=CHAT, user_id=AUTH_USER, text="oi")
        )
        self.assertEqual(len(fake.envelopes), 1)
        self.assertEqual(fake.envelopes[0].content, "oi")


if __name__ == "__main__":
    unittest.main()
