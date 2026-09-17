"""Canal de entrada WhatsApp: webhook da Cloud API vira turno do agente.

Espelha o padrão do Telegram (allowlist fail-closed, sem efeito fingido) na
versão dirigida por webhook: assinatura ``X-Hub-Signature-256``, apertão de
mão do subscribe e despacho assíncrono. Tudo com fakes — nenhuma rede real.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kairos_gateway.adapters.config import save_config
from kairos_gateway.whatsapp_inbound import (
    WhatsAppConfigError,
    WhatsAppInbound,
    WhatsAppMessage,
    WhatsAppSignatureError,
    build_whatsapp_inbound,
    parse_messages,
    verify_challenge,
    verify_webhook_signature,
)

APP_SECRET = "app-secret-de-teste"  # noqa: S105 - fixture sintética de teste
VERIFY_TOKEN = "verify-token-de-teste"  # noqa: S105 - fixture sintética de teste
PHONE = "+5511999888777"
PHONE_DIGITS = "5511999888777"
WAMID = "wamid.ABC123=="


def _headers(secret: str, raw: bytes) -> dict[str, str]:
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={expected}"}


def _payload(*, text: str = "Olá", mid: str = WAMID, sender: str = PHONE) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "111111111111111",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "phone_number_id": "22222222222222",
                                    "display_phone_number": "5511900001111",
                                },
                                "contacts": [{"profile": {"name": "Teste"}, "wa_id": sender}],
                                "messages": [
                                    {
                                        "from": sender,
                                        "id": mid,
                                        "timestamp": "1730000000",
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
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
    name = "whatsapp"

    def __init__(self, log: list[tuple[str, str, str]] | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = log if log is not None else []

    def send(self, target: str, payload: str):
        self.sent.append(("send", target, payload))
        from kairos_gateway.service import SendResult

        return SendResult(ok=True)


def _make_inbound(tmp: Path, *, router: FakeRouter, adapter: FakeAdapter | None) -> WhatsAppInbound:
    doc = {
        "whatsapp": {
            "enabled": True,
            "phone_number_id": "22222222222222",
            "number_default": "",
            "inbound": {
                "enabled": True,
                "allowed_phone_numbers": [PHONE],
                "experiences": False,
            },
        }
    }
    save_config(tmp, doc)
    return WhatsAppInbound(
        tmp,
        router,
        app_secret=APP_SECRET,
        verify_token=VERIFY_TOKEN,
        adapter=adapter,
    )


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_verify_webhook_signature_aceita_assinatura_valida(self):
        raw = _payload()
        header = _headers(APP_SECRET, raw)["X-Hub-Signature-256"]
        self.assertTrue(verify_webhook_signature(APP_SECRET, raw, header))

    def test_verify_webhook_signature_recusa_assinatura_trocada(self):
        raw = _payload()
        self.assertFalse(verify_webhook_signature("outro-segredo", raw, "sha256=0" * 16))

    def test_verify_webhook_signature_recusa_header_ausente_ou_malformado(self):
        raw = _payload()
        self.assertFalse(verify_webhook_signature(APP_SECRET, raw, None))
        self.assertFalse(verify_webhook_signature(APP_SECRET, raw, "sem-prefixo"))

    def test_verify_webhook_signature_recusa_corpo_vazio(self):
        self.assertFalse(verify_webhook_signature(APP_SECRET, b"", "sha256=0" * 16))

    def test_verify_challenge_devolve_desafio_no_subscribe_correto(self):
        self.assertEqual(
            verify_challenge(
                shared_token=VERIFY_TOKEN,
                mode="subscribe",
                verify_token=VERIFY_TOKEN,
                challenge="desafio-123",
            ),
            "desafio-123",
        )

    def test_verify_challenge_recusa_divergencias(self):
        kwargs = {
            "shared_token": VERIFY_TOKEN,
            "mode": "subscribe",
            "verify_token": VERIFY_TOKEN,
            "challenge": "x",
        }
        with self.assertRaises(WhatsAppSignatureError):
            verify_challenge(**{**kwargs, "mode": "unsubscribe"})
        with self.assertRaises(WhatsAppSignatureError):
            verify_challenge(**{**kwargs, "verify_token": "outro"})
        with self.assertRaises(WhatsAppSignatureError):
            verify_challenge(**{**kwargs, "challenge": None})


class ParseTests(unittest.TestCase):
    def test_parse_messages_extrai_texto_normalizado(self):
        raw = json.loads(_payload().decode())
        (mensagem,) = parse_messages(raw)
        self.assertIsInstance(mensagem, WhatsAppMessage)
        self.assertEqual(mensagem.message_id, WAMID)
        self.assertEqual(mensagem.wa_id, PHONE)
        self.assertEqual(mensagem.phone, PHONE_DIGITS)
        self.assertEqual(mensagem.text, "Olá")

    def test_parse_messages_ignora_status_e_nao_texto(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {"messages": [{"type": "image", "id": "w.jpg"}]},
                        },
                        {"field": "messages", "value": {"messages": []}},
                        {
                            "field": "statuses",
                            "value": {"statuses": [{"id": "s1", "status": "read"}]},
                        },
                    ]
                }
            ]
        }
        self.assertEqual(parse_messages(payload), [])

    def test_parse_messages_malformado_retorna_vazio(self):
        self.assertEqual(parse_messages({}), [])
        self.assertEqual(parse_messages(None), [])
        self.assertEqual(parse_messages({"entry": "não-lista"}), [])

    def test_parse_messages_ignora_mensagem_sem_remetente_ou_texto(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {"from": "", "id": "x1", "type": "text", "text": {"body": "oi"}}
                                ]
                            },
                        }
                    ]
                }
            ]
        }
        self.assertEqual(parse_messages(payload), [])


class InboundTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _accepted(self, inbound: WhatsAppInbound, raw: bytes, header: dict[str, str] | None = None):
        sig = header or _headers(APP_SECRET, raw)
        return inbound.accept(raw, sig.get("X-Hub-Signature-256"))

    def test_accept_desabilitado_recusa_fechado(self):
        doc = {
            "whatsapp": {
                "enabled": True,
                "phone_number_id": "",
                "number_default": "",
                "inbound": {
                    "enabled": False,
                    "allowed_phone_numbers": [PHONE],
                    "experiences": True,
                },
            }
        }
        save_config(self.tmp, doc)
        inbound = WhatsAppInbound(
            self.tmp, FakeRouter(), app_secret=APP_SECRET, verify_token=VERIFY_TOKEN
        )
        with self.assertRaises(WhatsAppConfigError):
            self._accepted(inbound, _payload())

    def test_accept_sem_app_secret_recusa_fechado(self):
        inbound = _make_inbound(self.tmp, router=FakeRouter(), adapter=None)
        self.assertFalse(inbound.has_valid_signature(_payload(), "sha256=" + "0" * 16))
        received = WhatsAppInbound(
            self.tmp, FakeRouter(), app_secret=None, verify_token=VERIFY_TOKEN
        )
        with self.assertRaises(WhatsAppConfigError):
            self._accepted(received, _payload())

    def test_accept_assinatura_invalida_recusa(self):
        inbound = _make_inbound(self.tmp, router=FakeRouter(), adapter=None)
        with self.assertRaises(WhatsAppSignatureError):
            inbound.accept(_payload(), "sha256=" + "0" * 64)

    def test_accept_despacha_remetente_autorizado_e_responde(self):
        router = FakeRouter([FakeEvent("delta", text="Oi, ")])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)
        challenge = inbound.verify_handshake(
            mode="subscribe", verify_token=VERIFY_TOKEN, challenge="c1"
        )
        self.assertEqual(challenge, "c1")

        async def run():
            aceitos = self._accepted(inbound, _payload())
            await asyncio.gather(*tuple(inbound._tasks))
            return aceitos

        aceitos = asyncio.run(run())
        self.assertEqual(aceitos, 1)
        self.assertEqual(len(router.envelopes), 1)
        envelope = router.envelopes[0]
        self.assertEqual(envelope.source, "whatsapp")
        self.assertEqual(envelope.conversation_id, f"whatsapp:{PHONE_DIGITS}")
        self.assertEqual(envelope.idempotency_key, f"whatsapp:{WAMID}")
        self.assertEqual(envelope.content, "Olá")
        self.assertEqual(
            adapter.sent,
            [("send", f"whatsapp:{PHONE_DIGITS}", "Oi,")],
        )

    def test_accept_ignora_remetente_fora_da_lista(self):
        adapter = FakeAdapter()
        router = FakeRouter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)
        raw = _payload(sender="+5588999887766")
        aceitos = self._accepted(inbound, raw)
        self.assertEqual(aceitos, 0)
        self.assertEqual(router.envelopes, [])

    def test_accept_sem_adapter_nao_constroi_turno(self):
        router = FakeRouter()
        inbound = _make_inbound(self.tmp, router=router, adapter=None)
        aceitos = self._accepted(inbound, _payload())
        self.assertEqual(aceitos, 0)
        self.assertEqual(router.envelopes, [])

    def test_accept_deduplica_reentrega_da_meta(self):
        router = FakeRouter()
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            primeiro = self._accepted(inbound, _payload())
            segundo = self._accepted(inbound, _payload())
            await asyncio.gather(*tuple(inbound._tasks))
            return primeiro, segundo

        primeiro, segundo = asyncio.run(run())
        self.assertEqual(primeiro, 1)
        self.assertEqual(segundo, 0)
        self.assertEqual(len(router.envelopes), 1)

    def test_aprovacao_sem_botao_e_recusa_honesta(self):
        call = FakeCall("despertador")
        router = FakeRouter([FakeEvent("tool_approval_request", tool_call=call)])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            self._accepted(inbound, _payload())
            await asyncio.gather(*tuple(inbound._tasks))

        asyncio.run(run())
        self.assertTrue(adapter.sent)
        alvos = {target for _, target, _ in adapter.sent}
        self.assertIn(f"whatsapp:{PHONE_DIGITS}", alvos)
        corpo = " ".join(texto for _, _, texto in adapter.sent)
        self.assertIn("nao tem botoes de aprovacao", corpo)

    def test_resposta_longa_vai_chunked_com_aviso(self):
        router = FakeRouter([FakeEvent("delta", text="x" * 45_000)])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            self._accepted(inbound, _payload())
            await asyncio.gather(*tuple(inbound._tasks))

        asyncio.run(run())
        self.assertGreater(len(adapter.sent), 1)
        corpo = "".join(text for _, _, text in adapter.sent)
        self.assertIn("consulte o painel", corpo)

    def test_build_whatsapp_inbound_resolve_do_router(self):
        adapter = FakeAdapter()
        doc = {
            "whatsapp": {
                "enabled": True,
                "phone_number_id": "",
                "number_default": "",
                "inbound": {"enabled": True, "allowed_phone_numbers": [PHONE], "experiences": True},
            }
        }
        save_config(self.tmp, doc)
        inbound = build_whatsapp_inbound(
            self.tmp,
            router=FakeRouter(),
            adapter=adapter,
            app_secret=APP_SECRET,
            verify_token=VERIFY_TOKEN,
        )
        self.assertTrue(inbound.inbound_enabled)
        self.assertIn(PHONE_DIGITS, inbound.allowed_phone_numbers)


if __name__ == "__main__":
    unittest.main()
