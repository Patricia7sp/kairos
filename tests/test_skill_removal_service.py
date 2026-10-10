"""Prova de origem baseada em instalação durável e bundle do pacote."""

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_skill_mutation_service as service_fixtures
import test_skill_removal_io as io_fixtures

import kairos_filesystem.tree as tree_io
from kairos_domain.ownership import Actor, Provenance
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import InstallationProof
from kairos_skills.removal_io import SkillRemovalFiles
from kairos_skills.removal_origin import prove_removal_origin
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class RemovalOriginTests(unittest.TestCase):
    def setUp(self):
        io_fixtures.RemovalIOTests.setUp(self)
        with SkillRemovalFiles(self.home) as files:
            self.capture = files.capture_installed("example")
        root = self.capture.entries[0]
        self.current = InstallationProof(uuid.uuid4().hex, Provenance.USER, root.device, root.inode)
        self.bundle = self.home / "package-skills"
        self.bundle.mkdir()
        shutil.copytree(self.target, self.bundle / "example")
        self.manifest = self.home / "skills/.bundled_manifest"

    def test_edited_manual_directory_can_be_removed(self):
        (self.target / "SKILL.md").write_bytes(b"legacy edited without semver")
        with SkillRemovalFiles(self.home) as files:
            captured = files.capture_installed("example")
        self.assertIs(
            prove_removal_origin(self.home, "example", self.current, captured), Provenance.USER
        )

    def test_identical_replaced_directory_refused(self):
        self.target.rename(self.home / "original")
        shutil.copytree(self.home / "original", self.target)
        with SkillRemovalFiles(self.home) as files:
            captured = files.capture_installed("example")
        with self.assertRaises(SkillMutationError):
            prove_removal_origin(self.home, "example", self.current, captured)

    def test_unknown_origin_refused(self):
        with (
            patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=self.bundle),
            self.assertRaises(SkillMutationError),
        ):
            prove_removal_origin(self.home, "example", None, self.capture)

    def test_bundled_proof_requires_full_tree_and_v2(self):
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=self.bundle):
            for value in (
                "example\n",
                "example:broken\n",
                "example:" + "a" * 32 + "\nexample:" + "a" * 32 + "\n",
            ):
                self.manifest.write_text(value)
                with self.subTest(manifest=value), self.assertRaises(SkillMutationError):
                    prove_removal_origin(self.home, "example", None, self.capture)
            self.manifest.write_text("example:" + "0" * 32 + "\n")
            self.assertIs(
                prove_removal_origin(self.home, "example", None, self.capture), Provenance.BUNDLED
            )
            (self.target / "scripts/run.bin").write_bytes(b"changed")
            with SkillRemovalFiles(self.home) as files:
                captured = files.capture_installed("example")
            with self.assertRaises(SkillMutationError):
                prove_removal_origin(self.home, "example", None, captured)

    def test_duplicate_bundle_name_refused(self):
        category = self.bundle / "category"
        category.mkdir()
        shutil.copytree(self.target, category / "example")
        self.manifest.write_text("example:" + "0" * 32 + "\n")
        with (
            patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=self.bundle),
            self.assertRaises(SkillMutationError),
        ):
            prove_removal_origin(self.home, "example", None, self.capture)

    def test_malformed_manifest_is_conflict_and_not_invalid_command(self):
        self.manifest.write_text("../invalid:" + "0" * 32 + "\n")
        with (
            patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=self.bundle),
            self.assertRaises(SkillMutationError) as raised,
        ):
            prove_removal_origin(self.home, "example", None, self.capture)
        self.assertEqual(raised.exception.kind, "conflict")


