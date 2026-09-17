"""messaging.json — configuração não-secreta estrita e fail-closed."""

import json
import stat
import tempfile
import unittest
from pathlib import Path

from kairos_gateway.adapters import build_platform_adapters, messaging_platforms
from kairos_gateway.adapters.config import (
    default_config,
    endpoints,
    load_config,
    save_config,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_load_config_missing_file_returns_defaults(self):
        doc = load_config(self.tmp)
        self.assertFalse(doc["telegram"]["enabled"])
        self.assertEqual(doc["webhook"]["endpoints"], [])
        self.assertTrue(doc["telegram"]["inbound"]["experiences"])
        self.assertFalse(doc["whatsapp"]["inbound"]["enabled"])
        self.assertEqual(doc["whatsapp"]["inbound"]["allowed_phone_numbers"], [])

    def test_whatsapp_inbound_suporta_telefones(self):
        doc = default_config()
        doc["whatsapp"]["inbound"]["enabled"] = True
        doc["whatsapp"]["inbound"]["allowed_phone_numbers"] = ["+5511999888777", "5511900001111"]
        save_config(self.tmp, doc)
        reloaded = load_config(self.tmp)
        self.assertTrue(reloaded["whatsapp"]["inbound"]["enabled"])
        self.assertEqual(
            reloaded["whatsapp"]["inbound"]["allowed_phone_numbers"],
            ["+5511999888777", "5511900001111"],
        )

    def test_whatsapp_inbound_allowlist_nao_aceita_ids_numericos(self):
        doc = default_config()
        doc["whatsapp"]["inbound"]["allowed_phone_numbers"] = [1234]
        with self.assertRaises(ValueError):
            save_config(self.tmp, doc)

    def test_whatsapp_inbound_rejeita_chave_desconhecida(self):
        doc = default_config()
        doc["whatsapp"]["inbound"]["poll_interval_seconds"] = 2.0
        with self.assertRaises(ValueError):
            save_config(self.tmp, doc)

    def test_telegram_allowlist_continua_numerica(self):
        doc = default_config()
        doc["telegram"]["inbound"]["allowed_user_ids"] = ["1"]
        with self.assertRaises(ValueError):
            save_config(self.tmp, doc)

    def test_inbound_experiences_ausente_herda_padrao_ligado(self):
        (self.tmp / "messaging.json").write_text(
            json.dumps(
                {
                    "telegram": {
                        "enabled": True,
                        "chat_id_default": "",
                        "inbound": {
                            "enabled": True,
                            "allowed_user_ids": [1],
                            "poll_interval_seconds": 1.0,
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        doc = load_config(self.tmp)
        self.assertTrue(doc["telegram"]["inbound"]["experiences"])

    def test_inbound_experiences_false_desliga(self):
        doc = default_config()
        doc["telegram"]["inbound"]["experiences"] = False
        save_config(self.tmp, doc)
        self.assertFalse(load_config(self.tmp)["telegram"]["inbound"]["experiences"])

    def test_save_and_load_roundtrip(self):
        doc = default_config()
        doc["telegram"]["enabled"] = True
        doc["telegram"]["chat_id_default"] = "42"
        doc["webhook"]["endpoints"] = [
            {"name": "alertas", "url": "https://hooks.example.com/alert"}
        ]
        save_config(self.tmp, doc)
        reloaded = load_config(self.tmp)
        self.assertTrue(reloaded["telegram"]["enabled"])
        self.assertEqual(reloaded["telegram"]["chat_id_default"], "42")
        self.assertEqual(endpoints(self.tmp), {"alertas": "https://hooks.example.com/alert"})

    def test_save_rejects_unknown_platform_key(self):
        with self.assertRaises(ValueError):
            save_config(self.tmp, {"telegram": {"enabled": True}, "inventado": {}})

    def test_save_rejects_unknown_platform_field(self):
        doc = default_config()
        doc["telegram"]["inventado"] = "x"
        with self.assertRaises(ValueError):
            save_config(self.tmp, doc)

    def test_save_rejects_malformed_endpoint(self):
        doc = default_config()
        doc["webhook"]["endpoints"] = [{"name": "", "url": "https://ok"}]
        with self.assertRaises(ValueError):
            save_config(self.tmp, doc)
        doc["webhook"]["endpoints"] = [{"name": "x", "url": "ftp://ruim"}]
        with self.assertRaises(ValueError):
            save_config(self.tmp, doc)

    def test_load_rejects_corrupt_json(self):
        (self.tmp / "messaging.json").write_text("{ nao-e-json", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(self.tmp)

    def test_messaging_file_is_private(self):
        save_config(self.tmp, default_config())
        modo = stat.S_IMODE((self.tmp / "messaging.json").stat().st_mode)
        self.assertEqual(modo, 0o600)

    def test_build_adapters_requires_enabled_and_secret(self):
        doc = default_config()
        doc["telegram"]["enabled"] = True
        save_config(self.tmp, doc)
        # Sem segredo no cofre, o adapter não é construído (nada de entregar em silêncio).
        self.assertEqual(build_platform_adapters(self.tmp), {})

    def test_messaging_platforms_lists_each_platform_without_secret(self):
        save_config(self.tmp, default_config())
        plataformas = messaging_platforms(self.tmp)
        self.assertEqual(
            {p["platform"] for p in plataformas}, {"telegram", "whatsapp", "slack", "webhook"}
        )
        telegram = next(p for p in plataformas if p["platform"] == "telegram")
        self.assertFalse(telegram["deliverable"])
        self.assertIn("vault", telegram)


if __name__ == "__main__":
    unittest.main()
