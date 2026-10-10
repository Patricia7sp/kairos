"""Projeção entre famílias conserva IDs, origens e snapshots reais."""

import argparse
import dataclasses
import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path

from skill_mutation_fixtures import creation_text
from test_skill_mutation_state import creation_draft

from kairos_cli.skill_mutations import _history, mutation_metadata
from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture, TreeEntry
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationAction,
    SkillMutationError,
    SkillMutationState,
    validate_skill_creation,
)
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_removals import SkillRemovalRepository


class RemovalProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db)
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.trees = SkillRemovalRepository(self.db, home_id=self.repo.home_id)
        self.name = "revisar-docs"

    def create(self):
        draft = creation_draft(self.home)
        self.repo.prepare(draft, creation_text())
        return self.repo.finish(draft.operation_id, SkillMutationState.COMMITTED)

    def capture(self, inode=2, data=b"# Legacy\n\x00\xff"):
        return TreeCapture(
            (
                TreeEntry("", "directory", 0o700, 0, None, 1, inode, 3),
                TreeEntry(
                    "SKILL.md",
                    "file",
                    0o600,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    1,
                    inode + 1,
                    3,
                ),
            ),
            {"SKILL.md": data},
        )

    def remove(self, *, origin=Provenance.USER, capture=None, finish=True):
        capture = capture or self.capture()
        operation_id = uuid.uuid4().hex
        draft = SkillTreeDraft(
            operation_id,
            self.repo.home_id,
            SkillTreeAction.REMOVE,
            self.name,
            Actor.USER_FOREGROUND,
            1.0,
            origin,
            TreeCapture(capture.entries, None),
            operation_id,
        )
        self.repo.prepare_remove(draft, capture)
        return (
            self.repo.finish(operation_id, SkillMutationState.COMMITTED)
            if finish
            else self.repo.get(operation_id)
        )

    def restore_draft(self, removed, inode=20):
        operation_id = uuid.uuid4().hex
        capture = self.repo.snapshot(removed.operation_id)
        proof = TreeCapture(
            tuple(
                dataclasses.replace(entry, inode=inode + index)
                for index, entry in enumerate(capture.entries)
            ),
            None,
        )
        return SkillTreeDraft(
            operation_id,
            self.repo.home_id,
            SkillTreeAction.RESTORE,
            self.name,
            Actor.USER_FOREGROUND,
            0.0,
            removed.provenance,
            proof,
            removed.operation_id,
            removed.operation_id,
        )

    def restore(self, removed):
        draft = self.restore_draft(removed)
        self.repo.prepare_restore(draft)
        return self.repo.finish(draft.operation_id, SkillMutationState.COMMITTED)

    def test_remove_deactivates_create_and_restore_activates_new_identity(self):
        created = self.create()
        removed = self.remove()
        self.assertIsNone(self.repo.current_installation(self.name))
        self.assertEqual(self.repo.current_removal(self.name).operation_id, removed.operation_id)
        restored = self.restore(removed)
        self.assertEqual(
            self.repo.current_installation(self.name).operation_id, restored.operation_id
        )
        self.assertEqual(self.repo.current_installation(self.name).inode, 20)
        self.assertIsNone(self.repo.current_removal(self.name))
        self.assertEqual(self.repo.get(created.operation_id), created)
        self.assertEqual(
            self.repo.snapshot(restored.operation_id).contents, {"SKILL.md": b"# Legacy\n\x00\xff"}
        )

    def test_current_removal_changes_without_losing_older_snapshots(self):
        self.create()
        first = self.remove()
        self.restore(first)
        second = self.remove(capture=self.capture(inode=20, data=b"# Edited\n"))
        self.assertEqual(self.repo.current_removal(self.name), second)
        self.assertEqual(
            self.repo.snapshot(first.operation_id).contents["SKILL.md"], b"# Legacy\n\x00\xff"
        )
        self.assertEqual(
            self.repo.snapshot(second.operation_id).contents["SKILL.md"], b"# Edited\n"
        )
        with self.assertRaises(SkillMutationError):
            self.repo.prepare_restore(self.restore_draft(first))

    def test_history_orders_families_by_global_sequence(self):
        created = self.create()
        removed = self.remove()
        restored = self.restore(removed)
        self.assertEqual(self.repo.history(), (restored, removed, created))
        self.assertEqual(self.repo.history(limit=1), (restored,))
        self.assertEqual(self.repo.history(name="absent"), ())
        self.assertEqual(self.repo.latest_restore(removed.operation_id), restored)

    def test_pending_other_family_blocks_all_writers(self):
        self.create()
        pending = self.remove(finish=False)
        self.assertEqual(self.repo.pending(self.name), (pending,))
        for state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT):
            if state is SkillMutationState.CONFLICT:
                self.repo.finish(pending.operation_id, state)
            with self.subTest(state=state), self.assertRaises(SkillMutationError):
                self.repo.prepare(creation_draft(self.home), creation_text())
            with self.subTest(state=state), self.assertRaises(SkillMutationError):
                self.remove()
        self.repo.finish(pending.operation_id, SkillMutationState.ABORTED)
        self.assertIsNotNone(self.repo.current_installation(self.name))

    def test_legacy_pending_blocks_tree_prepare(self):
        draft = creation_draft(self.home)
        self.repo.prepare(draft, creation_text())
        with self.assertRaises(SkillMutationError):
            self.remove(origin=Provenance.BUNDLED)
        self.assertEqual(self.repo.history(), (self.repo.get(draft.operation_id),))

    def test_restore_preserves_recorded_origin(self):
        removed = self.remove(origin=Provenance.BUNDLED)
        self.assertIn(self.name, self.repo.bundled_tombstones())
        restored = self.restore(removed)
        self.assertIs(restored.provenance, Provenance.BUNDLED)
        self.assertIs(self.repo.current_installation(self.name).provenance, Provenance.BUNDLED)
        self.assertNotIn(self.name, self.repo.bundled_tombstones())

    def test_old_rollback_cannot_remove_restored_or_reinstalled_tree(self):
        created = self.create()
        removed = self.remove()
        restored = self.restore(removed)
        rollback = dataclasses.replace(
            created.draft,
            operation_id=uuid.uuid4().hex,
            action=SkillMutationAction.ROLLBACK,
            reverts=created.operation_id,
        )
        with self.assertRaises(SkillMutationError):
            self.repo.prepare(rollback, creation_text())
        self.assertEqual(
            self.repo.current_installation(self.name).operation_id, restored.operation_id
        )

    def test_add_after_remove_uses_new_id(self):
        created = self.create()
        removed = self.remove()
        newer = self.create()
        self.assertNotEqual(created.operation_id, newer.operation_id)
        self.assertEqual(self.repo.current_installation(self.name).operation_id, newer.operation_id)
        self.assertEqual(self.repo.current_removal(self.name).operation_id, removed.operation_id)
        with self.assertRaises(SkillMutationError):
            self.repo.prepare_restore(self.restore_draft(removed))
        self.assertEqual(
            self.repo.snapshot(removed.operation_id).contents["SKILL.md"], b"# Legacy\n\x00\xff"
        )

    def test_legacy_metadata_unchanged(self):
        created = self.create()
        self.assertEqual(
            mutation_metadata(created),
            {
                "id": created.operation_id,
                "name": self.name,
                "action": "create",
                "state": "committed",
                "origin": "user",
                "sha256": created.sha256,
                "created_at": created.draft.created_at,
                "reverts": None,
            },
        )
        removed = self.remove()
        metadata = mutation_metadata(removed)
        self.assertEqual(metadata["action"], "remove")
        self.assertEqual(metadata["snapshot_id"], removed.operation_id)
        self.assertNotIn("contents", metadata)

    def test_bundled_tombstone_survives_new_manual_creation(self):
        removed = self.remove(origin=Provenance.BUNDLED)
        created = self.create()
        self.assertEqual(
            self.repo.current_installation(self.name).operation_id, created.operation_id
        )
        self.assertEqual(self.repo.current_removal(self.name).operation_id, removed.operation_id)
        self.assertIn(self.name, self.repo.bundled_tombstones())

    def test_history_rejects_tree_only_partial_ledger(self):
        with self.db:
            self.db.execute("DROP TABLE skill_mutation_events")
            self.db.execute("DROP TABLE skill_mutation_contents")
            self.db.execute("DROP TABLE skill_mutation_operations")
            self.db.execute("DROP TABLE schema_version")
        with self.assertRaises(SkillMutationError):
            _history(self.home, argparse.Namespace(name=None, limit=20))

    def test_history_rejects_partial_modern_ledger_without_writing(self):
        self.db.execute("DROP TABLE skill_tree_events")
        self.db.commit()
        with self.assertRaises(SkillMutationError):
            _history(self.home, argparse.Namespace(name=None, limit=20))

    def test_missing_timeline_reference_blocks_projection_and_history(self):
        created = self.create()
        self.db.execute("DROP TRIGGER skill_mutation_timeline_no_delete")
        with self.db:
            self.db.execute(
                "DELETE FROM skill_mutation_timeline WHERE legacy_event=(SELECT MAX(sequence) FROM skill_mutation_events)"
            )
        for read in (
            lambda: self.repo.current_installation(self.name),
            self.repo.history,
            lambda: self.repo.get(created.operation_id),
        ):
            with self.subTest(read=read), self.assertRaises(SkillMutationError):
                read()

    def test_orphan_operation_blocks_history_and_projection(self):
        created = self.create()
        self.db.execute("DROP TRIGGER skill_mutation_timeline_no_delete")
        self.db.execute("DROP TRIGGER skill_mutation_events_no_delete")
        with self.db:
            self.db.execute("DELETE FROM skill_mutation_timeline")
            self.db.execute("DELETE FROM skill_mutation_events")
        for read in (self.repo.history, lambda: self.repo.current_installation(self.name)):
            with self.subTest(read=read), self.assertRaises(SkillMutationError):
                read()
        self.assertIsNotNone(created)

    def test_aborted_remove_preserves_installation(self):
        created = self.create()
        removed = self.remove(finish=False)
        self.repo.finish(removed.operation_id, SkillMutationState.ABORTED)
        self.assertEqual(
            self.repo.current_installation(self.name).operation_id, created.operation_id
        )
        self.assertIsNone(self.repo.current_removal(self.name))

    def test_repository_registers_writer_capability(self):
        self.db.create_function("kairos_skill_writer_version", 0, None)
        repo = SkillMutationRepository(self.db, home_id=self.repo.home_id)
        draft = creation_draft(self.home, validate_skill_creation(creation_text(name="other")))
        repo.prepare(draft, creation_text(name="other"))
        self.assertEqual(repo.pending("other")[0].operation_id, draft.operation_id)


