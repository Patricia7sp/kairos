"""Journal de árvores: preservação, atomicidade e leitura fail-closed."""

import dataclasses
import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from skill_mutation_fixtures import creation_text
from test_skill_mutation_state import creation_draft

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationAction,
    SkillMutationError,
    SkillMutationState,
)
from kairos_state import connect
from kairos_state.migrations import canonical_fingerprint, migrate, repair_derived_objects
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.schema import SCHEMA_VERSION


class RemovalStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.path = self.home / "state.db"
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        migrate(self.db, target=7)
        self.home_id = catalog_home_id(self.home)
        self.legacy = SkillMutationRepository(self.db, home_id=self.home_id)
        self.created = creation_draft(self.home)
        self.legacy.prepare(self.created, creation_text())
        self.legacy.finish(self.created.operation_id, SkillMutationState.COMMITTED)
        from kairos_skills.mutation_contract import validate_skill_creation

        text = creation_text(name="pending")
        self.pending = creation_draft(self.home, validate_skill_creation(text))
        self.legacy.prepare(self.pending, text)
        prior_text = creation_text(name="rolled-back")
        prior = creation_draft(self.home, validate_skill_creation(prior_text))
        self.legacy.prepare(prior, prior_text)
        self.legacy.finish(prior.operation_id, SkillMutationState.COMMITTED)
        rollback = dataclasses.replace(
            prior,
            operation_id=uuid.uuid4().hex,
            action=SkillMutationAction.ROLLBACK,
            reverts=prior.operation_id,
        )
        self.legacy.prepare(rollback, prior_text)
        self.legacy.finish(rollback.operation_id, SkillMutationState.COMMITTED)
        self.legacy_before = self.legacy_evidence()

    def legacy_evidence(self):
        return (
            self.legacy.history(),
            self.legacy.content(self.created.operation_id),
            self.legacy.pending("pending"),
            tuple(tuple(row) for row in self.db.execute("SELECT * FROM skill_mutation_events")),
            tuple(
                (record.operation_id, self.legacy.content(record.operation_id))
                for record in self.legacy.history()
            ),
        )

    def modern(self):
        from kairos_state.repositories.skill_removals import SkillRemovalRepository

        migrate(self.db)
        return SkillRemovalRepository(self.db, home_id=self.home_id)

    def capture(self):
        from kairos_filesystem.contract import TreeCapture, TreeEntry

        contents = {"SKILL.md": b"# Legacy, no semver\n", "scripts/run.bin": b"\x00\xff"}
        entries = [TreeEntry("", "directory", 0o700, 0, None, 1, 2, 3)]
        for path in ("empty", "scripts"):
            entries.append(TreeEntry(path, "directory", 0o700, 0, None, 1, len(entries) + 2, 3))
        for path, content in contents.items():
            entries.append(
                TreeEntry(
                    path,
                    "file",
                    0o600,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                    1,
                    len(entries) + 2,
                    3,
                )
            )
        return TreeCapture(tuple(sorted(entries, key=lambda entry: entry.path)), contents)

    def draft(self, **changes):
        from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft

        operation_id = uuid.uuid4().hex
        return dataclasses.replace(
            SkillTreeDraft(
                operation_id,
                self.home_id,
                SkillTreeAction.REMOVE,
                self.created.name,
                Actor.USER_FOREGROUND,
                1.0,
                Provenance.USER,
                self.capture(),
                operation_id,
            ),
            **changes,
        )

    def test_migration_preserves_legacy_ids_content_and_pending(self):
        migrate(self.db)
        self.assertEqual(self.legacy_before, self.legacy_evidence())
        self.assertIsNotNone(
            self.db.execute(
                "SELECT name FROM sqlite_master WHERE name='skill_tree_operations'"
            ).fetchone()
        )
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute(
                "INSERT INTO skill_mutation_operations(operation_id,home_id,action,name,actor,created_at,identity_json,sha256,size_bytes) SELECT ?,home_id,'remove',name,actor,created_at,identity_json,sha256,size_bytes FROM skill_mutation_operations LIMIT 1",
                (uuid.uuid4().hex,),
            )

    def test_future_schema_refused(self):
        with self.db:
            self.db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION + 1,))
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(self.db)
        self.assertEqual(self.legacy_before, self.legacy_evidence())

    def test_timeline_import_and_new_events_have_one_order(self):
        repo = self.modern()
        imported = tuple(
            tuple(row)
            for row in self.db.execute(
                "SELECT legacy_event,tree_event FROM skill_mutation_timeline ORDER BY sequence"
            )
        )
        self.assertEqual(imported, tuple((row[0], None) for row in self.legacy_before[3]))
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        self.legacy.finish(self.pending.operation_id, SkillMutationState.ABORTED)
        repo.finish(draft.operation_id, SkillMutationState.COMMITTED)
        rows = self.db.execute(
            "SELECT legacy_event,tree_event FROM skill_mutation_timeline ORDER BY sequence"
        ).fetchall()
        self.assertIsNotNone(rows[-3][1])
        self.assertIsNotNone(rows[-2][0])
        self.assertIsNotNone(rows[-1][1])
        self.assertEqual(len(rows), len(imported) + 3)

    def test_prepare_snapshot_is_atomic(self):
        repo = self.modern()
        before = canonical_fingerprint(self.db)
        self.db.execute(
            "CREATE TRIGGER fail_tree_event BEFORE INSERT ON skill_tree_events BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with self.assertRaises(SkillMutationError):
            repo.prepare_remove(self.draft(), self.capture())
        self.assertEqual(before, canonical_fingerprint(self.db))

    def test_binary_snapshot_roundtrip_and_corruption_refused(self):
        repo = self.modern()
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        actual = repo.snapshot(draft.operation_id)
        self.assertEqual(actual, self.capture())
        self.assertEqual(actual.contents["scripts/run.bin"], b"\x00\xff")
        self.db.execute("DROP TRIGGER skill_tree_blobs_no_update")
        with self.db:
            self.db.execute(
                "UPDATE skill_tree_blobs SET content=? WHERE sha256=?",
                (b"\x01\xff", hashlib.sha256(b"\x00\xff").hexdigest()),
            )
        with self.assertRaises(SkillMutationError) as caught:
            repo.snapshot(draft.operation_id)
        self.assertEqual(caught.exception.kind, "corrupt")

    def test_duplicate_or_missing_timeline_reference_refused(self):
        repo = self.modern()
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        old_event = self.db.execute(
            "SELECT sequence FROM skill_mutation_events LIMIT 1"
        ).fetchone()[0]
        tree_event = self.db.execute("SELECT sequence FROM skill_tree_events LIMIT 1").fetchone()[0]
        for values in (
            (None, None),
            (old_event, tree_event),
            (old_event, None),
            (None, tree_event),
            (None, tree_event + 100),
        ):
            with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError), self.db:
                self.db.execute(
                    "INSERT INTO skill_mutation_timeline(legacy_event,tree_event) VALUES (?,?)",
                    values,
                )
        self.db.execute("DROP TRIGGER skill_mutation_timeline_no_delete")
        with self.db:
            self.db.execute("DELETE FROM skill_mutation_timeline WHERE tree_event=?", (tree_event,))
        with self.assertRaises(SkillMutationError):
            repo.get(draft.operation_id)

    def test_snapshot_limits_checked_before_reconstruction(self):
        repo = self.modern()
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        self.db.execute("DROP TRIGGER skill_tree_snapshots_no_update")
        manifest = json.loads(
            self.db.execute("SELECT manifest_json FROM skill_tree_snapshots").fetchone()[0]
        )
        corruptions = []
        for field, value in (
            ("path", "/external"),
            ("path", "../outside"),
            ("size", 16 * 1024 * 1024 + 1),
            ("sha256", "f" * 64),
        ):
            altered = [dict(entry) for entry in manifest]
            altered[-1][field] = value
            corruptions.append(altered)
        corruptions.extend(([*manifest, manifest[-1]], manifest * 4097))
        for altered in corruptions:
            with self.subTest(altered=altered[-1]), self.db:
                self.db.execute(
                    "UPDATE skill_tree_snapshots SET manifest_json=?",
                    (json.dumps(altered, sort_keys=True, separators=(",", ":")),),
                )
            with self.assertRaises(SkillMutationError):
                repo.snapshot(draft.operation_id)

    def test_legacy_connection_cannot_append_operation_or_event(self):
        repo = self.modern()
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        legacy = sqlite3.connect(self.path)
        self.addCleanup(legacy.close)
        for sql, operation_id in (
            (
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?, 'committed')",
                self.created.operation_id,
            ),
            (
                "INSERT INTO skill_tree_events(operation_id,state) VALUES (?, 'committed')",
                draft.operation_id,
            ),
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError), legacy:
                legacy.execute(sql, (operation_id,))
        for sql in (
            "INSERT INTO skill_mutation_operations SELECT * FROM skill_mutation_operations LIMIT 1",
            "INSERT INTO skill_tree_operations SELECT * FROM skill_tree_operations LIMIT 1",
            "INSERT INTO skill_mutation_contents SELECT * FROM skill_mutation_contents LIMIT 1",
            "INSERT INTO skill_tree_snapshots SELECT * FROM skill_tree_snapshots LIMIT 1",
            "INSERT INTO skill_tree_blobs SELECT * FROM skill_tree_blobs LIMIT 1",
            "INSERT INTO skill_mutation_timeline SELECT * FROM skill_mutation_timeline LIMIT 1",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.OperationalError), legacy:
                legacy.execute(sql)

    def test_backup_and_repair_preserve_snapshot(self):
        repo = self.modern()
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        before = canonical_fingerprint(self.db)
        self.db.execute("DROP TRIGGER skill_tree_events_no_delete")
        repair_derived_objects(self.db)
        self.assertEqual(before, canonical_fingerprint(self.db))
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("DELETE FROM skill_tree_events")
        destination = connect(self.home / "restored.db")
        self.addCleanup(destination.close)
        self.db.backup(destination)
        from kairos_state.repositories.skill_removals import SkillRemovalRepository

        restored_repo = SkillRemovalRepository(destination, home_id=self.home_id)
        self.assertEqual(
            repo.snapshot(draft.operation_id).contents,
            restored_repo.snapshot(draft.operation_id).contents,
        )
        self.assertEqual(
            restored_repo.snapshot(draft.operation_id).contents["scripts/run.bin"], b"\x00\xff"
        )
        restored_legacy = SkillMutationRepository(destination, home_id=self.home_id)
        self.assertEqual(self.legacy_before[0], restored_legacy.history())
        self.assertEqual(self.legacy_before[1], restored_legacy.content(self.created.operation_id))
        self.assertEqual(self.legacy_before, self.legacy_evidence())

    def test_missing_blob_and_incorrect_size_are_refused(self):
        repo = self.modern()
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        digest = hashlib.sha256(b"\x00\xff").hexdigest()
        self.db.execute("DROP TRIGGER skill_tree_blobs_no_update")
        self.db.execute("PRAGMA ignore_check_constraints=ON")
        with self.db:
            self.db.execute(
                "UPDATE skill_tree_blobs SET size_bytes=size_bytes+1 WHERE sha256=?", (digest,)
            )
        with self.assertRaises(SkillMutationError) as caught:
            repo.snapshot(draft.operation_id)
        self.assertEqual(caught.exception.kind, "corrupt")
        self.db.execute("DROP TRIGGER skill_tree_blobs_no_delete")
        with self.db:
            self.db.execute("DELETE FROM skill_tree_blobs WHERE sha256=?", (digest,))
        with self.assertRaises(SkillMutationError) as caught:
            repo.snapshot(draft.operation_id)
        self.assertEqual(caught.exception.kind, "corrupt")

    def test_snapshot_metadata_limits_are_inclusive_and_types_strict(self):
        from kairos_filesystem.contract import TreeCapture, TreeEntry
        from kairos_skills.removal_contract import validate_capture

        root = self.capture().entries[0]
        skill = TreeEntry("SKILL.md", "file", 0o600, 0, hashlib.sha256(b"").hexdigest(), 1, 20, 3)
        large = tuple(
            TreeEntry(f"file-{index}", "file", 0o600, 16 * 1024 * 1024, "f" * 64, 1, 21 + index, 3)
            for index in range(4)
        )
        capture = TreeCapture((root, skill, *large), None)
        validate_capture(capture)
        for altered in (
            dataclasses.replace(
                capture,
                entries=(*capture.entries, dataclasses.replace(skill, path="overflow", size=1)),
            ),
            dataclasses.replace(capture, entries=(root, dataclasses.replace(skill, size=65537))),
            dataclasses.replace(capture, entries=(root, dataclasses.replace(skill, mode=True))),
            dataclasses.replace(capture, entries=(root, dataclasses.replace(skill, size=True))),
            dataclasses.replace(capture, entries=(root, dataclasses.replace(skill, inode=True))),
            dataclasses.replace(
                capture, entries=(root, dataclasses.replace(skill, path="nested/SKILL.md"))
            ),
            dataclasses.replace(
                capture, entries=(root, dataclasses.replace(skill, path="bad\x00file"))
            ),
            dataclasses.replace(capture, entries=(root, dataclasses.replace(skill, mount_id=99))),
        ):
            with self.subTest(entries=altered.entries), self.assertRaises(SkillMutationError):
                validate_capture(altered)

    def test_read_connection_does_not_register_writer_or_migrate(self):
        from kairos_state.connection import read_connection

        with read_connection(self.path) as reader:
            self.assertIsNone(
                reader.execute(
                    "SELECT name FROM sqlite_master WHERE name='skill_tree_operations'"
                ).fetchone()
            )
            with self.assertRaises(sqlite3.OperationalError):
                reader.execute("SELECT kairos_skill_writer_version()")

    def test_restore_rejects_uncommitted_parent_wrong_origin_and_bytes(self):
        from kairos_skills.removal_contract import SkillTreeAction

        repo = self.modern()
        removal = self.draft(provenance=Provenance.BUNDLED)
        repo.prepare_remove(removal, self.capture())
        restore = self.draft(
            action=SkillTreeAction.RESTORE,
            snapshot_id=removal.operation_id,
            reverts=removal.operation_id,
            provenance=Provenance.BUNDLED,
        )
        with self.assertRaises(SkillMutationError):
            repo.prepare_restore(restore)
        repo.finish(removal.operation_id, SkillMutationState.COMMITTED)
        before = canonical_fingerprint(self.db)
        with self.assertRaises(SkillMutationError):
            repo.prepare_restore(dataclasses.replace(restore, provenance=Provenance.USER))
        altered = dataclasses.replace(
            restore.proof,
            entries=tuple(
                dataclasses.replace(entry, mode=0o644) if entry.kind == "file" else entry
                for entry in restore.proof.entries
            ),
        )
        with self.assertRaises(SkillMutationError):
            repo.prepare_restore(dataclasses.replace(restore, proof=altered))
        self.assertEqual(before, canonical_fingerprint(self.db))
        record = repo.prepare_restore(restore)
        self.assertIs(record.provenance, Provenance.BUNDLED)

    def test_restore_uses_original_snapshot_and_new_proof(self):
        from kairos_skills.removal_contract import SkillTreeAction

        repo = self.modern()
        draft = self.draft()
        repo.prepare_remove(draft, self.capture())
        repo.finish(draft.operation_id, SkillMutationState.COMMITTED)
        proof = dataclasses.replace(
            self.capture(),
            entries=tuple(
                dataclasses.replace(entry, inode=entry.inode + 100)
                for entry in self.capture().entries
            ),
        )
        restore = self.draft(
            operation_id=uuid.uuid4().hex,
            action=SkillTreeAction.RESTORE,
            proof=proof,
            snapshot_id=draft.operation_id,
            reverts=draft.operation_id,
        )
        record = repo.prepare_restore(restore)
        self.assertEqual(record.draft.proof.entries, proof.entries)
        self.assertEqual(repo.snapshot(restore.operation_id), self.capture())
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM skill_tree_snapshots").fetchone()[0], 1
        )
        finished = repo.finish(restore.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(repo.finish(restore.operation_id, SkillMutationState.COMMITTED), finished)
        with self.assertRaises(SkillMutationError):
            repo.finish(restore.operation_id, SkillMutationState.ABORTED)
