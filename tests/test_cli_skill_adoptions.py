"""Adoção e revogação pela CLI, com ledger e árvore reais."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from skill_mutation_fixtures import creation_text

REPO = Path(__file__).resolve().parents[1]
PRIVATE = "sentinela-privada-adoção-á"


class CliSkillAdoptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.source = self.root / "source.md"
        self.source.write_text(creation_text(body=PRIVATE), encoding="utf-8")

    def command(self, args, *, bootstrap=None):
        program = "from kairos_cli.main import main\n"
        if bootstrap is not None:
            program += bootstrap + "\n"
        program += "raise SystemExit(main())\n"
        result = subprocess.run(
            [sys.executable, "-c", program, *args],
            cwd=REPO,
            env={**os.environ, "KAIROS_HOME": str(self.home)},
            input="",
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertNotIn(PRIVATE, result.stdout + result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        return result.returncode, result.stdout, result.stderr

    def legacy(self, name="legacy-skill"):
        target = self.home / "skills" / name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(PRIVATE, encoding="utf-8")
        (target / "references").mkdir()
        (target / "references" / "binary.bin").write_bytes(b"\x00\xff\x80")
        (target / "references" / "binary.bin").chmod(0o640)
        return target

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

    def add(self, name="criada-pela-cli"):
        self.source.write_text(creation_text(name=name, body=PRIVATE), encoding="utf-8")
        code, output, error = self.command(["skills", "add", "--file", str(self.source), "--json"])
        self.assertEqual(code, 0, error)
        return json.loads(output)

    def test_all_three_command_forms(self):
        self.legacy("sem-guard")
        code, output, error = self.command(["skills", "adopt", "sem-guard", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adopted_without_guard = json.loads(output)
        self.assertEqual(adopted_without_guard["action"], "adopt")
        self.assertEqual(adopted_without_guard["state"], "committed")

        code, output, error = self.command(["skills", "unadopt", "sem-guard", "--yes"])
        self.assertEqual(code, 0, error)
        self.assertIn("action: unadopt", output)

        self.legacy("com-guard")
        code, output, error = self.command(["skills", "adopt", "com-guard", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption_id = json.loads(output)["adoption_id"]
        code, output, error = self.command(
            [
                "skills",
                "unadopt",
                "com-guard",
                "--yes",
                "--expect-adoption",
                adoption_id,
                "--json",
            ]
        )
        self.assertEqual(code, 0, error)
        revoked = json.loads(output)
        self.assertEqual(revoked["action"], "unadopt")
        self.assertEqual(revoked["adoption_id"], adoption_id)
        self.assertFalse(revoked["validity"]["adoption_active"])
        code, output, error = self.command(["skills", "identify", "com-guard", "--json"])
        self.assertEqual(code, 0, error)
        self.assertFalse(json.loads(output)["adoption_active"])

    def test_missing_yes_invalid_name_or_id_before_io(self):
        for args, expected in (
            (["skills", "adopt", "legacy-skill", "--json"], 77),
            (["skills", "adopt", "../private", "--yes", "--json"], 2),
            (
                ["skills", "unadopt", "legacy-skill", "--yes", "--expect-adoption", "bad"],
                2,
            ),
            (["skills", "adopt", "legacy-skill", "--yes", "--origin", "local"], 2),
            (["skills", "unadopt", "legacy-skill", "--yes", "--actor", "background"], 2),
            (["skills", "adopt", "legacy-skill", "--yes", "--force"], 2),
        ):
            with self.subTest(args=args):
                code, stdout, stderr = self.command(args)
                self.assertEqual(code, expected)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
                self.assertFalse(self.home.exists())

    def test_json_current_ids_lineage_and_validity(self):
        installed = self.legacy()
        installed_id = installed.stat().st_ino
        code, output, error = self.command(["skills", "adopt", "legacy-skill", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        metadata = json.loads(output)
        self.assertEqual(metadata["action"], "adopt")
        self.assertEqual(metadata["state"], "committed")
        self.assertEqual(metadata["operation_id"], metadata["id"])
        self.assertEqual(metadata["installation_id"], metadata["lineage"]["installation_id"])
        self.assertEqual(metadata["adoption_id"], metadata["lineage"]["adoption_id"])
        self.assertEqual(metadata["installation_id"], metadata["adoption_id"])
        self.assertEqual(metadata["historical_creation_origin"], "unknown")
        self.assertEqual(metadata["installation_source"], "unknown")
        self.assertEqual(metadata["authorization_basis"], "user_adoption")
        self.assertEqual(metadata["identity_valid"], True)
        self.assertEqual(metadata["validity"], {"identity_valid": True, "adoption_active": True})
        self.assertNotEqual(metadata["installation_id"], str(installed_id))
        self.assertNotIn("snapshot", output)
        self.assertNotIn("binary.bin", output)

    def test_identify_after_cli_adopt_is_read_only(self):
        self.legacy()
        code, output, error = self.command(["skills", "adopt", "legacy-skill", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption = json.loads(output)
        before = self.home_inventory()

        code, output, error = self.command(["skills", "identify", "legacy-skill", "--json"])

        self.assertEqual(code, 0, error)
        identified = json.loads(output)
        self.assertEqual(identified["adoption_id"], adoption["adoption_id"])
        self.assertTrue(identified["adoption_active"])
        self.assertEqual(self.home_inventory(), before)

    def test_expected_A_B_exit_1_without_revocation(self):
        self.legacy()
        code, output, error = self.command(["skills", "adopt", "legacy-skill", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption_id = json.loads(output)["adoption_id"]
        wrong_id = uuid.uuid4().hex
        code, output, error = self.command(
            [
                "skills",
                "unadopt",
                "legacy-skill",
                "--yes",
                "--expect-adoption",
                wrong_id,
                "--json",
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertTrue(error)
        self.assertNotIn(wrong_id, error)
        code, output, error = self.command(["skills", "identify", "legacy-skill", "--json"])
        self.assertEqual(code, 0, error)
        current = json.loads(output)
        self.assertEqual(current["adoption_id"], adoption_id)
        self.assertTrue(current["adoption_active"])
        code, output, error = self.command(["skills", "history", "--json"])
        self.assertEqual(code, 0, error)
        self.assertEqual([entry["action"] for entry in json.loads(output)], ["adopt"])

    def test_sanitized_errors_and_exit_codes(self):
        code, output, error = self.command(["skills", "adopt", "legacy-skill", "--yes", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertTrue(error)

        self.legacy()
        code, output, error = self.command(
            ["skills", "adopt", "legacy-skill", "--yes", "--json"],
            bootstrap=(
                "from contextlib import contextmanager\n"
                "from kairos_skills import adoptions\n"
                "from kairos_skills.mutation_contract import SkillMutationError\n"
                "@contextmanager\n"
                "def unavailable(*args, **kwargs):\n"
                "    raise SkillMutationError('unavailable', 'private backend path')\n"
                "adoptions.skill_mutation_lock = unavailable"
            ),
        )
        self.assertEqual(code, 69)
        self.assertEqual(output, "")
        self.assertNotIn("private backend path", error)
        self.assertTrue((self.home / "skills" / "legacy-skill").is_dir())

    def test_history_all_families_global_order(self):
        created = self.add("criada-primeiro")
        self.assertEqual(self.command(["skills", "rollback", created["id"], "--json"])[0], 0)
        self.legacy("adotada")
        code, output, error = self.command(["skills", "adopt", "adotada", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption_id = json.loads(output)["adoption_id"]
        code, output, error = self.command(["skills", "remove", "adotada", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        removed_id = json.loads(output)["operation_id"]
        self.assertEqual(self.command(["skills", "rollback", removed_id, "--json"])[0], 0)
        self.assertEqual(self.command(["skills", "unadopt", "adotada", "--yes"])[0], 0)

        code, output, error = self.command(["skills", "history", "--json"])
        self.assertEqual(code, 0, error)
        history = json.loads(output)
        self.assertEqual(
            [record["action"] for record in history],
            ["unadopt", "restore", "remove", "adopt", "rollback", "create"],
        )
        adopted, revoked = history[3], history[0]
        self.assertEqual(adopted["adoption_id"], adoption_id)
        self.assertEqual(adopted["authorization_basis"], "user_adoption")
        self.assertTrue(adopted["adoption_active"])
        self.assertEqual(revoked["adoption_id"], adoption_id)
        self.assertEqual(revoked["authorization_basis"], "none")
        self.assertFalse(revoked["adoption_active"])
        for record in (adopted, revoked):
            self.assertEqual(record["historical_creation_origin"], "unknown")
            self.assertEqual(record["installation_source"], "unknown")
            self.assertIn("operation_id", record)
            self.assertIn("installation_id", record)
            self.assertIn("lineage", record)

    def test_rollback_adopt_unadopt_refuses(self):
        installed = self.legacy()
        original = (installed / "SKILL.md").read_bytes()
        code, output, error = self.command(["skills", "adopt", "legacy-skill", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption_id = json.loads(output)["operation_id"]
        code, output, error = self.command(["skills", "rollback", adoption_id, "--json"])
        self.assertEqual(code, 77)
        self.assertEqual(output, "")
        self.assertIn("unadopt", error)
        self.assertEqual((installed / "SKILL.md").read_bytes(), original)

        code, output, error = self.command(["skills", "unadopt", "legacy-skill", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        unadoption_id = json.loads(output)["operation_id"]
        code, output, error = self.command(["skills", "rollback", unadoption_id, "--json"])
        self.assertEqual(code, 77)
        self.assertEqual(output, "")
        self.assertIn("adopt", error)
        self.assertEqual((installed / "SKILL.md").read_bytes(), original)

    def test_independent_proof_is_returned_without_new_adoption(self):
        created = self.add("prova-independente")
        code, output, error = self.command(
            ["skills", "adopt", "prova-independente", "--yes", "--json"]
        )
        self.assertEqual(code, 0, error)
        metadata = json.loads(output)
        self.assertIsNone(metadata["record"])
        self.assertIsNone(metadata["operation_id"])
        self.assertIsNone(metadata["adoption_id"])
        self.assertEqual(metadata["installation_id"], created["id"])
        self.assertEqual(metadata["authorization_basis"], "proven_write")
        code, output, error = self.command(["skills", "history", "--json"])
        self.assertEqual(code, 0, error)
        self.assertEqual([entry["action"] for entry in json.loads(output)], ["create"])


if __name__ == "__main__":
    unittest.main()
