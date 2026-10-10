"""Administração usa protocolo real sem executar ferramentas ou outros servidores."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from kairos_mcp.admin import McpAdminError, list_servers, remove_server
from kairos_mcp.admin import test_server as probe_server


class McpAdminProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.fixture = self.home / "probe.py"
        shutil.copyfile(Path(__file__).with_name("mcp_admin_probe_server.py"), self.fixture)

    def server(self, name: str, mode: str = "ok") -> dict:
        return {
            "command": sys.executable,
            "args": [str(self.fixture), str(self.home / f"log_{name}.journal"), mode],
        }

    def configure(self, servers: dict) -> None:
        (self.home / "config.yaml").write_text(
            yaml.safe_dump({"mcp_servers": servers}), encoding="utf-8"
        )

    def events(self, name: str) -> list[dict]:
        path = self.home / f"log_{name}.journal"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def snapshot(self) -> dict:
        paths = [self.home / "config.yaml", *(self.home / "mcp").rglob("*")]
        return {
            str(path.relative_to(self.home)): path.read_bytes()
            for path in paths
            if path.is_file() and path.suffix != ".py"
        }

    def test_descobre_todas_paginas_sem_tools_call_e_so_acorda_selecionado(self) -> None:
        self.configure({"chosen": self.server("chosen"), "other": self.server("other")})
        manifests = probe_server(self.home, "chosen")
        events = self.events("chosen")
        methods = [event["method"] for event in events if event["event"] == "request"]
        self.assertIn("initialize", methods)
        self.assertIn("notifications/initialized", methods)
        self.assertNotIn("tools/call", methods)
        self.assertEqual(methods[0], "initialize")
        pages = [tool for event in events if event["event"] == "page" for tool in event["tools"]]
        self.assertEqual(manifests, pages)
        self.assertEqual({tool["name"] for tool in manifests}, {"first", "last"})
        cursors = [
            event.get("params", {}).get("cursor")
            for event in events
            if event.get("method") == "tools/list"
        ]
        self.assertEqual(cursors, [None, "second-page"])
        self.assertEqual(self.events("other"), [])

    def test_teste_nao_grava_config_nem_cache_existente_ou_novo(self) -> None:
        self.configure({"chosen": self.server("chosen")})
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    (self.home / "mcp").mkdir()
                    (self.home / "mcp/chosen.json").write_text('[{"name":"cached-old"}]')
                before = self.snapshot()
                manifests = probe_server(self.home, "chosen")
                self.assertTrue(manifests)
                self.assertEqual(self.snapshot(), before)

    def test_list_remove_nao_acordam_processos(self) -> None:
        self.configure({"chosen": self.server("chosen"), "other": self.server("other")})
        (self.home / "mcp").mkdir()
        (self.home / "mcp/chosen.json").write_text("[]")
        self.assertEqual({item["nome"] for item in list_servers(self.home)}, {"chosen", "other"})
        self.assertTrue(remove_server(self.home, "chosen"))
        self.assertEqual({item["nome"] for item in list_servers(self.home)}, {"other"})
        self.assertEqual(self.events("chosen"), [])
        self.assertEqual(self.events("other"), [])

    def test_handshake_recusado_e_morte_reportam_falha_sem_segredo(self) -> None:
        for mode in ("refuse", "die"):
            with self.subTest(mode=mode):
                self.configure({"chosen": self.server(mode, mode)})
                with self.assertRaises(McpAdminError) as error:
                    probe_server(self.home, "chosen")
                self.assertNotIn("REMOTE_SECRET_ABC", str(error.exception))
                events = self.events(mode)
                self.assertTrue(any(event["event"] == "spawn" for event in events))
                methods = [event["method"] for event in events if event["event"] == "request"]
                self.assertIn("initialize", methods)
                self.assertNotIn("tools/call", methods)
                self.assertNotIn("tools/list", methods)

    def test_remoto_e_comando_inseguro_recusados_antes_spawn(self) -> None:
        unsafe = self.server("chosen")
        unsafe["args"].append("--token=REMOTE_SECRET_ABC;touch")
        for config in (
            unsafe,
            {"command": "sh", "args": ["-c", "REMOTE_SECRET_ABC"]},
            {"transport": "http", "url": "https://example.invalid/REMOTE_SECRET_ABC"},
            {"transport": "sse", "url": "https://example.invalid/REMOTE_SECRET_ABC"},
        ):
            with self.subTest(config=config):
                self.configure({"chosen": config, "other": self.server("other")})
                with self.assertRaises(McpAdminError) as error:
                    probe_server(self.home, "chosen")
                self.assertNotIn("REMOTE_SECRET_ABC", str(error.exception))
                self.assertEqual(self.events("chosen"), [])
                self.assertEqual(self.events("other"), [])
