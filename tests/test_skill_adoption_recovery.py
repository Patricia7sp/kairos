"""Recuperação cruzada de adoções e árvores após SIGKILL real."""

import multiprocessing
import os
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from skill_org_fixtures import personal_operation, personal_policy, personal_receipt

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_skills.adoption_contract import AdoptionAuthority
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
)
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import reconcile_pending
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_skills.removal_io import SkillRemovalFiles
from kairos_skills.removal_origin import resolve_removal_origin
from kairos_skills.sync import origin_hash
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_adoptions import SkillAdoptionRepository
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_removals import SkillRemovalRepository


def stop_at_boundary(pipe, operation_id):
    pipe.send(operation_id)
    pipe.recv()


@personal_operation
def stopped_adoption(home, name, phase, pipe, *, policy):
    database = connect(home / "state.db")
    repository = SkillMutationRepository(database, home_id=catalog_home_id(home))
    service = SkillAdoptionService(home, repository, policy=policy)
    original_prepare = SkillAdoptionRepository.prepare_adopt
    original_finish = SkillAdoptionRepository.finish
    original_snapshot = SkillRemovalRepository._store_snapshot

    def store_snapshot(removals, snapshot_id, snapshot):
        result = original_snapshot(removals, snapshot_id, snapshot)
        if phase == "snapshot-write-uncommitted":
            stop_at_boundary(pipe, snapshot_id)
        return result

    def prepare(adoptions, draft, snapshot):
        result = original_prepare(adoptions, draft, snapshot)
        if phase == "prepared":
            stop_at_boundary(pipe, draft.operation_id)
        return result

    def finish(adoptions, operation_id, state):
        if phase == "before-commit" and state is SkillMutationState.COMMITTED:
            stop_at_boundary(pipe, operation_id)
        result = original_finish(adoptions, operation_id, state)
        if phase == "after-commit" and state is SkillMutationState.COMMITTED:
            stop_at_boundary(pipe, operation_id)
        return result

    with (
        patch.object(SkillAdoptionRepository, "prepare_adopt", prepare),
        patch.object(SkillAdoptionRepository, "finish", finish),
        patch.object(SkillRemovalRepository, "_store_snapshot", store_snapshot),
    ):
        service.adopt(name, actor=Actor.USER_FOREGROUND, confirmed=True)
    database.close()


@personal_operation
def stopped_restore(home, name, remove_id, phase, pipe, *, policy):
    database = connect(home / "state.db")
    repository = SkillMutationRepository(database, home_id=catalog_home_id(home))
    service = SkillMutationService(home, repository, policy=policy)
    original_prepare = repository.prepare_restore
    original_publish = SkillRemovalFiles.publish
    original_finish = repository.finish

    def prepare(draft, **kwargs):
        result = original_prepare(draft, **kwargs)
        if phase == "prepared":
            stop_at_boundary(pipe, draft.operation_id)
        return result

    def publish(files, operation_id, skill_name):
        original_publish(files, operation_id, skill_name)
        if phase == "published":
            stop_at_boundary(pipe, operation_id)

    def finish(operation_id, state):
        if phase == "before-commit" and state is SkillMutationState.COMMITTED:
            stop_at_boundary(pipe, operation_id)
        result = original_finish(operation_id, state)
        if phase == "after-commit" and state is SkillMutationState.COMMITTED:
            stop_at_boundary(pipe, operation_id)
        return result

    repository.prepare_restore = prepare
    repository.finish = finish
    with patch.object(SkillRemovalFiles, "publish", publish):
        service.rollback(remove_id, actor=Actor.USER_FOREGROUND)
    database.close()


