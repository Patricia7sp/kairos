"""Contratos de leitura e remoção administrativa MCP."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from kairos_mcp import admin


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = self.home / "config.yaml"

    def write(self, data):
        self.path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def test_list_missing_and_invalid_entries_without_secret_disclosure(self):
        self.assertEqual(admin.list_servers(self.home), [])
        self.write(
            {
                "mcp_servers": {
                    "ok": {"command": "python"},
                    "bad": {"command": "bash", "args": ["SECRET"]},
                    "remote": {"transport": "http", "url": "https://SECRET"},
                }
            }
        )
        rows = admin.list_servers(self.home)
        self.assertEqual([r["nome"] for r in rows], ["bad", "ok", "remote"])
        self.assertEqual([r["valido"] for r in rows], [False, True, False])
        self.assertNotIn("SECRET", str(rows))

    def test_malformed_structures_and_duplicate_keys_are_refused(self):
        for text in (
            "[oops",
            "- item",
            "mcp_servers: []",
            "mcp_servers:\n  x: {}\n  x: {}",
            "date: 2026-99-99",
        ):
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(admin.McpAdminError):
                    admin.list_servers(self.home)

    def test_remove_preserves_other_config_cache_and_permissions(self):
        self.write(
            {"other": {"secret": "kept"}, "mcp_servers": {"bad": 42, "ok": {"command": "python"}}}
        )
        self.path.chmod(0o640)
        cache = self.home / "mcp"
        cache.mkdir()
        (cache / "bad.json").write_text("discard")
        (cache / "ok.json").write_text("keep")
        self.assertTrue(admin.remove_server(self.home, "bad"))
        self.assertEqual(
            yaml.safe_load(self.path.read_text()),
            {"other": {"secret": "kept"}, "mcp_servers": {"ok": {"command": "python"}}},
        )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertFalse((cache / "bad.json").exists())
        self.assertEqual((cache / "ok.json").read_text(), "keep")

    def test_remove_preserves_other_mapping_shared_by_yaml_alias(self):
        self.path.write_text(
            "mcp_servers: &servers\n"
            "  alvo: {command: python}\n"
            "  outro: {command: python}\n"
            "backup_servers: *servers\n",
            encoding="utf-8",
        )
        before = yaml.safe_load(self.path.read_text())
        self.assertFalse(admin.remove_server(self.home, "alvo"))
        after = yaml.safe_load(self.path.read_text())
        self.assertEqual(after["backup_servers"], before["backup_servers"])
        self.assertEqual(after["mcp_servers"], {"outro": before["mcp_servers"]["outro"]})

    def test_recursive_yaml_alias_refused_before_config_or_cache_mutation(self):
        original = "&root\nmcp_servers:\n  alvo: {command: python}\nbackup: *root\n"
        self.path.write_text(original, encoding="utf-8")
        cache = self.home / "mcp"
        cache.mkdir()
        (cache / "alvo.json").write_text("keep", encoding="utf-8")
        with self.assertRaises(admin.McpAdminError):
            admin.remove_server(self.home, "alvo")
        self.assertEqual(self.path.read_text(), original)
        self.assertEqual((cache / "alvo.json").read_text(), "keep")

    def test_invalid_uppercase_transport_has_normalized_safe_diagnostic(self):
        for transport in ("STDIO", "HTTP", "SSE"):
            with self.subTest(transport=transport):
                self.write({"mcp_servers": {"bad": {"transport": transport, "env": "SECRET"}}})
                row = admin.list_servers(self.home)[0]
                self.assertFalse(row["valido"])
                self.assertEqual(row["transporte"], transport.lower())
                self.assertNotIn("SECRET", str(row))

    def test_unsafe_names_and_symlinks_refuse_before_mutation(self):
        self.write({"mcp_servers": {"ok": {}}})
        original = self.path.read_bytes()
        for name in ("../outside", "..", ".hidden", "a/b", "a\\b", ""):
            with self.subTest(name=name), self.assertRaises(admin.McpAdminError):
                admin.remove_server(self.home, name)
        (self.home / "mcp").mkdir()
        (self.home / "mcp" / "ok.json").symlink_to(self.path)
        with self.assertRaises(admin.McpAdminError):
            admin.remove_server(self.home, "ok")
        self.assertEqual(self.path.read_bytes(), original)
        self.path.rename(self.home / "real.yaml")
        self.path.symlink_to(self.home / "real.yaml")
        with self.assertRaises(admin.McpAdminError):
            admin.list_servers(self.home)

    def test_test_failure_is_safe_and_remote_transport_is_refused(self):
        self.write(
            {
                "mcp_servers": {
                    "missing": {"command": "/absent/SECRET"},
                    "remote": {"transport": "sse", "url": "https://SECRET"},
                }
            }
        )
        for name in ("missing", "remote", "absent"):
            with self.subTest(name=name), self.assertRaises(admin.McpAdminError) as caught:
                admin.test_server(self.home, name)
            self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse((self.home / "mcp").exists())

    def test_concurrent_change_refused_without_overwriting(self):
        self.write({"mcp_servers": {"ok": {}}})
        original_mkstemp = tempfile.mkstemp

        def concurrent_write(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            self.write({"mcp_servers": {"ok": {}}, "concurrent": "preserved"})
            return result

        with (
            patch("kairos_mcp.admin.tempfile.mkstemp", side_effect=concurrent_write),
            self.assertRaises(admin.McpAdminError),
        ):
            admin.remove_server(self.home, "ok")
        self.assertEqual(yaml.safe_load(self.path.read_text())["concurrent"], "preserved")
        self.assertIn("ok", yaml.safe_load(self.path.read_text())["mcp_servers"])

    def test_existing_numeric_env_and_uppercase_transport_work_in_real_process(self):
        server = self.home / "probe.py"
        server.write_text(
            "import json, os, sys\n"
            "for line in sys.stdin:\n"
            "    msg = json.loads(line)\n"
            "    if 'id' not in msg: continue\n"
            "    result = {}\n"
            "    if msg['method'] == 'tools/list':\n"
            "        result = {'tools': [{'name': os.environ['PORT'] + '_' + os.environ['RATE']}]}\n"
            "    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}), flush=True)\n"
        )
        self.write(
            {
                "mcp_servers": {
                    "local": {
                        "transport": "STDIO",
                        "command": sys.executable,
                        "args": [str(server)],
                        "env": {"PORT": 1234, "RATE": 1.5},
                    }
                }
            }
        )
        self.assertTrue(admin.list_servers(self.home)[0]["valido"])
        self.assertEqual(admin.test_server(self.home, "local"), [{"name": "1234_1.5"}])

    def test_boolean_env_is_refused_as_invalid(self):
        self.write({"mcp_servers": {"local": {"command": sys.executable, "env": {"FLAG": True}}}})
        self.assertFalse(admin.list_servers(self.home)[0]["valido"])
        with self.assertRaises(admin.McpAdminError):
            admin.test_server(self.home, "local")