class RemovalServiceTests(unittest.TestCase):
    def setUp(self):
        service_fixtures.MutationServiceTests.setUp(self)
        self.created = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.name = self.created.name
        self.target = self.home / "skills" / self.name
        (self.target / "SKILL.md").write_bytes(b"# Legacy edited without semver\n")
        (self.target / "scripts").mkdir()
        (self.target / "scripts/run.bin").write_bytes(b"\x00\xff")
        (self.target / "scripts/run.bin").chmod(0o750)
        (self.target / ".empty").mkdir(mode=0o710)

    def remove(self):
        return self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

    def test_remove_restore_preserves_legacy_and_assets(self):
        original_identity = self.target.stat().st_ino
        removed = self.remove()
        self.assertFalse(self.target.exists())
        self.assertIs(removed.state, SkillMutationState.COMMITTED)
        restored = self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(restored.reverts, removed.operation_id)
        self.assertNotEqual(self.target.stat().st_ino, original_identity)
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")
        self.assertEqual((self.target / "scripts/run.bin").stat().st_mode & 0o777, 0o750)
        self.assertTrue((self.target / ".empty").is_dir())
        self.assertEqual(
            self.repo.snapshot(removed.operation_id).contents["scripts/run.bin"], b"\x00\xff"
        )
        self.assertEqual(
            self.repo.current_installation(self.name).operation_id, restored.operation_id
        )
        self.assertEqual(
            self.repo.content(self.created.operation_id).text.encode(), self.source.read_bytes()
        )
        with self.assertRaises(SkillMutationError):
            self.service.rollback(restored.operation_id, actor=Actor.USER_FOREGROUND)

    def test_repeat_remove_requires_current_durable_proof(self):
        removed = self.remove()
        self.assertEqual(self.remove(), removed)
        retired = self.home / ".skill-mutations/retired" / removed.operation_id
        (retired / "scripts/run.bin").write_bytes(b"changed")
        with self.assertRaises(SkillMutationError):
            self.remove()
        self.assertFalse(self.target.exists())
        self.assertEqual(
            self.repo.snapshot(removed.operation_id).contents["scripts/run.bin"], b"\x00\xff"
        )

    def test_stale_remove_cannot_restore_over_newer_remove(self):
        first = self.remove()
        self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        second = self.remove()
        self.assertNotEqual(first.operation_id, second.operation_id)
        with self.assertRaises(SkillMutationError) as raised:
            self.service.rollback(first.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(raised.exception.operation_id, second.operation_id)
        self.assertFalse(self.target.exists())

    def test_repeat_restore_does_not_touch_new_installation(self):
        removed = self.remove()
        restored = self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        second = self.remove()
        new = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        inode = self.target.stat().st_ino
        self.assertEqual(
            self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND), restored
        )
        self.assertEqual(self.target.stat().st_ino, inode)
        self.assertEqual(self.repo.current_installation(self.name).operation_id, new.operation_id)
        self.assertEqual(self.repo.current_removal(self.name).operation_id, second.operation_id)

    def test_identical_replacement_has_no_effect(self):
        original = self.home / "saved"
        self.target.rename(original)
        shutil.copytree(original, self.target)
        with self.assertRaises(SkillMutationError):
            self.remove()
        self.assertTrue(self.target.exists())
        self.assertEqual(len(self.repo.history()), 1)

    def test_oversized_existing_skill_is_conflict_and_preserved(self):
        (self.target / "SKILL.md").write_bytes(b"x" * 65537)
        with self.assertRaises(SkillMutationError) as raised:
            self.remove()
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertEqual(len(self.repo.history()), 1)
        self.assertEqual((self.target / "SKILL.md").stat().st_size, 65537)

    def test_confirmation_actor_and_name_refused_before_io(self):
        before = set(self.home.iterdir())
        for kwargs in (
            {"actor": Actor.USER_FOREGROUND},
            {"actor": Actor.CURATOR, "confirmed": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(SkillMutationError) as raised:
                self.service.remove(self.name, **kwargs)
            self.assertEqual(raised.exception.kind, "denied")
        with self.assertRaises(SkillMutationError):
            self.service.remove("../escape", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(set(self.home.iterdir()), before)
        self.assertEqual(len(self.repo.history()), 1)

    def test_open_descriptor_edit_after_retire_preserves_conflict(self):
        original = SkillRemovalFiles.retire
        with (self.target / "scripts/run.bin").open("r+b") as descriptor:

            def retire_then_edit(files, name, operation_id):
                original(files, name, operation_id)
                descriptor.seek(0)
                descriptor.write(b"edited")
                descriptor.flush()

            with (
                patch.object(SkillRemovalFiles, "retire", retire_then_edit),
                self.assertRaises(SkillMutationError) as raised,
            ):
                self.remove()
        operation_id = raised.exception.operation_id
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"edited")
        self.assertEqual(self.repo.snapshot(operation_id).contents["scripts/run.bin"], b"\x00\xff")

    def test_restore_collision_preserves_snapshot_and_foreign_tree(self):
        removed = self.remove()
        self.target.mkdir()
        (self.target / "SKILL.md").write_bytes(b"foreign")
        with self.assertRaises(SkillMutationError):
            self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"foreign")
        self.assertIs(self.repo.get(removed.operation_id).state, SkillMutationState.COMMITTED)

    def test_old_rollback_cannot_erase_restore(self):
        removed = self.remove()
        restored = self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        with self.assertRaises(SkillMutationError):
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertTrue(self.target.exists())
        self.assertEqual(
            self.repo.current_installation(self.name).operation_id, restored.operation_id
        )

    def test_later_create_and_rollback_invalidate_old_remove(self):
        removed = self.remove()
        self.assertFalse(self.repo.has_later_installation(removed.operation_id))
        created = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.service.rollback(created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertTrue(self.repo.has_later_installation(removed.operation_id))
        with self.assertRaises(SkillMutationError):
            self.remove()
        with self.assertRaises(SkillMutationError):
            self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertFalse(self.target.exists())

    def test_remove_before_prepare_failure_preserves_original(self):
        inode = self.target.stat().st_ino
        with (
            patch.object(
                self.repo, "prepare_remove", side_effect=SkillMutationError("io", "fixture")
            ),
            self.assertRaises(SkillMutationError),
        ):
            self.remove()
        self.assertEqual(self.target.stat().st_ino, inode)
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")
        self.assertEqual(len(self.repo.history()), 1)

    def test_edited_retirement_and_occupied_name_preserve_both(self):
        original = SkillRemovalFiles.retire

        def retire_and_replace(files, name, operation_id):
            original(files, name, operation_id)
            retired = self.home / ".skill-mutations/retired" / operation_id
            (retired / "scripts/run.bin").write_bytes(b"edited")
            self.target.mkdir()
            (self.target / "SKILL.md").write_bytes(b"foreign")

        with (
            patch.object(SkillRemovalFiles, "retire", retire_and_replace),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.remove()
        operation_id = raised.exception.operation_id
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"foreign")
        retired = self.home / ".skill-mutations/retired" / operation_id
        self.assertEqual((retired / "scripts/run.bin").read_bytes(), b"edited")
        self.assertEqual(self.repo.snapshot(operation_id).contents["scripts/run.bin"], b"\x00\xff")

    def test_invalid_retired_root_mode_preserves_conflict(self):
        original = SkillRemovalFiles.retire

        def retire_then_change_mode(files, name, operation_id):
            original(files, name, operation_id)
            (self.home / ".skill-mutations/retired" / operation_id).chmod(0o1700)

        with (
            patch.object(SkillRemovalFiles, "retire", retire_then_change_mode),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.remove()
        operation_id = raised.exception.operation_id
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.CONFLICT)
        retired = self.home / ".skill-mutations/retired" / operation_id
        self.assertEqual((retired / "scripts/run.bin").read_bytes(), b"\x00\xff")
        self.assertFalse(self.target.exists())

    def test_restore_publication_collision_preserves_both(self):
        removed = self.remove()
        original = SkillRemovalFiles.publish

        def occupy_before_publish(files, operation_id, name):
            self.target.mkdir()
            (self.target / "SKILL.md").write_bytes(b"foreign")
            original(files, operation_id, name)

        with (
            patch.object(SkillRemovalFiles, "publish", occupy_before_publish),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        operation_id = raised.exception.operation_id
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.PREPARED)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"foreign")
        staging = self.home / ".skill-mutations/staging" / operation_id
        self.assertEqual((staging / "scripts/run.bin").read_bytes(), b"\x00\xff")

    def test_restore_stops_writes_when_private_ancestor_moves(self):
        removed = self.remove()
        private = self.home / ".skill-mutations"
        moved = self.root / "moved-private"
        original_write = os.write

        def write_then_move(fd, data):
            written = original_write(fd, data)
            if private.exists():
                private.rename(moved)
            return written

        with (
            patch("kairos_filesystem.tree.os.write", write_then_move),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        operation_id = raised.exception.operation_id
        self.assertNotEqual(operation_id, removed.operation_id)
        staged = moved / "staging" / operation_id
        self.assertTrue(staged.exists())
        self.assertFalse((staged / "scripts").exists())
        self.assertIsNone(self.repo.get(operation_id))
        self.assertEqual(
            self.repo.snapshot(removed.operation_id).contents["scripts/run.bin"], b"\x00\xff"
        )
        self.assertFalse(self.target.exists())

    def test_restore_does_not_allocate_inflated_skill_before_refusing(self):
        (self.target / "scripts/large.bin").write_bytes(b"x" * 131072)
        removed = self.remove()
        staging = self.home / ".skill-mutations/staging"
        original_fsync = os.fsync
        original_read = tree_io._read_file
        allocations = []
        inflated = False

        def fsync_then_inflate(fd):
            nonlocal inflated
            original_fsync(fd)
            if not inflated and Path(os.readlink(f"/proc/self/fd/{fd}")) == staging:
                staged = next(staging.iterdir())
                (staged / "SKILL.md").write_bytes(b"x" * 65537)
                inflated = True

        def read_and_observe(fd, size, *, include_contents):
            path = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if path.name == "SKILL.md" and size > 65536 and include_contents:
                allocations.append(size)
            return original_read(fd, size, include_contents=include_contents)

        with (
            patch.object(tree_io.os, "fsync", fsync_then_inflate),
            patch.object(tree_io, "_read_file", read_and_observe),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertTrue(inflated)
        self.assertEqual(allocations, [])
        self.assertIsNone(self.repo.get(raised.exception.operation_id))
        self.assertFalse(self.target.exists())
        self.assertEqual(
            self.repo.snapshot(removed.operation_id).contents["scripts/large.bin"], b"x" * 131072
        )


class RemovalBundledServiceTests(unittest.TestCase):
    def setUp(self):
        io_fixtures.RemovalIOTests.setUp(self)
        self.bundle = self.home / "package-skills"
        self.bundle.mkdir()
        shutil.copytree(self.target, self.bundle / "example")
        (self.home / "skills/.bundled_manifest").write_text("example:" + "0" * 32 + "\n")
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db)
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.service = SkillMutationService(self.home, self.repo)
        resolver = patch(
            "kairos_skills.removal_origin.trusted_bundle_root", return_value=self.bundle
        )
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_bundled_restore_keeps_origin_and_clears_tombstone_only_at_commit(self):
        removed = self.service.remove("example", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIs(removed.provenance, Provenance.BUNDLED)
        self.assertIn("example", self.repo.bundled_tombstones())
        original = self.repo.prepare_restore

        def prepare_then_error(draft):
            original(draft)
            self.assertIn("example", self.repo.bundled_tombstones())
            raise SkillMutationError("io", "fixture")

        with (
            patch.object(self.repo, "prepare_restore", prepare_then_error),
            self.assertRaises(SkillMutationError),
        ):
            self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertIn("example", self.repo.bundled_tombstones())
        restored = self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertIs(restored.provenance, Provenance.BUNDLED)
        self.assertNotIn("example", self.repo.bundled_tombstones())
        (self.target / "scripts/run.bin").write_bytes(b"edited current bundled restore")
        edited = self.service.remove("example", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIs(edited.provenance, Provenance.BUNDLED)
        self.assertEqual(
            self.repo.snapshot(edited.operation_id).contents["scripts/run.bin"],
            b"edited current bundled restore",
        )

    def test_unknown_and_edited_unrecorded_bundle_do_not_remove(self):
        (self.target / "scripts/run.bin").write_bytes(b"edited")
        with self.assertRaises(SkillMutationError):
            self.service.remove("example", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(self.repo.history(), ())
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"edited")