class RemovalWriterGuardTests(unittest.TestCase):
    def setUp(self):
        from test_skill_mutation_service import MutationServiceTests

        MutationServiceTests.setUp(self)
        self.created = self.service.add(self.source, actor=Actor.USER_FOREGROUND)

    def remove_draft(self):
        target = self.home / "skills" / self.created.name
        directory = target.stat()
        skill = (target / "SKILL.md").stat()
        data = (target / "SKILL.md").read_bytes()
        capture = TreeCapture(
            (
                TreeEntry("", "directory", 0o700, 0, None, directory.st_dev, directory.st_ino, 3),
                TreeEntry(
                    "SKILL.md",
                    "file",
                    0o600,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    skill.st_dev,
                    skill.st_ino,
                    3,
                ),
            ),
            {"SKILL.md": data},
        )
        operation_id = uuid.uuid4().hex
        draft = SkillTreeDraft(
            operation_id,
            self.repo.home_id,
            SkillTreeAction.REMOVE,
            self.created.name,
            Actor.USER_FOREGROUND,
            1.0,
            Provenance.USER,
            TreeCapture(capture.entries, None),
            operation_id,
        )
        self.repo.prepare_remove(draft, capture)
        return draft

    def test_add_does_not_reinterpret_pending_remove_as_legacy_rollback(self):
        draft = self.remove_draft()
        before = (self.home / "skills" / self.created.name / "SKILL.md").read_bytes()
        with self.assertRaises(SkillMutationError) as raised:
            self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertEqual(raised.exception.operation_id, draft.operation_id)
        self.assertIs(self.repo.get(draft.operation_id).state, SkillMutationState.PREPARED)
        self.assertEqual(
            (self.home / "skills" / self.created.name / "SKILL.md").read_bytes(), before
        )

    def test_legacy_rollback_does_not_reinterpret_pending_remove(self):
        draft = self.remove_draft()
        with self.assertRaises(SkillMutationError) as raised:
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertEqual(raised.exception.operation_id, draft.operation_id)
        self.assertIs(self.repo.get(draft.operation_id).state, SkillMutationState.PREPARED)

    def test_add_after_real_retirement_keeps_previous_removal(self):
        draft = self.remove_draft()
        target = self.home / "skills" / self.created.name
        target.rename(self.root / "retired")
        removed = self.repo.finish(draft.operation_id, SkillMutationState.COMMITTED)
        newer = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertNotEqual(self.created.operation_id, newer.operation_id)
        self.assertEqual(
            self.repo.current_installation(newer.name).operation_id, newer.operation_id
        )
        self.assertEqual(self.repo.current_removal(newer.name), removed)
        with self.assertRaises(SkillMutationError):
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual((target / "SKILL.md").read_bytes(), self.source.read_bytes())

    def test_old_rollback_with_same_inode_cannot_retire_restore(self):
        draft = self.remove_draft()
        removed = self.repo.finish(draft.operation_id, SkillMutationState.COMMITTED)
        restore_id = uuid.uuid4().hex
        restore = dataclasses.replace(
            draft,
            operation_id=restore_id,
            action=SkillTreeAction.RESTORE,
            reverts=removed.operation_id,
        )
        self.repo.prepare_restore(restore)
        self.repo.finish(restore_id, SkillMutationState.COMMITTED)
        before = self.repo.history()
        with self.assertRaises(SkillMutationError):
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(self.repo.history(), before)
        self.assertEqual(
            (self.home / "skills" / self.created.name / "SKILL.md").read_bytes(),
            self.source.read_bytes(),
        )

    def test_repeating_historical_rollback_preserves_later_installation(self):
        previous = self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        newer = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        target = self.home / "skills" / newer.name
        identity = target.stat()
        before = self.repo.history()
        repeated = self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(repeated, previous)
        self.assertEqual(self.repo.history(), before)
        self.assertEqual(
            (target.stat().st_dev, target.stat().st_ino), (identity.st_dev, identity.st_ino)
        )
        self.assertEqual((target / "SKILL.md").read_bytes(), self.source.read_bytes())