class SkillAdoptionRecoveryTests(unittest.TestCase):
    name = "revisar-docs"

    def setUp(self):
        self.context = multiprocessing.get_context("spawn")
        self.new_home()

    def new_home(self):
        if hasattr(self, "database"):
            self.database.close()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.target = self.home / "skills" / self.name
        self.target.mkdir(parents=True)
        (self.target / "SKILL.md").write_bytes(b"# Legacy without semver\n")
        scripts = self.target / "scripts"
        scripts.mkdir()
        (scripts / "run.bin").write_bytes(b"\x00\xff")
        (scripts / "run.bin").chmod(0o751)
        self.database = connect(self.home / "state.db")
        migrate(self.database)
        self.addCleanup(self.database.close)
        self.repository = SkillMutationRepository(self.database, home_id=catalog_home_id(self.home))
        self.adoptions = SkillAdoptionService(
            self.home, self.repository, policy=personal_policy(self, self.home)
        )
        self.mutations = SkillMutationService(
            self.home, self.repository, policy=personal_policy(self, self.home)
        )

    def tree_state(self, root=None):
        root = self.target if root is None else root
        return {
            str(path.relative_to(root)): (
                path.lstat().st_mode & 0o777,
                path.read_bytes() if path.is_file() else None,
            )
            for path in (root, *sorted(root.rglob("*")))
        }

    def adopt(self):
        return self.adoptions.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True).record

    def remove(self):
        return self.mutations.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

    def interrupted(self, target, *args):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=target, args=(*args, child))
        process.start()
        try:
            self.assertTrue(parent.poll(20), "Subprocesso não atingiu a fronteira real.")
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
        database = connect(self.home / "state.db")
        try:
            repository = SkillMutationRepository(database, home_id=catalog_home_id(self.home))
            with skill_mutation_lock(self.home), SkillRemovalFiles(self.home) as files:
                return reconcile_pending(
                    repository, files, name=self.name, policy=personal_policy(self, files.home)
                )
        finally:
            database.close()

    def reopen_backup(self):
        backup_path = self.root / "consistent-state-backup.db"
        backup = sqlite3.connect(backup_path)
        try:
            self.database.backup(backup)
        finally:
            backup.close()
        self.database.close()
        for suffix in ("-wal", "-shm", "-journal"):
            (self.home / f"state.db{suffix}").unlink(missing_ok=True)
        os.replace(backup_path, self.home / "state.db")
        self.database = connect(self.home / "state.db")
        self.addCleanup(self.database.close)
        self.repository = SkillMutationRepository(self.database, home_id=catalog_home_id(self.home))
        self.adoptions = SkillAdoptionService(
            self.home, self.repository, policy=personal_policy(self, self.home)
        )
        self.mutations = SkillMutationService(
            self.home, self.repository, policy=personal_policy(self, self.home)
        )

    def test_SIGKILL_adoption_durable_boundaries(self):
        for phase in (
            "snapshot-write-uncommitted",
            "prepared",
            "before-commit",
            "after-commit",
        ):
            with self.subTest(phase=phase):
                self.new_home()
                before = self.tree_state()
                operation_id = self.interrupted(stopped_adoption, self.home, self.name, phase)
                if phase == "snapshot-write-uncommitted":
                    self.assertIsNone(self.repository.get(operation_id))
                    self.assertIsNone(self.repository.installation_evidence(self.name).adoption)
                    with self.assertRaises(SkillMutationError):
                        self.repository.snapshot(operation_id)
                    self.assertEqual(self.tree_state(), before)
                    continue
                pending_or_committed = self.repository.get(operation_id)
                self.assertIsNotNone(pending_or_committed)
                self.assertEqual(
                    self.repository.snapshot(operation_id).contents["SKILL.md"],
                    b"# Legacy without semver\n",
                )
                expected = (
                    SkillMutationState.COMMITTED
                    if phase == "after-commit"
                    else SkillMutationState.PREPARED
                )
                self.assertIs(pending_or_committed.state, expected)

                self.reconcile()

                self.assertIs(
                    self.repository.get(operation_id).state,
                    SkillMutationState.COMMITTED,
                )
                self.assertEqual(self.tree_state(), before)
                evidence = self.repository.installation_evidence(self.name)
                self.assertEqual(evidence.adoption.adoption_id, operation_id)
                self.assertTrue(evidence.adoption.active)

    def test_SIGKILL_restore_preserves_adoption_link(self):
        for phase in ("prepared", "published", "before-commit", "after-commit"):
            with self.subTest(phase=phase):
                self.new_home()
                before = self.tree_state()
                adoption = self.adopt()
                removed = self.remove()
                snapshot = self.repository.snapshot(removed.operation_id)
                operation_id = self.interrupted(
                    stopped_restore,
                    self.home,
                    self.name,
                    removed.operation_id,
                    phase,
                )
                record = self.repository.get(operation_id)
                self.assertEqual(record.draft.adoption.adoption_id, adoption.operation_id)
                self.assertEqual(record.draft.adoption.installation_id, operation_id)
                self.assertTrue(record.draft.adoption.active)
                self.assertEqual(
                    record.draft.installation_source, adoption.draft.installation_source
                )

                self.reconcile()

                record = self.repository.get(operation_id)
                expected = (
                    SkillMutationState.ABORTED
                    if phase == "prepared"
                    else SkillMutationState.COMMITTED
                )
                self.assertIs(record.state, expected)
                self.assertEqual(self.repository.snapshot(removed.operation_id), snapshot)
                if phase == "prepared":
                    self.assertFalse(self.target.exists())
                else:
                    evidence = self.repository.installation_evidence(self.name)
                    self.assertEqual(evidence.current.operation_id, operation_id)
                    self.assertEqual(evidence.adoption.adoption_id, adoption.operation_id)
                    self.assertTrue(evidence.adoption.active)
                    self.assertEqual(self.tree_state(), before)

    def test_fsync_failure_never_reports_success(self):
        before = self.tree_state()
        adoption = self.adopt()
        removed = self.remove()
        original_fsync = os.fsync

        def fail_skill_parent(fd):
            descriptor = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if descriptor == self.home / "skills":
                raise OSError("fixture fsync failure")
            return original_fsync(fd)

        with (
            patch("kairos_skills.removal_io.os.fsync", fail_skill_parent),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.mutations.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)

        operation_id = raised.exception.operation_id
        self.assertEqual(raised.exception.kind, "io")
        prepared = self.repository.get(operation_id)
        self.assertIs(prepared.state, SkillMutationState.PREPARED)
        self.assertEqual(prepared.draft.adoption.adoption_id, adoption.operation_id)
        self.assertTrue(self.target.exists())
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")

        self.reconcile()
        self.assertIs(self.repository.get(operation_id).state, SkillMutationState.COMMITTED)
        self.assertEqual(self.tree_state(), before)

    def test_cross_family_pending_blocks_writers(self):
        self.new_home()
        before = self.tree_state()
        operation_id = self.interrupted(stopped_adoption, self.home, self.name, "prepared")
        (self.target / "SKILL.md").write_bytes(b"concurrent edit preserved\n")

        with self.assertRaises(SkillMutationError) as raised:
            self.mutations.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(raised.exception.operation_id, operation_id)
        self.assertIs(self.repository.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"concurrent edit preserved\n")
        self.assertEqual(len(self.repository.history(name=self.name)), 1)
        self.assertNotEqual(self.tree_state(), before)

    def test_recovery_unavailable_primitive_keeps_restore_prepared(self):
        adoption = self.adopt()
        removed = self.remove()
        operation_id, files = self._prepare_restore_record(removed, adoption)
        files.publish(operation_id, self.name)
        files.sync_parents()
        files.__exit__(None, None, None)
        self.reopen_backup()
        before = self.tree_state()

        with (
            skill_mutation_lock(self.home),
            SkillRemovalFiles(self.home) as files,
            patch("kairos_filesystem.tree.os.listxattr", side_effect=OSError("unavailable")),
            self.assertRaises(SkillMutationError) as raised,
        ):
            reconcile_pending(
                self.repository, files, name=self.name, policy=personal_policy(self, files.home)
            )

        self.assertEqual(raised.exception.kind, "unavailable")
        self.assertIs(self.repository.get(operation_id).state, SkillMutationState.PREPARED)
        self.assertEqual(self.tree_state(), before)
        self.assertEqual(
            self.repository.snapshot(removed.operation_id).contents["SKILL.md"],
            b"# Legacy without semver\n",
        )

    def _prepare_restore_record(self, removed, adoption):
        operation_id = uuid.uuid4().hex
        snapshot = self.repository.snapshot(removed.operation_id)
        files = SkillRemovalFiles(self.home)
        files.__enter__()
        try:
            staged = files.stage(operation_id, snapshot)
            draft = SkillTreeDraft(
                operation_id,
                self.repository.home_id,
                SkillTreeAction.RESTORE,
                self.name,
                Actor.USER_FOREGROUND,
                1.0,
                Provenance.USER,
                TreeCapture(staged.entries, None),
                removed.operation_id,
                removed.operation_id,
                AdoptionAuthority(adoption.operation_id, operation_id, True),
                adoption.draft.installation_source,
            )
            self.repository._tree_repository().prepare_restore(
                draft, policy_receipt=personal_receipt(draft, self.repository._tree_repository())
            )
            return operation_id, files
        except BaseException:
            files.__exit__(None, None, None)
            raise

    def _commit_recovery_backup(self):
        self.reopen_backup()
        pending = self.repository.pending(self.name)
        self.assertEqual(len(pending), 1)
        self.assertIs(pending[0].state, SkillMutationState.PREPARED)
        return pending[0]

    def test_revoked_or_protected_incomplete_restore_preserves_versions(self):
        self.new_home()
        adoption = self.adopt()
        removed = self.remove()
        restored = self.mutations.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.adoptions.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        restored_state = self.tree_state()
        stale_id, files = self._prepare_restore_record(removed, adoption)
        preserved_current = self.root / "preserved-current"
        self.target.rename(preserved_current)
        files.publish(stale_id, self.name)
        files.sync_parents()
        files.__exit__(None, None, None)
        stale_record = self._commit_recovery_backup()
        self.assertEqual(stale_record.draft.adoption.adoption_id, adoption.operation_id)

        with self.assertRaises(SkillMutationError):
            self.reconcile()

        self.assertIs(self.repository.get(stale_id).state, SkillMutationState.CONFLICT)
        self.assertEqual(self.tree_state(), restored_state)
        self.assertEqual(self.tree_state(preserved_current), restored_state)
        self.assertEqual(
            self.repository.snapshot(removed.operation_id).contents["SKILL.md"],
            b"# Legacy without semver\n",
        )
        self.assertEqual(
            self.repository.get(restored.operation_id).state, SkillMutationState.COMMITTED
        )

        self.new_home()
        adoption = self.adopt()
        removed = self.remove()
        protected_id, files = self._prepare_restore_record(removed, adoption)
        files.publish(protected_id, self.name)
        files.sync_parents()
        files.__exit__(None, None, None)
        protected_record = self._commit_recovery_backup()
        self.assertEqual(protected_record.draft.adoption.adoption_id, adoption.operation_id)
        protected_state = self.tree_state()
        bundle = self.root / "new-protected-bundle"
        bundle.mkdir()
        shutil.copytree(self.target, bundle / self.name)
        (self.home / "skills" / ".bundled_manifest").write_text(
            f"{self.name}:{origin_hash(bundle / self.name)}\n"
        )
        protected_capture = SkillRemovalFiles(self.home)
        with protected_capture:
            protected_tree = protected_capture.capture_installed(self.name)
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle):
            origin = resolve_removal_origin(self.home, self.name, None, protected_tree)
        self.assertEqual(origin.provenance, Provenance.BUNDLED)

        with (
            patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle),
            self.assertRaises(SkillMutationError),
        ):
            self.reconcile()

        self.assertIs(self.repository.get(protected_id).state, SkillMutationState.CONFLICT)
        self.assertEqual(self.tree_state(), protected_state)
        self.assertEqual(
            self.repository.snapshot(removed.operation_id).contents["SKILL.md"],
            b"# Legacy without semver\n",
        )
