"""Backup inclui proteção durável e recusa família parcial sem publicar sucesso."""

import argparse
import io
import shutil
import sqlite3
import tarfile
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from kairos_cli.backup import _archive_home, run_backup
from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillDirectoryIdentity,
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationState,
    validate_skill_creation,
)
from kairos_skills.org_policy_contract import OrgPolicyReceipt, OrgProtectionBinding
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_org_protections import SkillOrgProtectionRepository


class OrgBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.connection = connect(self.home / "state.db")
        self.addCleanup(self.connection.close)
        migrate(self.connection, target=10)
        text = "---\nname: org-skill\ndescription: Fixture.\nversion: '0.1.0'\n---\nFixture.\n"
        creation = validate_skill_creation(text)
        home_id = catalog_home_id(self.home)
        operation_id = uuid.uuid4().hex
        draft = SkillMutationDraft(
            operation_id,
            home_id,
            SkillMutationAction.CREATE,
            "org-skill",
            Actor.USER_FOREGROUND,
            1.0,
            SkillDirectoryIdentity(1, 2, 1, 4),
            creation.sha256,
            creation.size_bytes,
        )
        receipt = OrgPolicyReceipt(
            home_id,
            "org-skill",
            uuid.uuid4().hex,
            "a" * 64,
            (OrgProtectionBinding("equipe-exemplo", False),),
        )
        repository = SkillMutationRepository(self.connection, home_id=home_id)
        repository.prepare(draft, text, policy_receipt=receipt)
        repository.finish(operation_id, SkillMutationState.COMMITTED)

    def snapshot(self):
        archive = self.root / "backup.tar.gz"
        _archive_home(self.home, archive)
        target = self.root / "snapshot.db"
        with tarfile.open(archive) as bundle, bundle.extractfile("state.db") as member:
            target.write_bytes(member.read())
        return target

    def test_snapshot_preserves_protection_and_does_not_transfer_to_another_home(self):
        target = self.snapshot()
        with sqlite3.connect(target) as snapshot:
            original = SkillOrgProtectionRepository(snapshot, home_id=catalog_home_id(self.home))
            self.assertEqual(original.protected_organizations("org-skill"), {"equipe-exemplo"})
            other_home = self.root / "other-home"
            other_home.mkdir()
            other = SkillOrgProtectionRepository(snapshot, home_id=catalog_home_id(other_home))
            self.assertEqual(other.protected_organizations("org-skill"), frozenset())
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_restore_to_same_home_keeps_protection_without_policy_source(self):
        target = self.snapshot()
        self.connection.close()
        for name in ("state.db", "state.db-wal", "state.db-shm"):
            (self.home / name).unlink(missing_ok=True)
        shutil.copyfile(target, self.home / "state.db")
        with sqlite3.connect(self.home / "state.db") as restored:
            repository = SkillOrgProtectionRepository(restored, home_id=catalog_home_id(self.home))
            self.assertEqual(repository.protected_organizations("org-skill"), {"equipe-exemplo"})

    def test_partial_family_refuses_public_backup_and_preserves_existing_output(self):
        self.connection.execute("DROP TABLE skill_org_events")
        self.connection.commit()
        target = self.root / "backup.tar.gz"
        target.write_bytes(b"previous-backup")
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            result = run_backup(self.home, argparse.Namespace(output=str(target), json=True))
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(target.read_bytes(), b"previous-backup")
        self.assertNotIn(str(self.home), error.getvalue())

    def test_legacy_snapshot_with_only_org_view_is_refused(self):
        home = self.root / "legacy"
        home.mkdir()
        with connect(home / "state.db") as legacy:
            migrate(legacy, target=9)
            legacy.execute(
                "CREATE VIEW skill_org_protections AS SELECT 'invalid' AS organization_id"
            )
            with self.assertRaises(sqlite3.DatabaseError):
                _archive_home(home, self.root / "legacy-backup.tar.gz")

    def test_invalid_snapshot_schema_version_is_explicit_failure(self):
        home = self.root / "legacy-invalid-version"
        home.mkdir()
        connection = connect(home / "state.db")
        self.addCleanup(connection.close)
        migrate(connection, target=9)
        target = self.root / "invalid-version-backup.tar.gz"
        target.write_bytes(b"previous-backup")
        for versions in ((-1,), (1.5,), ("private-version-sentinel",), (), (9, 9)):
            with self.subTest(versions=versions):
                connection.execute("DELETE FROM schema_version")
                connection.executemany(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    [(version,) for version in versions],
                )
                connection.commit()
                output, error = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    result = run_backup(home, argparse.Namespace(output=str(target), json=True))
                self.assertEqual(result, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(target.read_bytes(), b"previous-backup")
                self.assertNotIn("private-version-sentinel", error.getvalue())
                self.assertNotIn(str(home), error.getvalue())
