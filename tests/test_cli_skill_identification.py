"""Identify na CLI consulta instalações reais e conserva o home observado."""

import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text

from kairos_cli.main import main
from kairos_skills import identification
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError


class CliSkillIdentificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.name = "revisar-docs"
        self.private_marker = "sentinela-privada-identificação-á"
        self.source = self.root / "private-source.md"
        self.source.write_text(creation_text(body=self.private_marker), encoding="utf-8")
        env = patch.dict(os.environ, {"KAIROS_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def assert_private(self, stdout, stderr):
        output = stdout + stderr
        self.assertNotIn(self.private_marker, output)
        self.assertNotIn(str(self.root), output)

    def command(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            try:
                code = main(args)
            except SystemExit as error:
                code = error.code
        self.assert_private(stdout.getvalue(), stderr.getvalue())
        return code, stdout.getvalue(), stderr.getvalue()

    def subprocess_command(self, args):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from kairos_cli.main import main; raise SystemExit(main())",
                *args,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "KAIROS_HOME": str(self.home)},
            input="",
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assert_private(result.stdout, result.stderr)
        return result.returncode, result.stdout, result.stderr

    def home_inventory(self):
        if not self.home.exists():
            return None
        inventory = {}
        for path in (self.home, *sorted(self.home.rglob("*"))):
            info = path.lstat()
            inventory[str(path.relative_to(self.home))] = (
                info.st_mode,
                info.st_dev,
                info.st_ino,
                info.st_nlink,
                info.st_mtime_ns,
                info.st_ctime_ns,
                path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
            )
        return inventory

    def legacy(self, *, name=None, text=None):
        target = self.home / "skills" / (name or self.name)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(text or self.private_marker, encoding="utf-8")
        (target / "empty").mkdir(mode=0o750)
        return target

    def add(self, *, command=None):
        command = command or self.command
        code, stdout, stderr = command(["skills", "add", "--file", str(self.source), "--json"])
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)

    def observe(self, *, as_json=True, command=None):
        command = command or self.command
        before = self.home_inventory()
        code, stdout, stderr = command(
            ["skills", "identify", self.name, *(["--json"] if as_json else [])]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(self.home_inventory(), before)
        self.assertNotIn(catalog_home_id(self.home), stdout)
        self.assertNotIn(hashlib.sha256(self.source.read_bytes()).hexdigest(), stdout)
        return json.loads(stdout) if as_json else stdout

    def test_cli_unknown_has_successful_negative_diagnostic(self):
        self.legacy()
        metadata = self.observe()
        self.assertEqual(
            metadata,
            {
                "name": self.name,
                "historical_creation_origin": "unknown",
                "installation_source": "unknown",
                "authorization_basis": "none",
                "ownership": "user",
                "current_installation_id": None,
                "historical_creation_id": None,
                "current_removal_id": None,
                "pending_operation_ids": [],
                "identity_valid": None,
                "known_protection": [],
                "protection_coverage": "namespace_and_recorded_only",
                "removivel": False,
                "reasons": ["unknown_origin"],
                "adoption_id": None,
                "adoption_active": None,
            },
        )
        self.assertFalse((self.home / "state.db").exists())

    def test_created_user_exposes_real_ids_without_content_or_home_identity(self):
        created = self.add()
        lock = self.home / ".skills-write.lock"
        lock.unlink()
        metadata = self.observe()
        self.assertEqual(
            metadata,
            {
                "name": self.name,
                "historical_creation_origin": "user",
                "installation_source": "local",
                "authorization_basis": "proven_write",
                "ownership": "user",
                "current_installation_id": created["id"],
                "historical_creation_id": created["id"],
                "current_removal_id": None,
                "pending_operation_ids": [],
                "identity_valid": True,
                "known_protection": [],
                "protection_coverage": "namespace_and_recorded_only",
                "removivel": True,
                "reasons": [],
                "adoption_id": None,
                "adoption_active": None,
            },
        )
        self.assertFalse(lock.exists())

    def test_text_explains_dimensions_identity_reasons_and_diagnostic_limits(self):
        self.legacy()
        stdout = self.observe(as_json=False)
        for field in (
            self.name,
            "historical_creation_origin",
            "installation_source",
            "authorization_basis",
            "identity_valid",
            "unknown_origin",
            "removivel",
        ):
            with self.subTest(field=field):
                self.assertIn(field, stdout)
        self.assertIn("diagnóstico", stdout.lower())
        self.assertIn("revalid", stdout.lower())
        self.assertIn("organizacional", stdout.lower())
        self.assertIn("limitada", stdout.lower())

    def test_help_exposes_identify_and_adoption_commands(self):
        code, stdout, stderr = self.command(["skills", "--help"])
        self.assertEqual((code, stderr), (0, ""))
        for command in (
            "identify",
            "add",
            "adopt",
            "history",
            "list",
            "remove",
            "rollback",
            "unadopt",
        ):
            with self.subTest(command=command):
                self.assertIn(command, stdout)
        code, stdout, stderr = self.command(["skills", "identify", "--help"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("NOME", stdout)
        self.assertIn("kebab-case", stdout)
        self.assertIn("somente leitura", stdout)
        self.assertFalse(self.home.exists())
        code, stdout, stderr = self.command(["skills", "adopt", "--help"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("NOME", stdout)
        self.assertIn("--yes", stdout)
        self.assertNotIn("--expect-adoption", stdout)
        self.assertFalse(self.home.exists())
        code, stdout, stderr = self.command(["skills", "unadopt", "--help"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("NOME", stdout)
        self.assertIn("--yes", stdout)
        self.assertIn("--expect-adoption ID", stdout)
        self.assertFalse(self.home.exists())

    def test_identify_requires_name_and_rejects_unknown_options_without_io(self):
        for args in (
            ["skills", "identify", "--json"],
            ["skills", "identify", self.name, "--unknown", "--json"],
            ["skills", "identify", self.name, "--yes", "--json"],
        ):
            with self.subTest(args=args):
                code, stdout, stderr = self.command(args)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
                self.assertFalse(self.home.exists())

    def test_invalid_name_and_org_are_rejected_before_reader_io(self):
        for name in ("../fora", "UPPER", "_org", "nome_com_underscore"):
            with (
                self.subTest(name=name),
                patch.object(
                    identification,
                    "database_connection",
                    side_effect=AssertionError("reader não deveria ser aberto"),
                ),
            ):
                code, stdout, stderr = self.command(["skills", "identify", name, "--json"])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("kebab-case", stderr)
                self.assertFalse(self.home.exists())

    def test_list_shows_legacy_name_but_identify_rejects_before_io(self):
        name = "Legacy_Skill"
        self.legacy(name=name)
        before = self.home_inventory()
        code, stdout, stderr = self.command(["skills", "list", "--json"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn(name, json.loads(stdout))
        with patch.object(
            identification,
            "database_connection",
            side_effect=AssertionError("reader não deveria ser aberto"),
        ):
            code, stdout, stderr = self.command(["skills", "identify", name, "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("kebab-case", stderr)
        self.assertEqual(self.home_inventory(), before)

    def test_valid_name_with_legacy_frontmatter_without_semver_is_identified(self):
        self.legacy(
            text=f"---\nname: {self.name}\ndescription: Legado.\n---\n{self.private_marker}\n"
        )
        metadata = self.observe()
        self.assertEqual(metadata["authorization_basis"], "none")
        self.assertEqual(metadata["historical_creation_origin"], "unknown")
        self.assertFalse(metadata["removivel"])

    def test_missing_home_returns_one_without_creating_parents(self):
        self.home = self.root / "private-parent" / "missing-home"
        with patch.dict(os.environ, {"KAIROS_HOME": str(self.home)}):
            code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr)
        self.assertFalse(self.home.parent.exists())

    def test_missing_target_preserves_existing_home(self):
        self.legacy(name="outra-skill")
        before = self.home_inventory()
        code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("ausente", stderr.lower())
        self.assertEqual(self.home_inventory(), before)

    def test_corrupt_ledger_is_sanitized_error_without_success_json(self):
        self.legacy()
        (self.home / "state.db").write_text(self.private_marker, encoding="utf-8")
        before = self.home_inventory()
        code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("ledger", stderr.lower())
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(self.home_inventory(), before)

    def test_unavailable_attestation_exits_69_without_effects(self):
        self.legacy()
        before = self.home_inventory()
        with patch("kairos_filesystem.tree.os.listxattr", side_effect=OSError(self.private_marker)):
            code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
        self.assertEqual(code, 69)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr)
        self.assertEqual(self.home_inventory(), before)

    def test_backend_errors_do_not_expose_raw_exception_values(self):
        for error_type in (OSError, sqlite3.DatabaseError):
            with (
                self.subTest(error_type=error_type),
                patch.object(
                    identification, "identify_skill", side_effect=error_type(self.private_marker)
                ),
            ):
                code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
                self.assertFalse(self.home.exists())

    def test_reader_denial_preserves_exit_77_without_success_json(self):
        with patch.object(
            identification,
            "identify_skill",
            side_effect=SkillMutationError("denied", "Consulta vetada por proteção conhecida."),
        ):
            code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
        self.assertEqual(code, 77)
        self.assertEqual(stdout, "")
        self.assertIn("vetada", stderr)
        self.assertFalse(self.home.exists())

    def test_concurrent_observation_change_exits_one_without_success_json(self):
        self.add()
        target = self.home / "skills" / self.name / "SKILL.md"
        original = identification.resolve_removal_origin
        after_change = []

        def resolve_then_edit(*args):
            result = original(*args)
            target.write_text(f"{self.private_marker}\nalteração concorrente\n", encoding="utf-8")
            after_change.append(self.home_inventory())
            return result

        with patch.object(identification, "resolve_removal_origin", resolve_then_edit):
            code, stdout, stderr = self.command(["skills", "identify", self.name, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("alterad", stderr.lower())
        self.assertIn("repita", stderr.lower())
        self.assertNotIn("corrupt", stderr.lower())
        self.assertNotIn("unknown", stderr.lower())
        self.assertTrue(after_change)
        self.assertEqual(self.home_inventory(), after_change[0])

    def test_internal_edit_keeps_proof_and_byte_identical_root_replacement_does_not(self):
        created = self.add()
        target = self.home / "skills" / self.name
        (target / "SKILL.md").write_text(self.private_marker, encoding="utf-8")
        metadata = self.observe()
        self.assertEqual(metadata["authorization_basis"], "proven_write")
        self.assertTrue(metadata["identity_valid"])
        target.rename(self.root / "previous-installation")
        shutil.copytree(self.root / "previous-installation", target)
        metadata = self.observe()
        self.assertEqual(metadata["current_installation_id"], created["id"])
        self.assertEqual(metadata["historical_creation_id"], created["id"])
        self.assertEqual(metadata["authorization_basis"], "none")
        self.assertFalse(metadata["identity_valid"])
        self.assertFalse(metadata["removivel"])
        self.assertEqual(metadata["reasons"], ["replaced_root"])

    def test_restored_installation_exposes_original_creation_and_preserves_history(self):
        created = self.add()
        code, stdout, stderr = self.command(["skills", "remove", self.name, "--yes", "--json"])
        self.assertEqual(code, 0, stderr)
        removed = json.loads(stdout)
        code, stdout, stderr = self.command(
            ["skills", "rollback", removed["operation_id"], "--json"]
        )
        self.assertEqual(code, 0, stderr)
        restored = json.loads(stdout)
        metadata = self.observe()
        self.assertEqual(metadata["current_installation_id"], restored["operation_id"])
        self.assertEqual(metadata["historical_creation_id"], created["id"])
        code, stdout, stderr = self.command(["skills", "history", "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            [entry["id"] for entry in json.loads(stdout)],
            [restored["operation_id"], removed["operation_id"], created["id"]],
        )

    def test_real_subprocess_identifies_cli_created_user_and_anonymous_legacy(self):
        created = self.add(command=self.subprocess_command)
        metadata = self.observe(command=self.subprocess_command)
        self.assertEqual(metadata["current_installation_id"], created["id"])
        self.assertEqual(metadata["historical_creation_id"], created["id"])
        self.assertEqual(metadata["authorization_basis"], "proven_write")
        self.assertTrue(metadata["identity_valid"])
        self.assertTrue(metadata["removivel"])
        self.name = "skill-legada"
        self.legacy()
        metadata = self.observe(command=self.subprocess_command)
        self.assertEqual(metadata["authorization_basis"], "none")
        self.assertFalse(metadata["removivel"])
        self.assertEqual(metadata["reasons"], ["unknown_origin"])

    def test_real_subprocess_rejects_corruption_and_unknown_option_without_success_json(self):
        self.legacy()
        (self.home / "state.db").write_text(self.private_marker, encoding="utf-8")
        before = self.home_inventory()
        for suffix, expected in ((["--json"], 1), (["--unknown", "--json"], 2)):
            with self.subTest(suffix=suffix):
                code, stdout, stderr = self.subprocess_command(
                    ["skills", "identify", self.name, *suffix]
                )
                self.assertEqual(code, expected)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
                self.assertEqual(self.home_inventory(), before)
