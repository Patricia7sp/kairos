"""Canal de entrada Webhook genérico: token + fonte autorizada viram turno.

Falta da paridade inbound/outbound fechada: o adapter webhook só entregava;
agora a ingestão por token (cofre), com fonte autorizada, roda o turno pelo
mesmo serviço de interação e responde pelo adapter no ``reply_url`` (ou no
endpoint padrão). Fakes — nenhuma rede.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kairos_gateway.adapters.config import save_config
from kairos_gateway.webhook_inbound import (
    WebhookConfigError,
    WebhookInbound,
    WebhookMessage,
    WebhookUnauthorizedError,
    build_webhook_inbound,
    parse_webhook_message,
)

INGEST_TOKEN = "token-de-ingestao"  # noqa: S105 - fixture sintética de teste
FONTE = "sensor-x"


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
    name = "webhook"

    def __init__(self, log: list[tuple[str, str, str]] | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = log if log is not None else []

    def send(self, target: str, payload: str):
        self.sent.append(("send", target, payload))
        from kairos_gateway.service import SendResult

        return SendResult(ok=True)


def _make_inbound(tmp: Path, *, router: FakeRouter, adapter: FakeAdapter | None) -> WebhookInbound:
    doc = {
        "webhook": {
            "enabled": True,
            "endpoints": [{"name": "padrao", "url": "https://servico.test/resp"}],
            "inbound": {"enabled": True, "allowed_sources": [FONTE], "experiences": False},
        }
    }
    save_config(tmp, doc)
    return WebhookInbound(
        tmp,
        router,
        ingest_token=INGEST_TOKEN,
        adapter=adapter,
    )


def _payload(
    *, text: str = "Olá", source: str = FONTE, ident: str = "e-1", reply_url: str = ""
) -> bytes:
    corpo: dict[str, Any] = {"text": text}
    if source:
        corpo["source"] = source
    if ident:
        corpo["id"] = ident
    if reply_url:
        corpo["reply_url"] = reply_url
    return json.dumps(corpo).encode()


class ParseTests(unittest.TestCase):
    def test_parse_extrai_mensagem_valida(self):
        mensagem = parse_webhook_message(json.loads(_payload().decode()))
        self.assertIsInstance(mensagem, WebhookMessage)
        assert mensagem is not None
        self.assertEqual(mensagem.text, "Olá")
        self.assertEqual(mensagem.source, FONTE)
        self.assertEqual(mensagem.message_id, "e-1")

    def test_parse_sem_texto_retorna_none(self):
        self.assertIsNone(parse_webhook_message({}))
        self.assertIsNone(parse_webhook_message({"text": "   "}))
        self.assertIsNone(parse_webhook_message({"text": 7}))
        self.assertIsNone(parse_webhook_message(None))

    def test_parse_campos_opcionais_invalidos_retornam_none(self):
        self.assertIsNone(parse_webhook_message({"text": "oi", "id": 9}))
        self.assertIsNone(parse_webhook_message({"text": "oi", "source": 1}))
        self.assertIsNone(parse_webhook_message({"text": "oi", "reply_url": 5}))


class InboundTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _accepted(self, inbound: WebhookInbound, raw: bytes) -> int:
        return inbound.accept(
            raw,
            token_header=INGEST_TOKEN,
            source_header=None,
        )

    def test_accept_desabilitado_recusa_fechado(self):
        doc = {
            "webhook": {
                "enabled": True,
                "endpoints": [],
                "inbound": {"enabled": False, "allowed_sources": [FONTE], "experiences": True},
            }
        }
        save_config(self.tmp, doc)
        inbound = WebhookInbound(self.tmp, FakeRouter(), ingest_token=INGEST_TOKEN)
        with self.assertRaises(WebhookConfigError):
            self._accepted(inbound, _payload())

    def test_accept_sem_token_no_cofre_recusa_fechado(self):
        sem_token = WebhookInbound(self.tmp, FakeRouter(), ingest_token=None)
        self.assertFalse(sem_token.has_valid_token(INGEST_TOKEN))
        with self.assertRaises(WebhookConfigError):
            sem_token.accept(_payload(), token_header=INGEST_TOKEN, source_header=None)

    def test_accept_token_divergente_recusa(self):
        inbound = _make_inbound(self.tmp, router=FakeRouter(), adapter=None)
        with self.assertRaises(WebhookUnauthorizedError):
            inbound.accept(_payload(), token_header="outro-token", source_header=None)  # noqa: S106 - divergente de teste
        with self.assertRaises(WebhookUnauthorizedError):
            inbound.accept(_payload(), token_header=None, source_header=None)

    def test_accept_despacha_fonte_autorizada_e_responde_no_reply_url(self):
        router = FakeRouter([FakeEvent("delta", text="Recebido!")])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            aceitos = self._accepted(inbound, _payload(reply_url="https://servico.test/resp"))
            await asyncio.gather(*tuple(inbound._tasks))
            return aceitos

        self.assertEqual(asyncio.run(run()), 1)
        self.assertEqual(len(router.envelopes), 1)
        envelope = router.envelopes[0]
        self.assertEqual(envelope.source, "webhook")
        self.assertEqual(envelope.conversation_id, f"webhook:{FONTE}")
        self.assertEqual(envelope.content, "Olá")
        self.assertEqual(
            adapter.sent,
            [("send", "webhook:https://servico.test/resp", "Recebido!")],
        )

    def test_accept_fonte_do_header_assume_o_corpo(self):
        router = FakeRouter()
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            aceitos = inbound.accept(
                _payload(source=""),
                token_header=INGEST_TOKEN,
                source_header="sensor-x",
            )
            await asyncio.gather(*tuple(inbound._tasks))
            return aceitos

        self.assertEqual(asyncio.run(run()), 1)
        self.assertEqual(router.envelopes[0].conversation_id, f"webhook:{FONTE}")

    def test_accept_fonte_fora_da_lista_ignora(self):
        router = FakeRouter()
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)
        aceitos = self._accepted(inbound, _payload(source="nao-autorizada"))
        self.assertEqual(aceitos, 0)
        self.assertEqual(router.envelopes, [])

    def test_accept_sem_adapter_nao_constroi_turno(self):
        router = FakeRouter()
        inbound = _make_inbound(self.tmp, router=router, adapter=None)
        self.assertEqual(self._accepted(inbound, _payload()), 0)
        self.assertEqual(router.envelopes, [])

    def test_accept_deduplica_reentrega_com_mesmo_id(self):
        router = FakeRouter()
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            primeiro = self._accepted(inbound, _payload())
            segundo = self._accepted(inbound, _payload())
            await asyncio.gather(*tuple(inbound._tasks))
            return primeiro, segundo

        primeiro, segundo = asyncio.run(run())
        self.assertEqual((primeiro, segundo), (1, 0))
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
        corpo = " ".join(texto for _, _, texto in adapter.sent)
        self.assertIn("nao tem botoes de aprovacao", corpo)

    def test_resposta_long_sem_reply_url_usa_endpoint_padrao(self):
        router = FakeRouter([FakeEvent("delta", text="x" * 45_000)])
        adapter = FakeAdapter()
        inbound = _make_inbound(self.tmp, router=router, adapter=adapter)

        async def run():
            self._accepted(inbound, _payload())
            await asyncio.gather(*tuple(inbound._tasks))

        asyncio.run(run())
        self.assertGreater(len(adapter.sent), 1)
        alvos = {target for _, target, _ in adapter.sent}
        self.assertEqual(alvos, {"webhook:"})

    def test_build_webhook_inbound_resolve_do_router(self):
        adapter = FakeAdapter()
        doc = {
            "webhook": {
                "enabled": True,
                "endpoints": [],
                "inbound": {"enabled": True, "allowed_sources": [FONTE], "experiences": True},
            }
        }
        save_config(self.tmp, doc)
        inbound = build_webhook_inbound(
            self.tmp,
            router=FakeRouter(),
            adapter=adapter,
            ingest_token=INGEST_TOKEN,
        )
        self.assertTrue(inbound.inbound_enabled)
        self.assertIn(FONTE, inbound.allowed_sources)


if __name__ == "__main__":
    unittest.main()
