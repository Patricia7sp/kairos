"""Unit tests for the messaging platform adapters.

Every adapter uses httpx.MockTransport injected via the `client` param,
so no real network is touched. The tests assert the request shape and the
structured SendResult; a cloud request that fails transiently or permanently
is exercised deterministically.
"""

import json
import unittest

import httpx

from kairos_gateway.adapters.slack import SlackAdapter
from kairos_gateway.adapters.telegram import TelegramAdapter
from kairos_gateway.adapters.webhook import WebhookAdapter
from kairos_gateway.adapters.whatsapp import WhatsAppAdapter
from kairos_gateway.service import SendResult

FAKE_ACCOUNT = "123456:ABC-DEF"
CHAT_ID = "987654321"
PHONE_ID = "5555555555"
SLACK_URL = "https://hooks.slack.com/services/T00/B00/xxx"
WEBHOOKS = {
    "alertas": "https://hooks.example.com/alert",
    "monitor": "https://hooks.example.com/mon",
}


def _handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content or b"{}")
    if "/getMe" in str(request.url):
        return httpx.Response(200, json={"ok": True, "result": {"username": "kairos_test"}})
    if "/sendMessage" in str(request.url):
        assert payload.get("chat_id") == CHAT_ID
        return httpx.Response(200, json={"ok": True})
    if "/messages" in str(request.url) and request.url.host == "graph.facebook.com":
        assert payload.get("messaging_product") == "whatsapp"
        assert payload.get("to") == "+5511999999999"
        return httpx.Response(200, json={})
    if request.url.host in ("hooks.slack.com", "hooks.example.com"):
        return httpx.Response(200, json={})
    return httpx.Response(404)


class TelegramTests(unittest.TestCase):
    def test_verify_get_me(self):
        adapter = TelegramAdapter(
            FAKE_ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        self.assertEqual(adapter.verify(), {"ok": True, "message": "Bot @kairos_test conectado"})

    def test_send_ok(self):
        adapter = TelegramAdapter(
            FAKE_ACCOUNT,
            chat_id_default=CHAT_ID,
            client=httpx.Client(transport=httpx.MockTransport(_handler)),
        )
        self.assertEqual(adapter.send(f"telegram:{CHAT_ID}", "Olá"), SendResult(ok=True))

    def test_send_no_dest_fallback(self):
        adapter = TelegramAdapter(
            FAKE_ACCOUNT,
            chat_id_default=CHAT_ID,
            client=httpx.Client(transport=httpx.MockTransport(_handler)),
        )
        self.assertEqual(adapter.send("telegram:", "x"), SendResult(ok=True))

    def test_send_429_retryable(self):
        def _err(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"parameters": {"retry_after": 5}}, headers={"Retry-After": "5"}
            )

        adapter = TelegramAdapter(
            FAKE_ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(_err))
        )
        result = adapter.send(f"telegram:{CHAT_ID}", "x")
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertEqual(result.retry_after, 5.0)

    def test_send_401_permanent(self):
        def _err(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"description": "Unauthorized"})

        adapter = TelegramAdapter(
            FAKE_ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(_err))
        )
        result = adapter.send(f"telegram:{CHAT_ID}", "x")
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_kind, "nao_autorizado")

    def test_send_no_dest_reject(self):
        adapter = TelegramAdapter(
            FAKE_ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        result = adapter.send("telegram:", "")
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_kind, "sem_destino")

    def test_send_network_error_retryable(self):
        def _net(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        adapter = TelegramAdapter(
            FAKE_ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(_net))
        )
        self.assertEqual(
            adapter.send(f"telegram:{CHAT_ID}", "x"),
            SendResult(ok=False, retryable=True, error_kind="rede"),
        )


