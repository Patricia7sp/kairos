"""Administração MCP pela CLI com configuração e efeitos reais."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from kairos_cli.main import main


class McpAdminCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"KAIROS_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def config(self, servers):
        (self.home / "config.yaml").write_text(
            yaml.safe_dump({"mcp_servers": servers, "outro": {"preservar": True}}),
            encoding="utf-8",
        )

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_list_sem_config_emite_lista_vazia(self):
        code, out, err = self.run_cli("mcp", "list", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [])

    def test_list_configurado_omite_credenciais_e_nao_inicia_servidor(self):
        marker = self.home / "spawned"
        server = self.home / "probe.py"
        server.write_text(f"open({str(marker)!r}, 'w').close()", encoding="utf-8")
        self.config(
            {
                "local": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(server)],
                    "env": {"TOKEN": "segredo-cli"},
                }
            }
        )
        code, out, err = self.run_cli("--json", "mcp", "list")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            json.loads(out), [{"nome": "local", "transporte": "stdio", "valido": True}]
        )
        self.assertNotIn("segredo-cli", out + err)
        self.assertFalse(marker.exists())

    def test_test_descobre_nomes_sem_expor_schema_e_sem_escrever_cache(self):
        fixture = Path("tests/fake_mcp_server.py")
        self.config(
            {"local": {"transport": "stdio", "command": sys.executable, "args": [str(fixture)]}}
        )
        code, out, err = self.run_cli("mcp", "test", "local", "--json")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["nome"], "local")
        self.assertIn("sum", result["ferramentas"])
        self.assertTrue(all(isinstance(name, str) for name in result["ferramentas"]))
        self.assertEqual(set(result), {"nome", "ferramentas"})
        self.assertFalse((self.home / "mcp" / "local.json").exists())

    def test_remove_preserva_outros_servidores_e_configuracao(self):
        self.config({"alvo": {}, "outro": {"transport": "stdio", "command": "python"}})
        cache = self.home / "mcp"
        cache.mkdir()
        (cache / "alvo.json").write_text("[]", encoding="utf-8")
        (cache / "outro.json").write_text("[]", encoding="utf-8")
        code, out, err = self.run_cli("mcp", "remove", "alvo", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            json.loads(out), {"nome": "alvo", "removido": True, "cache_removido": True}
        )
        saved = yaml.safe_load((self.home / "config.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("alvo", saved["mcp_servers"])
        self.assertIn("outro", saved["mcp_servers"])
        self.assertTrue(saved["outro"]["preservar"])
        self.assertFalse((cache / "alvo.json").exists())
        self.assertTrue((cache / "outro.json").exists())

    def test_operacoes_recusam_servidor_ausente_com_exit_um(self):
        for action in ("test", "remove"):
            with self.subTest(action=action):
                code, out, err = self.run_cli("mcp", action, "ausente", "--json")
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.strip())

    def test_list_expoe_entrada_invalida_para_correcao_sem_revelar_url(self):
        self.config({"quebrado": {"transport": "http", "url": "segredo-cli"}})
        code, out, err = self.run_cli("mcp", "list", "--json")
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual(rows[0]["nome"], "quebrado")
        self.assertFalse(rows[0]["valido"])
        self.assertTrue(rows[0]["erro"])
        self.assertNotIn("segredo-cli", out + err)

    def test_config_malformada_recusada_sem_expor_conteudo(self):
        (self.home / "config.yaml").write_text("mcp_servers: [segredo-cli", encoding="utf-8")
        code, out, err = self.run_cli("mcp", "list", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())
        self.assertNotIn("segredo-cli", err)

    def test_remove_sem_cache_confirma_remocao_real(self):
        self.config({"alvo": {}})
        code, out, err = self.run_cli("mcp", "remove", "alvo", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            json.loads(out), {"nome": "alvo", "removido": True, "cache_removido": False}
        )
        code, out, err = self.run_cli("mcp", "list", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [])

    def test_nome_obrigatorio_para_test_e_remove(self):
        for action in ("test", "remove"):
            with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(["mcp", action])
                self.assertEqual(caught.exception.code, 2)

    def test_list_nao_carrega_kairos_tools_em_processo_limpo(self):
        script = (
            "import sys; from kairos_cli.main import main; "
            "code = main(['mcp', 'list', '--json']); "
            "assert not any(n == 'kairos_tools' or n.startswith('kairos_tools.') for n in sys.modules); "
            "raise SystemExit(code)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=15, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
