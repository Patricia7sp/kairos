"""Tombstones e pendências reais precedem qualquer cópia de sync."""

import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_skill_removal_projection as projection_fixtures
from skill_mutation_fixtures import creation_text
from test_skill_mutation_io import hold_lock
from test_skill_mutation_state import creation_draft

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.sync import MANIFEST_NAME, read_manifest, sync_bundled_skills
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def guarded_sync(bundle, home, pipe):
    from contextlib import contextmanager

    from kairos_skills import sync

    original_snapshot = sync._snapshot_bundle
    original_read = sync.read_skill_tombstones

    @contextmanager
    def snapshot(source):
        with original_snapshot(source) as bundled:
            pipe.send("snapshot_ready")
            yield bundled

    def read(home):
        result = original_read(home)
        pipe.send("ledger_read")
        return result

    with (
        patch.object(sync, "_snapshot_bundle", snapshot),
        patch.object(sync, "read_skill_tombstones", read),
    ):
        result = sync.sync_bundled_skills(bundle, home / "skills")
    pipe.send(result.copied)


class RemovalSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bundle = self.root / "bundle"
        self.name = "revisar-docs"
        self.source = self.bundle / self.name
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_bytes(b"# Legacy\n\x00\xff")
        self.db = None

    def repository(self):
        if self.db is None:
            self.db = connect(self.home / "state.db")
            self.addCleanup(self.db.close)
            migrate(self.db)
            self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        return self.repo

    def remove(self):
        from kairos_skills.mutations import SkillMutationService

        self.repository()
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=self.bundle):
            return SkillMutationService(self.home, self.repo).remove(
                self.name, actor=Actor.USER_FOREGROUND, confirmed=True
            )

    def test_tombstone_survives_bundle_disappearance_and_reappearance(self):
        sync_bundled_skills(self.bundle, self.home / "skills")
        removed = self.remove()
        before = self.repo.snapshot(removed.operation_id).contents
        saved = self.root / "saved-bundle"
        self.source.rename(saved)
        sync_bundled_skills(self.bundle, self.home / "skills")
        self.assertNotIn(self.name, read_manifest(self.home / "skills" / MANIFEST_NAME))
        saved.rename(self.source)
        result = sync_bundled_skills(self.bundle, self.home / "skills")
        self.assertFalse((self.home / "skills" / self.name).exists())
        self.assertEqual(result.user_deleted, [self.name])
        from kairos_skills.removal_state import read_skill_tombstones

        self.assertIn(self.name, read_skill_tombstones(self.home))
        self.assertEqual(self.repo.snapshot(removed.operation_id).contents, before)

    def test_sync_does_not_seed_during_pending_restore(self):
        sync_bundled_skills(self.bundle, self.home / "skills")
        removed = self.remove()
        self.repo.prepare_restore(
            projection_fixtures.RemovalProjectionTests.restore_draft(self, removed)
        )
        (self.source / "SKILL.md").write_bytes(b"new bundle")
        before = (self.home / "skills" / MANIFEST_NAME).read_bytes()
        with self.assertRaises(SkillMutationError) as raised:
            sync_bundled_skills(self.bundle, self.home / "skills")
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertFalse((self.home / "skills" / self.name).exists())
        self.assertEqual((self.home / "skills" / MANIFEST_NAME).read_bytes(), before)

    def test_common_pending_blocks_sync_even_with_empty_bundle(self):
        repo = self.repository()
        draft = creation_draft(self.home, name="revisar-docs")
        repo.prepare(draft, creation_text())
        self.source.rename(self.root / "saved-bundle")
        for state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT):
            if state is SkillMutationState.CONFLICT:
                repo.finish(draft.operation_id, state)
            with self.subTest(state=state):
                with self.assertRaises(SkillMutationError) as raised:
                    sync_bundled_skills(self.bundle, self.home / "skills")
                self.assertEqual(raised.exception.operation_id, draft.operation_id)
                self.assertFalse((self.home / "skills").exists())

    def test_corrupt_or_incompatible_ledger_refuses_seeding(self):
        self.repository()
        for statement in (
            "UPDATE schema_version SET version=9",
            "UPDATE schema_version SET version=8",
            "DROP TABLE skill_tree_snapshots",
        ):
            with self.db:
                self.db.execute(statement)
            if statement.startswith("UPDATE schema_version SET version=8"):
                continue
            with self.subTest(statement=statement):
                with self.assertRaises(SkillMutationError):
                    sync_bundled_skills(self.bundle, self.home / "skills")
                self.assertFalse((self.home / "skills").exists())

    def test_old_home_read_does_not_create_or_migrate_database(self):
        from kairos_skills.removal_state import read_skill_tombstones

        missing = self.root / "missing-home"
        self.assertEqual(read_skill_tombstones(missing), frozenset())
        self.assertFalse(missing.exists())
        self.assertEqual(read_skill_tombstones(self.home), frozenset())
        self.assertEqual(list(self.home.iterdir()), [])
        path = self.home / "state.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE old_data(value)")
            db.execute("INSERT INTO old_data VALUES ('preserve')")
        before = path.read_bytes()
        self.assertEqual(read_skill_tombstones(self.home), frozenset())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({entry.name for entry in self.home.iterdir()}, {"state.db"})

    def test_database_links_refuse_seeding_and_preserve_outside(self):
        outside = self.root / "outside.db"
        outside.write_bytes(b"private marker")
        database = self.home / "state.db"
        database.symlink_to(outside)
        with self.assertRaises(SkillMutationError):
            sync_bundled_skills(self.bundle, self.home / "skills")
        database.unlink()
        os.link(outside, database)
        with self.assertRaises(SkillMutationError):
            sync_bundled_skills(self.bundle, self.home / "skills")
        self.assertEqual(outside.read_bytes(), b"private marker")
        self.assertFalse((self.home / "skills").exists())

    def test_missing_bundle_cannot_hide_pending_operations(self):
        repo = self.repository()
        draft = creation_draft(self.home)
        repo.prepare(draft, creation_text())
        self.source.rename(self.root / "saved-bundle")
        self.bundle.rmdir()
        with self.assertRaises(SkillMutationError) as raised:
            sync_bundled_skills(self.bundle, self.home / "skills")
        self.assertEqual(raised.exception.operation_id, draft.operation_id)
        self.assertFalse((self.home / "skills").exists())

    def test_restore_committed_allows_sync_without_overwriting_edits(self):
        sync_bundled_skills(self.bundle, self.home / "skills")
        removed = self.remove()
        from kairos_skills.mutations import SkillMutationService

        SkillMutationService(self.home, self.repo).rollback(
            removed.operation_id, actor=Actor.USER_FOREGROUND
        )
        installed = self.home / "skills" / self.name / "SKILL.md"
        installed.write_bytes(b"local edit")
        (self.source / "SKILL.md").write_bytes(b"bundle update")
        result = sync_bundled_skills(self.bundle, self.home / "skills")
        self.assertEqual(result.user_modified, [self.name])
        self.assertEqual(installed.read_bytes(), b"local edit")
        from kairos_skills.removal_state import read_skill_tombstones

        self.assertNotIn(self.name, read_skill_tombstones(self.home))

    def test_partial_modern_or_unreadable_ledger_never_assumes_no_tombstone(self):
        path = self.home / "state.db"
        for version in (8, 9):
            with self.subTest(version=version):
                with sqlite3.connect(path) as db:
                    db.execute("CREATE TABLE schema_version(version INTEGER)")
                    db.execute("INSERT INTO schema_version VALUES (?)", (version,))
                from kairos_skills.removal_state import read_skill_tombstones

                before = path.read_bytes()
                with self.assertRaises(SkillMutationError):
                    read_skill_tombstones(self.home)
                self.assertEqual(path.read_bytes(), before)
                path.unlink()
        path.write_bytes(b"invalid database")
        with self.assertRaises(SkillMutationError):
            read_skill_tombstones(self.home)
        with self.assertRaises(SkillMutationError):
            sync_bundled_skills(self.bundle, self.home / "skills")
        self.assertFalse((self.home / "skills").exists())

    def test_sync_and_remove_share_lock(self):
        context = multiprocessing.get_context("spawn")
        lock_parent, lock_child = context.Pipe()
        sync_parent, sync_child = context.Pipe()
        holder = context.Process(target=hold_lock, args=(self.home, lock_child))
        syncing = context.Process(target=guarded_sync, args=(self.bundle, self.home, sync_child))
        holder.start()
        try:
            self.assertTrue(lock_parent.poll(10))
            self.assertEqual(lock_parent.recv(), "locked")
            syncing.start()
            self.assertTrue(sync_parent.poll(10))
            self.assertEqual(sync_parent.recv(), "snapshot_ready")
            self.assertFalse(sync_parent.poll(0.15))
            self.assertFalse((self.home / "skills").exists())
            lock_parent.send("release")
            self.assertTrue(sync_parent.poll(10))
            self.assertEqual(sync_parent.recv(), "ledger_read")
            self.assertTrue(sync_parent.poll(10))
            self.assertEqual(sync_parent.recv(), [self.name])
            holder.join(5)
            syncing.join(5)
            self.assertEqual(holder.exitcode, 0)
            self.assertEqual(syncing.exitcode, 0)
        finally:
            for process in (holder, syncing):
                if process.is_alive():
                    process.kill()
                if process.pid is not None:
                    process.join(5)
            for pipe in (lock_parent, lock_child, sync_parent, sync_child):
                pipe.close()
