"""Remoção CLI real, snapshots duráveis e erros sem dados privados."""

import argparse
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text

from kairos_cli.main import main
from kairos_cli.skill_mutations import run_skill_mutation
from kairos_skills.catalog import catalog_home_id
from kairos_state import connect
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class CliRemovalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.source = self.root / "source.md"
        self.source.write_text(creation_text(body="private fictitious removal body"))
        env = patch.dict(os.environ, {"KAIROS_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def command(self, args):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            try:
                code = main(args)
            except SystemExit as failure:
                code = failure.code
        self.assertNotIn("private fictitious removal body", out.getvalue() + err.getvalue())
        self.assertNotIn(str(self.root), out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def add(self):
        code, output, err = self.command(["skills", "add", "--file", str(self.source), "--json"])
        self.assertEqual(code, 0, err)
        return json.loads(output)

    def test_cli_remove_confirmation_has_no_effect(self):
        code, output, err = self.command(["skills", "remove", "revisar-docs", "--json"])
        self.assertEqual(code, 77)
        self.assertEqual(output, "")
        self.assertTrue(err)
        self.assertFalse(self.home.exists())
        self.add()
        before = (self.home / "state.db").read_bytes()
        lock = self.home / ".skills-write.lock"
        lock.unlink()
        installed = self.home / "skills" / "revisar-docs"
        literal = (installed / "SKILL.md").read_bytes()
        self.assertEqual(self.command(["skills", "remove", "revisar-docs"])[0], 77)
        self.assertEqual((installed / "SKILL.md").read_bytes(), literal)
        self.assertEqual((self.home / "state.db").read_bytes(), before)
        self.assertFalse(lock.exists())

    def test_cli_remove_restore_json_and_history(self):
        created = self.add()
        installed = self.home / "skills" / "revisar-docs"
        refs = installed / "references"
        refs.mkdir()
        asset = refs / "private.bin"
        asset.write_bytes(b"\x00\xff\x80")
        asset.chmod(0o640)
        (installed / "empty").mkdir(mode=0o750)
        original = (installed / "SKILL.md").read_bytes()
        code, output, err = self.command(["skills", "remove", "revisar-docs", "--yes", "--json"])
        self.assertEqual(code, 0, err)
        removed = json.loads(output)
        self.assertEqual(removed["nome"], "revisar-docs")
        self.assertEqual(removed["action"], "remove")
        self.assertEqual(removed["state"], "committed")
        self.assertTrue(removed["removido"])
        self.assertTrue(removed["restauravel"])
        self.assertFalse(installed.exists())
        code, history, err = self.command(["skills", "history", "--json"])
        self.assertEqual(code, 0, err)
        records = json.loads(history)
        self.assertEqual(
            [record["id"] for record in records], [removed["operation_id"], created["id"]]
        )
        self.assertEqual(records[0]["action"], "remove")
        code, output, err = self.command(["skills", "rollback", removed["operation_id"], "--json"])
        self.assertEqual(code, 0, err)
        restored = json.loads(output)
        self.assertEqual(restored["action"], "restore")
        self.assertEqual(restored["reverts"], removed["operation_id"])
        self.assertNotEqual(restored["operation_id"], removed["operation_id"])
        self.assertEqual((installed / "SKILL.md").read_bytes(), original)
        self.assertEqual(asset.read_bytes(), b"\x00\xff\x80")
        self.assertEqual(asset.stat().st_mode & 0o777, 0o640)
        self.assertEqual((installed / "empty").stat().st_mode & 0o777, 0o750)
        with connect(self.home / "state.db") as db:
            repo = SkillMutationRepository(db, home_id=catalog_home_id(self.home))
            self.assertEqual(
                repo.snapshot(removed["operation_id"]).contents["references/private.bin"],
                b"\x00\xff\x80",
            )

    def test_cli_exit_codes_and_redaction(self):
        for args in (
            ["skills", "remove", "../private", "--yes"],
            ["skills", "remove", "UPPER", "--yes"],
            ["skills", "remove", "revisar-docs", "--actor", "background", "--yes"],
        ):
            with self.subTest(args=args):
                self.assertEqual(self.command(args)[0], 2)
                self.assertFalse(self.home.exists())
        self.add()
        with patch(
            "kairos_skills.mutation_io.ctypes.CDLL", side_effect=OSError("private backend path")
        ):
            code, output, err = self.command(
                ["skills", "remove", "revisar-docs", "--yes", "--json"]
            )
        self.assertEqual(code, 69)
        self.assertEqual(output, "")
        self.assertNotIn("private backend path", err)
        self.assertTrue((self.home / "skills" / "revisar-docs").exists())

    def test_unknown_restore_id_refused(self):
        unknown = uuid.uuid4().hex
        code, output, err = self.command(["skills", "rollback", unknown, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn(unknown, err)
        self.assertFalse(self.home.exists())

    def test_direct_composition_confirmation_precedes_io(self):
        args = argparse.Namespace(
            skills_command="remove", name="revisar-docs", yes=False, json=True
        )
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            self.assertEqual(run_skill_mutation(self.home, args), 77)
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(self.home.exists())

    def test_catalog_reflects_remove_restore_without_changing_started_turn(self):
        from kairos_skills.catalog import catalog_asset
        from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset
        from kairos_state.repositories import SessionRepository, SkillCatalogRepository

        self.add()
        installed = self.home / "skills" / "revisar-docs"
        reference = installed / "references" / "guide.md"
        reference.parent.mkdir()
        reference.write_text("cached private reference")
        self.source.write_text(creation_text(name="other-skill"))
        self.assertEqual(self.command(["skills", "add", "--file", str(self.source)])[0], 0)
        with connect(self.home / "state.db") as db:
            SessionRepository(db).ensure("started-turn", source="cli")
            catalogs = SkillCatalogRepository(db)
            original = catalogs.create_if_absent("started-turn", capture_skill_catalog(self.home))
            body = catalog_asset(original, "revisar-docs")
            asset = catalog_asset(original, "revisar-docs", "references/guide.md")
            original_body = read_catalog_asset(self.home, body)
            catalogs.put_asset("started-turn", body, original_body)
            catalogs.put_asset("started-turn", asset, "cached private reference")
            before = catalogs.export_session("started-turn")
            code, output, err = self.command(
                ["skills", "remove", "revisar-docs", "--yes", "--json"]
            )
            self.assertEqual(code, 0, err)
            removed = json.loads(output)
            self.assertEqual(
                [entry.name for entry in capture_skill_catalog(self.home).entries], ["other-skill"]
            )
            self.assertEqual(catalogs.export_session("started-turn"), before)
            self.assertEqual(catalogs.get_asset("started-turn", body), original_body)
            self.assertEqual(catalogs.get_asset("started-turn", asset), "cached private reference")
            self.assertEqual(self.command(["skills", "rollback", removed["operation_id"]])[0], 0)
            self.assertIn(
                "revisar-docs", [entry.name for entry in capture_skill_catalog(self.home).entries]
            )
            self.assertEqual(catalogs.export_session("started-turn"), before)

    def test_occupied_restore_refuses_success_and_preserves_current_tree(self):
        self.add()
        code, output, err = self.command(["skills", "remove", "revisar-docs", "--yes", "--json"])
        self.assertEqual(code, 0, err)
        removed = json.loads(output)
        self.source.write_text(creation_text(body="later installation"))
        newer = self.add()
        installed = self.home / "skills" / "revisar-docs" / "SKILL.md"
        before = installed.read_bytes()
        code, output, err = self.command(["skills", "rollback", removed["operation_id"], "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn(newer["id"], err)
        self.assertEqual(installed.read_bytes(), before)

    def test_history_partial_modern_schema_is_refused_without_migration(self):
        import sqlite3

        self.home.mkdir()
        path = self.home / "state.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE schema_version(version INTEGER)")
            db.execute("INSERT INTO schema_version VALUES (8)")
        before = path.read_bytes()
        code, output, err = self.command(["skills", "history", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertTrue(err)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({entry.name for entry in self.home.iterdir()}, {"state.db"})