class WhatsAppTests(unittest.TestCase):
    def test_verify_ok(self):
        def _ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"verified_name": "Minha Loja"})

        adapter = WhatsAppAdapter(
            FAKE_ACCOUNT, PHONE_ID, client=httpx.Client(transport=httpx.MockTransport(_ok))
        )
        self.assertEqual(
            adapter.verify(), {"ok": True, "message": "Conta de WhatsApp Minha Loja conectada"}
        )

    def test_send_ok(self):
        adapter = WhatsAppAdapter(
            FAKE_ACCOUNT, PHONE_ID, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        self.assertEqual(adapter.send("whatsapp:+5511999999999", "Oi"), SendResult(ok=True))

    def test_send_400_permanent(self):
        def _err(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "invalid number"}})

        adapter = WhatsAppAdapter(
            FAKE_ACCOUNT, PHONE_ID, client=httpx.Client(transport=httpx.MockTransport(_err))
        )
        result = adapter.send("whatsapp:+0000", "Oi")
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_kind, "plataforma")

    def test_send_no_dest_reject(self):
        adapter = WhatsAppAdapter(
            FAKE_ACCOUNT, PHONE_ID, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        self.assertEqual(adapter.send("whatsapp:", "Oi").error_kind, "sem_destino")

    def test_send_500_retryable(self):
        def _err(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        adapter = WhatsAppAdapter(
            FAKE_ACCOUNT, PHONE_ID, client=httpx.Client(transport=httpx.MockTransport(_err))
        )
        result = adapter.send("whatsapp:+5511999999999", "Oi")
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)


class SlackTests(unittest.TestCase):
    def test_verify_ok(self):
        adapter = SlackAdapter(
            SLACK_URL, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        self.assertIn("válida", adapter.verify()["message"])

    def test_send_ok(self):
        adapter = SlackAdapter(
            SLACK_URL, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        self.assertEqual(adapter.send("slack:", "Hi"), SendResult(ok=True))

    def test_send_with_channel_override(self):
        captured: dict[str, str] = {}

        def _cap(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={})

        adapter = SlackAdapter(SLACK_URL, client=httpx.Client(transport=httpx.MockTransport(_cap)))
        adapter.send("slack:#ops", "Hi")
        self.assertIn("channel=%23ops", captured["url"])

    def test_send_429_retryable(self):
        def _err(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={})

        adapter = SlackAdapter(SLACK_URL, client=httpx.Client(transport=httpx.MockTransport(_err)))
        self.assertFalse(adapter.send("slack:", "x").ok)
        self.assertTrue(adapter.send("slack:", "x").retryable)


class WebhookTests(unittest.TestCase):
    def test_verify_ok(self):
        adapter = WebhookAdapter(
            WEBHOOKS, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        self.assertTrue(adapter.verify()["ok"])

    def test_send_named_endpoint(self):
        captured: dict[str, str] = {}

        def _cap(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={})

        adapter = WebhookAdapter(WEBHOOKS, client=httpx.Client(transport=httpx.MockTransport(_cap)))
        adapter.send("webhook:alertas", "fail!")
        self.assertEqual(captured["url"], "https://hooks.example.com/alert")

    def test_send_unknown_name_permanent(self):
        adapter = WebhookAdapter(
            WEBHOOKS, client=httpx.Client(transport=httpx.MockTransport(_handler))
        )
        result = adapter.send("webhook:inexistente", "x")
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_kind, "endpoint_inexistente")

    def test_send_direct_url(self):
        captured: dict[str, str] = {}

        def _cap(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={})

        adapter = WebhookAdapter(WEBHOOKS, client=httpx.Client(transport=httpx.MockTransport(_cap)))
        adapter.send("webhook:https://exemplo.com/fake", "test")
        self.assertEqual(captured["url"], "https://exemplo.com/fake")

    def test_send_500_retryable(self):
        def _err(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={})

        adapter = WebhookAdapter(WEBHOOKS, client=httpx.Client(transport=httpx.MockTransport(_err)))
        self.assertTrue(adapter.send("webhook:alertas", "x").retryable)


if __name__ == "__main__":
    unittest.main()
