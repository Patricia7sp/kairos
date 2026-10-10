"""Árvores de skills reais e cópias privadas independentes."""

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from kairos_filesystem.contract import FilesystemError
from kairos_skills import removal_io
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.removal_io import SkillRemovalFiles


class RemovalIOTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.target = self.home / "skills/example"
        self.target.mkdir(parents=True)
        (self.target / "SKILL.md").write_bytes(b"# Legacy without frontmatter\n")
        (self.target / "scripts").mkdir()
        (self.target / "scripts/run.bin").write_bytes(b"\x00\xff")
        (self.target / "scripts/run.bin").chmod(0o750)
        (self.target / ".empty").mkdir(mode=0o710)
        self.operation_id = uuid.uuid4().hex

    def test_tree_roundtrip_has_new_identity(self):
        with SkillRemovalFiles(self.home) as files:
            original = files.capture_installed("example")
            files.retire("example", self.operation_id)
            copy_id = uuid.uuid4().hex
            staged = files.stage(copy_id, original)
            self.assertNotEqual(original.entries[0].inode, staged.entries[0].inode)
            files.publish(copy_id, "example")
            self.assertEqual(files.capture_installed("example"), staged)
            self.assertEqual(original.contents, staged.contents)
            self.assertEqual(
                [(e.path, e.mode) for e in original.entries],
                [(e.path, e.mode) for e in staged.entries],
            )
            self.assertTrue((self.target / ".empty").is_dir())

    def test_snapshot_is_separate_from_retired_tree(self):
        with SkillRemovalFiles(self.home) as files:
            captured = files.capture_installed("example")
            files.retire("example", self.operation_id)
            retired = self.home / ".skill-mutations/retired" / self.operation_id
            (retired / "scripts/run.bin").write_bytes(b"changed")
            self.assertEqual(captured.contents["scripts/run.bin"], b"\x00\xff")
            self.assertNotEqual(files.capture_private(self.operation_id, retired=True), captured)
            files.return_retired(self.operation_id, "example")
            self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"changed")

    def test_publish_collision_preserves_both(self):
        with SkillRemovalFiles(self.home) as files:
            original = files.capture_installed("example")
            staged = files.stage(self.operation_id, original)
            with self.assertRaises(SkillMutationError):
                files.publish(self.operation_id, "example")
            self.assertEqual(files.capture_installed("example"), original)
            self.assertEqual(files.capture_private(self.operation_id, retired=False), staged)

    def test_private_orphans_untouched(self):
        orphan = self.home / ".skill-mutations/staging" / self.operation_id
        orphan.mkdir(parents=True)
        orphan.parent.chmod(0o700)
        orphan.parent.parent.chmod(0o700)
        (orphan / "unknown").write_bytes(b"preserve")
        with SkillRemovalFiles(self.home) as files:
            self.assertIsNone(files.capture_installed("absent"))
        self.assertEqual((orphan / "unknown").read_bytes(), b"preserve")

    def test_skill_limit_and_required_regular_file(self):
        for value in (None, b"x" * 65537):
            with self.subTest(value=value is None):
                skill = self.target / "SKILL.md"
                skill.unlink(missing_ok=True)
                if value is not None:
                    skill.write_bytes(value)
                with SkillRemovalFiles(self.home) as files, self.assertRaises(SkillMutationError):
                    files.capture_installed("example")

    def test_all_tree_limits_include_hidden_files_and_directories(self):
        for kind in ("file", "total", "entries", "depth"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                target = home / "skills/example"
                target.mkdir(parents=True)
                (target / "SKILL.md").write_bytes(b"legacy")
                if kind in ("file", "total"):
                    for number in range(1 if kind == "file" else 4):
                        with (target / f".asset-{number}").open("wb") as output:
                            output.truncate(16 * 1024 * 1024 + (1 if kind == "file" else 0))
                elif kind == "entries":
                    for number in range(4095):
                        (target / f".directory-{number}").mkdir()
                else:
                    nested = target
                    for _ in range(33):
                        nested /= "nested"
                        nested.mkdir()
                with SkillRemovalFiles(home) as files, self.assertRaises(SkillMutationError):
                    files.capture_installed("example")
                self.assertEqual((target / "SKILL.md").read_bytes(), b"legacy")

    def test_unsafe_creation_is_refused_before_private_directories(self):
        with (
            patch(
                "kairos_skills.removal_io.assert_creation_supported",
                side_effect=FilesystemError("unavailable", "fixture"),
            ),
            self.assertRaises(SkillMutationError) as raised,
            SkillRemovalFiles(self.home),
        ):
            self.fail("Unsupported filesystem accepted")
        self.assertEqual(raised.exception.kind, "unavailable")
        self.assertFalse((self.home / ".skill-mutations").exists())

    def test_links_and_special_modes_are_preserved_on_refusal(self):
        asset = self.target / "scripts/run.bin"
        linked = self.target / ".linked"
        linked.symlink_to(asset)
        with SkillRemovalFiles(self.home) as files, self.assertRaises(SkillMutationError):
            files.capture_installed("example")
        self.assertTrue(linked.is_symlink())
        linked.unlink()
        asset.chmod(0o4750)
        with SkillRemovalFiles(self.home) as files, self.assertRaises(SkillMutationError):
            files.capture_installed("example")
        self.assertEqual(asset.read_bytes(), b"\x00\xff")

    def test_transient_directory_replacement_refused_during_content_read(self):
        original_open = removal_io.open_fd
        original_read = removal_io.read_skill_file
        scripts = self.target / "scripts"
        saved = self.home / "original-scripts"

        def swap_directory(stack, name, flags, **kwargs):
            if name == "scripts":
                scripts.rename(saved)
                scripts.mkdir()
                (saved / "run.bin").rename(scripts / "run.bin")
            return original_open(stack, name, flags, **kwargs)

        def read_and_return(parent, name, limit, **kwargs):
            result = original_read(parent, name, limit, **kwargs)
            if name == "run.bin":
                (scripts / "run.bin").rename(saved / "run.bin")
                scripts.rmdir()
                saved.rename(scripts)
            return result

        with (
            SkillRemovalFiles(self.home) as files,
            patch.object(removal_io, "open_fd", swap_directory),
            patch.object(removal_io, "read_skill_file", read_and_return),
            self.assertRaises(SkillMutationError),
        ):
            files.capture_installed("example")

    def test_private_permissions_changed_after_open_are_refused(self):
        with SkillRemovalFiles(self.home) as files:
            (self.home / ".skill-mutations").chmod(0o755)
            with self.assertRaises(SkillMutationError):
                files.capture_installed("example")
        self.assertEqual((self.target / "scripts/run.bin").read_bytes(), b"\x00\xff")

    def test_posix_backslash_asset_roundtrip(self):
        asset = self.target / "scripts" / "legacy\\asset.bin"
        asset.write_bytes(b"\x00\x01")
        asset.chmod(0o640)
        with SkillRemovalFiles(self.home) as files:
            original = files.capture_installed("example")
            self.assertEqual(original.contents["scripts/legacy\\asset.bin"], b"\x00\x01")
            files.retire("example", self.operation_id)
            staged_id = uuid.uuid4().hex
            copied = files.stage(staged_id, original)
            files.publish(staged_id, "example")
            self.assertEqual(files.capture_installed("example"), copied)
        self.assertEqual(asset.read_bytes(), b"\x00\x01")
        self.assertEqual(asset.stat().st_mode & 0o777, 0o640)
