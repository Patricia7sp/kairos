"""Contrato CLI: confirmação, efeito real e erro parcial sanitizado."""

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

from kairos_cli.main import main


class CLIPluginRemovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"

    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"KAIROS_HOME": str(self.home)}),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            result = main(args)
        return result, out.getvalue(), err.getvalue()

    def plugin(self):
        target = self.home / "plugins/broken"
        target.mkdir(parents=True)
        (target / "bad.py").write_text("raise RuntimeError('loaded')")
        (self.home / "config.yaml").write_text("broken: [")
        return target

    def test_no_confirmation_no_home(self):
        code, out, err = self.invoke(["plugins", "remove", "broken"])
        self.assertEqual(code, 77)
        self.assertFalse(self.home.exists())
        self.assertEqual(out, "")
        self.assertIn("--yes", err)

    def test_success_json_and_no_config_loader(self):
        target = self.plugin()
        with (
            patch("kairos_cli.config.load_config", side_effect=AssertionError("config")),
            patch("kairos_plugins.load_plugins", side_effect=AssertionError("loader")),
        ):
            code, out, err = self.invoke(["--json", "plugins", "remove", "broken", "--yes"])
        self.assertEqual(code, 0, err)
        self.assertEqual(
            json.loads(out), {"nome": "broken", "removido": True, "requer_reinicio": True}
        )
        self.assertFalse(target.exists())
        self.assertEqual(err, "")

    def test_invalid_name_usage(self):
        code, out, err = self.invoke(["plugins", "remove", "../bad", "--yes"])
        self.assertEqual(code, 2)
        self.assertFalse(self.home.exists())
        self.assertEqual(out, "")
        self.assertNotIn("../bad", err)

    def test_unsafe_installed_state_is_operational_error(self):
        target = self.plugin()
        for kind in ("fifo", "hardlink", "mode"):
            with self.subTest(kind=kind):
                unsafe = target / kind
                if kind == "fifo":
                    os.mkfifo(unsafe)
                elif kind == "hardlink":
                    os.link(target / "bad.py", unsafe)
                else:
                    unsafe.write_bytes(b"private")
                    unsafe.chmod(0o4600)
                code, out, err = self.invoke(["plugins", "remove", "broken", "--yes"])
                preserved = unsafe.exists()
                unsafe.unlink()
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertFalse(json.loads(err)["retirado"])
                self.assertTrue(preserved)

    def test_terminal_metadata_rejects_impossible_posix_paths(self):
        target = self.plugin()
        (target / "bad.py").unlink()
        filename = os.fsdecode(b"asset-\xff\\literal")
        (target / filename).write_bytes(b"asset")
        arguments = ["plugins", "remove", "broken", "--yes", "--json"]
        for _ in range(2):
            code, out, err = self.invoke(arguments)
            self.assertEqual(code, 0, err)
            self.assertTrue(json.loads(out)["removido"])
        metadata = next((self.home / ".plugins-retired").glob("*/metadata.json"))
        original = json.loads(metadata.read_text())
        self.assertIn(filename, [entry["path"] for entry in original["entries"]])
        for path in ("\ud800", "\udc7f", "é" * 128):
            with self.subTest(path=repr(path)):
                value = json.loads(json.dumps(original))
                value["entries"][1]["path"] = path
                metadata.write_text(json.dumps(value, ensure_ascii=True))
                code, out, err = self.invoke(arguments)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertIn("residuo_id", json.loads(err))
                self.assertNotIn("Traceback", err)

    def test_invalid_name_in_private_metadata_is_operational_error(self):
        self.plugin()
        arguments = ["plugins", "remove", "broken", "--yes"]
        self.assertEqual(self.invoke(arguments)[0], 0)
        metadata = next((self.home / ".plugins-retired").glob("*/metadata.json"))
        value = json.loads(metadata.read_text())
        value["name"] = "../private-secret"
        metadata.write_text(json.dumps(value))
        code, out, err = self.invoke(arguments)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertNotIn("private-secret", err)

    def test_partial_stderr_json_no_success(self):
        self.plugin()
        with patch(
            "kairos_plugins.removal.delete_verified_tree", side_effect=OSError("SECRET\nbackend")
        ):
            code, out, err = self.invoke(["plugins", "remove", "broken", "--yes", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        failure = json.loads(err)
        self.assertTrue(failure["retirado"])
        self.assertIn("residuo_id", failure)
        self.assertNotIn("removido", failure)
        self.assertNotIn("SECRET", err)

    def test_remove_import_does_not_import_loader(self):
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import kairos_plugins.removal; assert 'kairos_plugins.loader' not in sys.modules",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode())

    def test_absent_name_is_error_without_success_output(self):
        self.home.mkdir()
        code, out, err = self.invoke(["plugins", "remove", "absent", "--yes"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertFalse(json.loads(err)["retirado"])

    def test_real_cli_remove_does_not_import_loader(self):
        target = self.plugin()
        code = "import sys; from kairos_cli.main import main; result=main(['plugins','remove','broken','--yes']); assert result==0; assert 'kairos_plugins.loader' not in sys.modules"
        process = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "KAIROS_HOME": str(self.home)},
            capture_output=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertFalse(target.exists())
