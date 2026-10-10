"""Recuperação de árvores após SIGKILL em fronteiras reais."""

import multiprocessing
import shutil
import unittest
from unittest.mock import patch

import test_skill_removal_service as fixtures

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import reconcile_pending
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_io import SkillRemovalFiles
from kairos_state import connect
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def stopped_tree_operation(home, name, remove_id, phase, pipe):
    db = connect(home / "state.db")
    repo = SkillMutationRepository(db, home_id=catalog_home_id(home))
    service = SkillMutationService(home, repo)
    original_prepare = repo.prepare_restore if remove_id else repo.prepare_remove
    original_move = SkillRemovalFiles.publish if remove_id else SkillRemovalFiles.retire
    original_finish = repo.finish
    original_stage = SkillRemovalFiles.stage

    def stop(operation_id):
        pipe.send(operation_id)
        pipe.recv()

    def prepare(draft, *args):
        if phase == "before-prepare":
            stop(draft.operation_id)
        result = original_prepare(draft, *args)
        if phase == "prepared":
            stop(draft.operation_id)
        return result

    def move(files, *args):
        original_move(files, *args)
        if phase == "rename":
            stop(args[0] if remove_id else args[1])

    def finish(operation_id, state):
        if phase == "before-commit":
            stop(operation_id)
        result = original_finish(operation_id, state)
        if phase == "after-commit":
            stop(operation_id)
        return result

    def stage(files, operation_id, snapshot):
        result = original_stage(files, operation_id, snapshot)
        if phase == "stage":
            stop(operation_id)
        return result

    if remove_id:
        repo.prepare_restore = prepare
    else:
        repo.prepare_remove = prepare
    repo.finish = finish
    with (
        patch.object(SkillRemovalFiles, "publish" if remove_id else "retire", move),
        patch.object(SkillRemovalFiles, "stage", stage),
    ):
        if remove_id:
            service.rollback(remove_id, actor=Actor.USER_FOREGROUND)
        else:
            service.remove(name, actor=Actor.USER_FOREGROUND, confirmed=True)
    db.close()


class RemovalRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.RemovalServiceTests.setUp(self)
        self.context = multiprocessing.get_context("spawn")

    def interrupted(self, phase, *, remove_id=None):
        parent, child = self.context.Pipe()
        process = self.context.Process(
            target=stopped_tree_operation, args=(self.home, self.name, remove_id, phase, child)
        )
        process.start()
        try:
            self.assertTrue(parent.poll(15), "Subprocesso não atingiu a fronteira real.")
            operation_id = parent.recv()
            process.kill()
            process.join(5)
            self.assertEqual(process.exitcode, -9)
            return operation_id
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def reconcile(self):
        db = connect(self.home / "state.db")
        try:
            repo = SkillMutationRepository(db, home_id=catalog_home_id(self.home))
            with skill_mutation_lock(self.home), SkillRemovalFiles(self.home) as files:
                return reconcile_pending(repo, files, name=self.name)
        finally:
            db.close()

    def test_remove_death_all_boundaries(self):
        for phase in ("prepared", "rename", "before-commit", "after-commit"):
            with self.subTest(phase=phase):
                operation_id = self.interrupted(phase)
                self.reconcile()
                expected = (
                    SkillMutationState.ABORTED
                    if phase == "prepared"
                    else SkillMutationState.COMMITTED
                )
                self.assertIs(self.repo.get(operation_id).state, expected)
                self.assertEqual(self.target.exists(), phase == "prepared")
                if phase != "prepared":
                    self.service.rollback(operation_id, actor=Actor.USER_FOREGROUND)

    def test_remove_death_before_prepare_leaves_original_and_no_journal_record(self):
        inode = self.target.stat().st_ino
        operation_id = self.interrupted("before-prepare")
        self.assertIsNone(self.repo.get(operation_id))
        self.assertEqual(self.reconcile(), ())
        self.assertEqual(self.target.stat().st_ino, inode)
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertNotEqual(removed.operation_id, operation_id)
        self.assertIs(removed.state, SkillMutationState.COMMITTED)

    def test_restore_death_all_boundaries(self):
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        for phase in ("prepared", "rename", "before-commit", "after-commit"):
            with self.subTest(phase=phase):
                operation_id = self.interrupted(phase, remove_id=removed.operation_id)
                self.reconcile()
                expected = (
                    SkillMutationState.ABORTED
                    if phase == "prepared"
                    else SkillMutationState.COMMITTED
                )
                self.assertIs(self.repo.get(operation_id).state, expected)
                self.assertEqual(self.target.exists(), phase != "prepared")
                self.assertEqual(
                    self.repo.snapshot(removed.operation_id).contents["scripts/run.bin"],
                    b"\x00\xff",
                )
                if phase != "prepared":
                    removed = self.service.remove(
                        self.name, actor=Actor.USER_FOREGROUND, confirmed=True
                    )

    def test_remove_both_present_preserves_conflict(self):
        operation_id = self.interrupted("rename")
        retired = self.home / ".skill-mutations/retired" / operation_id
        shutil.copytree(retired, self.target)
        with self.assertRaises(SkillMutationError) as raised:
            self.reconcile()
        self.assertEqual(raised.exception.operation_id, operation_id)
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")
        self.assertEqual((retired / "scripts/run.bin").read_bytes(), b"\x00\xff")

    def test_restore_orphan_without_prepare_untouched(self):
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        orphan_id = self.interrupted("stage", remove_id=removed.operation_id)
        self.assertIsNone(self.repo.get(orphan_id))
        self.assertEqual(self.reconcile(), ())
        orphan = self.home / ".skill-mutations/staging" / orphan_id
        self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual((orphan / "scripts/run.bin").read_bytes(), b"\x00\xff")

    def test_remove_edited_retired_tree_keeps_conflict(self):
        operation_id = self.interrupted("rename")
        retired = self.home / ".skill-mutations/retired" / operation_id
        (retired / "scripts/run.bin").write_bytes(b"edited")
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((retired / "scripts/run.bin").read_bytes(), b"edited")
        self.assertFalse(self.target.exists())

    def test_fsync_failure_after_rename_remains_pending(self):
        operation_id = self.interrupted("rename")
        with (
            patch("kairos_skills.removal_io.os.fsync", side_effect=OSError("fixture")),
            self.assertRaises(SkillMutationError),
        ):
            self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.PREPARED)
        self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.COMMITTED)

    def test_remove_both_absent_conflict(self):
        operation_id = self.interrupted("rename")
        retired = self.home / ".skill-mutations/retired" / operation_id
        shutil.rmtree(retired)
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual(self.repo.snapshot(operation_id).contents["scripts/run.bin"], b"\x00\xff")

    def test_restore_both_absent_conflict(self):
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        operation_id = self.interrupted("prepared", remove_id=removed.operation_id)
        shutil.rmtree(self.home / ".skill-mutations/staging" / operation_id)
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertFalse(self.target.exists())

    def test_restore_both_present_preserved(self):
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        operation_id = self.interrupted("prepared", remove_id=removed.operation_id)
        staged = self.home / ".skill-mutations/staging" / operation_id
        shutil.copytree(staged, self.target)
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((staged / "scripts/run.bin").read_bytes(), b"\x00\xff")
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")

    def test_remove_identical_replacement_conflict(self):
        operation_id = self.interrupted("prepared")
        saved = self.home / "original"
        self.target.rename(saved)
        shutil.copytree(saved, self.target)
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")

    def test_restore_replaced_published_copy_conflict(self):
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        operation_id = self.interrupted("rename", remove_id=removed.operation_id)
        saved = self.home / "original"
        self.target.rename(saved)
        shutil.copytree(saved, self.target)
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")

    def test_corrupted_snapshot_refused_before_touching_files(self):
        operation_id = self.interrupted("rename")
        self.db.execute("DROP TRIGGER skill_tree_blobs_no_update")
        with self.db:
            self.db.execute("UPDATE skill_tree_blobs SET content=? WHERE size_bytes=2", (b"xx",))
        retired = self.home / ".skill-mutations/retired" / operation_id
        with self.assertRaises(SkillMutationError) as raised:
            self.reconcile()
        self.assertEqual(raised.exception.kind, "corrupt")
        self.assertEqual((retired / "scripts/run.bin").read_bytes(), b"\x00\xff")
        self.assertFalse(self.target.exists())
