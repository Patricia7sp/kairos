"""Canal de entrada Slack: Events API (webhook) vira turno do agente.

Espelha o padrão do WhatsApp na mecânica do Slack: assinatura ``v0`` do corpo
cru com o Signing Secret + janela anti-replay do ``X-Slack-Request-Timestamp``,
desafio ``url_verification`` e despacho assíncrono de ``event_callback``. Tudo
com fakes — nenhuma rede real.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from kairos_gateway.adapters.config import save_config
from kairos_gateway.slack_inbound import (
    SlackConfigError,
    SlackInbound,
    SlackMessage,
    SlackSignatureError,
    build_slack_inbound,
    parse_slack_events,
    verify_slack_signature,
)

SIGNING_SECRET = "signing-secret-de-teste"  # noqa: S105 - fixture sintética de teste
USER = "U123ABC"
CHANNEL = "D456DEF"
EVENT_ID = "Ev789XYZ"
NOW = int(time.time())


def _signature(secret: str, raw: bytes, *, ts: int = NOW) -> tuple[str, str]:
    base = f"v0:{ts}:{raw.decode('utf-8')}"
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return str(ts), f"v0={digest}"


def _message_payload(
    *, text: str = "Olá", event_id: str = EVENT_ID, user: str = USER, channel: str = CHANNEL
) -> bytes:
    return json.dumps(
        {
            "token": "xoxb-ignore",
            "team_id": "T111",
            "api_app_id": "A222",
            "type": "event_callback",
            "event_id": event_id,
            "event_time": NOW,
            "event": {
                "type": "message",
                "channel": channel,
                "user": user,
                "text": text,
                "ts": f"{NOW}.000001",
                "channel_type": "im",
                "event_ts": f"{NOW}.000001",
            },
        }
    ).encode()


def _challenge_payload(*, challenge: str = "desafio-42") -> bytes:
    return json.dumps(
        {"type": "url_verification", "challenge": challenge, "team_id": "T111"}
    ).encode()


class FakeEvent:
    def __init__(self, kind: str, **kwargs: Any) -> None:
        self.kind = kind
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeCall:
    def __init__(self, name: str, arguments: str = "{}") -> None:
        self.name = name
        self.arguments = arguments


class FakeRouter:
    def __init__(self, events: list[Any] | None = None) -> None:
        self.events = list(events or [])
        self.envelopes: list[Any] = []

    async def stream(self, envelope: Any) -> Any:
        self.envelopes.append(envelope)
        for event in self.events:
            yield event
        yield FakeEvent("turn_end")


class FakeAdapter:
    name = "slack"

    def __init__(self, log: list[tuple[str, str, str]] | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = log if log is not None else []

    def send(self, target: str, payload: str):
        self.sent.append(("send", target, payload))
        from kairos_gateway.service import SendResult

        return SendResult(ok=True)


def _make_inbound(tmp: Path, *, router: FakeRouter, adapter: FakeAdapter | None) -> SlackInbound:
    doc = {
        "slack": {
            "enabled": True,
            "channel_default": "",
            "inbound": {"enabled": True, "allowed_user_ids": [USER], "experiences": False},
        }
    }
    save_config(tmp, doc)
    return SlackInbound(
        tmp,
        router,
        signing_secret=SIGNING_SECRET,
        adapter=adapter,
    )


def _signed(raw: bytes) -> dict[str, str]:
    ts, sig = _signature(SIGNING_SECRET, raw)
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


class SignatureTests(unittest.TestCase):
    def test_verify_aceita_assinatura_valida(self):
        raw = _message_payload()
        ts, sig = _signature(SIGNING_SECRET, raw)
        self.assertTrue(
            verify_slack_signature(SIGNING_SECRET, raw, timestamp_header=ts, signature_header=sig)
        )

    def test_verify_recusa_assinatura_trocada(self):
        raw = _message_payload()
        _, sig = _signature("outro-segredo", raw)
        ts, _ = _signature(SIGNING_SECRET, raw)
        self.assertFalse(
            verify_slack_signature(SIGNING_SECRET, raw, timestamp_header=ts, signature_header=sig)
        )

    def test_verify_recusa_header_ausente_ou_malformado(self):
        raw = _message_payload()
        ts, sig = _signature(SIGNING_SECRET, raw)
        self.assertFalse(
            verify_slack_signature(SIGNING_SECRET, raw, timestamp_header=None, signature_header=sig)
        )
        self.assertFalse(
            verify_slack_signature(SIGNING_SECRET, raw, timestamp_header=ts, signature_header=None)
        )
        self.assertFalse(
            verify_slack_signature(
                SIGNING_SECRET, raw, timestamp_header=ts, signature_header="sha256=x"
            )
        )

    def test_verify_recusa_replay_fora_da_janela(self):
        raw = _message_payload()
        velho = NOW - 600
        ts, sig = _signature(SIGNING_SECRET, raw, ts=velho)
        self.assertFalse(
            verify_slack_signature(
                SIGNING_SECRET, raw, timestamp_header=ts, signature_header=sig, now=NOW
            )
        )

    def test_verify_aceita_na_borda_da_janela(self):
        raw = _message_payload()
        ts, sig = _signature(SIGNING_SECRET, raw, ts=NOW - 300)
        self.assertTrue(
            verify_slack_signature(
                SIGNING_SECRET, raw, timestamp_header=ts, signature_header=sig, now=NOW
            )
        )

    def test_verify_recusa_timestamp_nao_numerico(self):
        raw = _message_payload()
        _, sig = _signature(SIGNING_SECRET, raw)
        self.assertFalse(
            verify_slack_signature(
                SIGNING_SECRET, raw, timestamp_header="ano-passado", signature_header=sig
            )
        )


class ParseTests(unittest.TestCase):
    def test_parse_eventos_extrai_mensagem_de_usuario(self):
        raw = json.loads(_message_payload().decode())
        (mensagem,) = parse_slack_events(raw)
        self.assertIsInstance(mensagem, SlackMessage)
        self.assertEqual(mensagem.message_id, EVENT_ID)
        self.assertEqual(mensagem.user, USER)
        self.assertEqual(mensagem.channel, CHANNEL)
        self.assertEqual(mensagem.text, "Olá")

    def test_parse_ignora_bot_e_subtypes(self):
        base = json.loads(_message_payload().decode())
        com_bot = json.loads(json.dumps(base))
        com_bot["event"]["bot_id"] = "B333"
        com_bot["event"]["subtype"] = "bot_message"
        com_sub = json.loads(json.dumps(base))
        com_sub["event"]["subtype"] = "message_changed"
        com_sub["event"]["message"] = com_sub["event"]
        self.assertEqual(parse_slack_events(com_bot), [])
        self.assertEqual(parse_slack_events(com_sub), [])

    def test_parse_ignora_envelopes_sem_evento_de_mensagem(self):
        self.assertEqual(parse_slack_events({}), [])
        self.assertEqual(parse_slack_events(None), [])
        self.assertEqual(parse_slack_events({"type": "url_verification", "challenge": "x"}), [])
        self.assertEqual(
            parse_slack_events({"type": "event_callback", "event": {"type": "pong"}}), []
        )

    def test_parse_ignora_mensagem_sem_usuario_ou_texto(self):
        payload = json.loads(_message_payload(text="  ").decode())
        self.assertEqual(parse_slack_events(payload), [])
        payload = json.loads(_message_payload().decode())
        del payload["event"]["user"]
        self.assertEqual(parse_slack_events(payload), [])


class InboundTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _accepted(self, inbound: SlackInbound, raw: bytes):
        cab = _signed(raw)
        return inbound.accept(
            raw,
            timestamp_header=cab["X-Slack-Request-Timestamp"],
            signature_header=cab["X-Slack-Signature"],
        )

    def test_challenge_devolve_desafio_no_handshake(self):
        inbound = _make_inbound(self.tmp, router=FakeRouter(), adapter=None)
        self.assertEqual(self._accepted(inbound, _challenge_payload()), "desafio-42")

    def test_accept_desabilitado_recusa_fechado(self):
        doc = {
            "slack": {
                "enabled": True,
                "channel_default": "",
                "inbound": {"enabled": False, "allowed_user_ids": [USER], "experiences": True},
            }
        }
        save_config(self.tmp, doc)
        inbound = SlackInbound(self.tmp, FakeRouter(), signing_secret=SIGNING_SECRET)
        with self.assertRaises(SlackConfigError):
            self._accepted(inbound, _message_payload())

    def test_accept_sem_signing_secret_recusa_fechado(self):
        inbound = _make_inbound(self.tmp, router=FakeRouter(), adapter=None)
        self.assertFalse(
            inbound.has_valid_signature(
                _message_payload(),
                timestamp_header="0",
                signature_header="v0=00",
            )
        )
        sem_segredo = SlackInbound(self.tmp, FakeRouter(), signing_secret=None)
        with self.assertRaises(SlackConfigError):
            self._accepted(sem_segredo, _message_payload())

    def test_accept_assinatura_invalida_recusa(self):
        inbound = _make_inbound(self.tmp, router=FakeRouter(), adapter=None)
        raw = _message_payload()
        ts, _ = _signature(SIGNING_SECRET, raw)
        with self.assertRaises(SlackSignatureError):
            inbound.accept(raw, timestamp_header=ts, signature_header="v0=" + "0" * 64)

    def test_accept_despacha_usuario_autorizado_e_responde(self):
        router = FakeRouter([FakeEvent("delta", text="Oi, ")])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            processado = self._accepted(inbound, _message_payload())
            await asyncio.gather(*tuple(inbound._tasks))
            return processado

        self.assertIsNone(asyncio.run(run()))
        self.assertEqual(len(router.envelopes), 1)
        envelope = router.envelopes[0]
        self.assertEqual(envelope.source, "slack")
        self.assertEqual(envelope.conversation_id, f"slack:{USER}")
        self.assertEqual(envelope.idempotency_key, f"slack:{EVENT_ID}")
        self.assertEqual(envelope.content, "Olá")
        self.assertEqual(adapter.sent, [("send", f"slack:{CHANNEL}", "Oi,")])

    def test_accept_ignora_usuario_fora_da_lista(self):
        adapter = FakeAdapter()
        router = FakeRouter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)
        raw = _message_payload(user="UZ999")
        self.assertIsNone(self._accepted(inbound, raw))
        self.assertEqual(router.envelopes, [])

    def test_accept_sem_adapter_nao_constroi_turno(self):
        router = FakeRouter()
        inbound = _make_inbound(self.tmp, router=router, adapter=None)
        self.assertIsNone(self._accepted(inbound, _message_payload()))
        self.assertEqual(router.envelopes, [])

    def test_accept_deduplica_reentrega_do_slack(self):
        router = FakeRouter()
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            self._accepted(inbound, _message_payload())
            self._accepted(inbound, _message_payload())
            await asyncio.gather(*tuple(inbound._tasks))

        asyncio.run(run())
        self.assertEqual(len(router.envelopes), 1)

    def test_aprovacao_sem_botao_e_recusa_honesta(self):
        call = FakeCall("despertador")
        router = FakeRouter([FakeEvent("tool_approval_request", tool_call=call)])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            self._accepted(inbound, _message_payload())
            await asyncio.gather(*tuple(inbound._tasks))

        asyncio.run(run())
        self.assertTrue(adapter.sent)
        alvos = {target for _, target, _ in adapter.sent}
        self.assertIn(f"slack:{CHANNEL}", alvos)
        corpo = " ".join(texto for _, _, texto in adapter.sent)
        self.assertIn("nao tem botoes de aprovacao", corpo)

    def test_resposta_longa_vai_chunked_com_aviso(self):
        router = FakeRouter([FakeEvent("delta", text="x" * 45_000)])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            self._accepted(inbound, _message_payload())
            await asyncio.gather(*tuple(inbound._tasks))

        asyncio.run(run())
        self.assertGreater(len(adapter.sent), 1)
        corpo = "".join(text for _, _, text in adapter.sent)
        self.assertIn("consulte o painel", corpo)

    def test_build_slack_inbound_resolve_do_router(self):
        adapter = FakeAdapter()
        doc = {
            "slack": {
                "enabled": True,
                "channel_default": "",
                "inbound": {"enabled": True, "allowed_user_ids": [USER], "experiences": True},
            }
        }
        save_config(self.tmp, doc)
        inbound = build_slack_inbound(
            self.tmp,
            router=FakeRouter(),
            adapter=adapter,
            signing_secret=SIGNING_SECRET,
        )
        self.assertTrue(inbound.inbound_enabled)
        self.assertIn(USER, inbound.allowed_user_ids)


if __name__ == "__main__":
    unittest.main()
