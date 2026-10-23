"""Staging preserva origem; movimentos exigem identidade e guard correntes."""

import os
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.removal_io import capture_skill


def capture(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return replace(capture_skill(fd, path.name), contents=None)
    finally:
        os.close(fd)


class PublicationFilesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        (self.source / "SKILL.md").write_bytes(b"new version")
        self.proof = capture(self.source)
        self.operation_id = uuid.uuid4().hex

    def files(self):
        from kairos_skills.publication_files import PublicationFiles

        return self.stack.enter_context(PublicationFiles(self.home))

    def test_stage_copies_without_moving_source(self):
        from kairos_skills.publication_contract import publication_inventory_hash

        files = self.files()
        staged = files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)
        self.assertEqual(capture(self.source), self.proof)
        self.assertNotEqual(staged.entries[0].inode, self.proof.entries[0].inode)
        self.assertEqual(publication_inventory_hash(staged), publication_inventory_hash(self.proof))
        self.assertEqual(files.capture_staged(self.operation_id), staged)
        self.assertEqual((self.home / ".skill-publications").stat().st_mode & 0o777, 0o700)
        self.assertIsNone(staged.contents)

    def test_retire_publish_and_return_preserve_versions(self):
        files = self.files()
        target = self.home / "skills/personal-skill"
        target.mkdir()
        (target / "SKILL.md").write_bytes(b"old version")
        previous = files.capture_installed("personal-skill")
        staged = files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)
        files.retire("personal-skill", self.operation_id, previous, guard=lambda: None)
        self.assertEqual(files.capture_previous(self.operation_id), previous)
        self.assertIsNone(files.capture_installed("personal-skill"))
        files.return_previous(self.operation_id, "personal-skill", previous, guard=lambda: None)
        self.assertEqual(files.capture_installed("personal-skill"), previous)
        files.retire("personal-skill", self.operation_id, previous, guard=lambda: None)
        files.publish(self.operation_id, "personal-skill", staged, guard=lambda: None)
        files.sync_parents(guard=lambda: None)
        self.assertEqual(files.capture_installed("personal-skill"), staged)
        self.assertEqual(files.capture_previous(self.operation_id), previous)

    def test_changed_identity_or_guard_refuses_before_move(self):
        files = self.files()
        staged = files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)

        def veto():
            raise SkillMutationError("denied", "veto")

        with self.assertRaises(SkillMutationError):
            files.publish(self.operation_id, "personal-skill", staged, guard=veto)
        path = self.home / ".skill-publications" / self.operation_id / "staged"
        path.rename(path.with_name("unknown"))
        path.mkdir(mode=0o700)
        (path / "SKILL.md").write_bytes(b"new version")
        with self.assertRaises(SkillMutationError):
            files.publish(self.operation_id, "personal-skill", staged, guard=lambda: None)
        self.assertEqual((path / "SKILL.md").read_bytes(), b"new version")
        self.assertFalse((self.home / "skills/personal-skill").exists())

    def test_stage_collision_preserves_existing_tree(self):
        files = self.files()
        staged = files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)
        with self.assertRaises(SkillMutationError):
            files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)
        self.assertEqual(files.capture_staged(self.operation_id), staged)

    def test_changed_staging_in_guard_does_not_publish_another_tree(self):
        files = self.files()
        staged = files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)
        path = self.home / ".skill-publications" / self.operation_id / "staged"

        def replace_source():
            path.rename(path.with_name("preserved"))
            path.mkdir(mode=0o700)
            (path / "SKILL.md").write_bytes(b"new version")

        with self.assertRaises(SkillMutationError):
            files.publish(self.operation_id, "personal-skill", staged, guard=replace_source)
        self.assertFalse((self.home / "skills/personal-skill").exists())
        self.assertTrue(path.exists())

    def test_closed_files_refuse_effects(self):
        from kairos_skills.publication_files import PublicationFiles

        with PublicationFiles(self.home) as files:
            pass
        with self.assertRaises(SkillMutationError):
            files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)

    def test_unknown_operation_directory_is_not_adopted(self):
        files = self.files()
        orphan = self.home / ".skill-publications" / self.operation_id
        orphan.mkdir(mode=0o700)
        (orphan / "unknown").write_bytes(b"preserve")
        before = orphan.stat().st_ino
        with self.assertRaises(SkillMutationError):
            files.stage(self.operation_id, self.source, self.proof, guard=lambda: None)
        self.assertEqual(orphan.stat().st_ino, before)
        self.assertEqual((orphan / "unknown").read_bytes(), b"preserve")
        self.assertFalse((orphan / "staged").exists())

    def test_oversized_skill_and_links_refuse(self):
        files = self.files()
        for kind in ("oversized", "symlink", "hardlink"):
            with self.subTest(kind=kind):
                source = self.root / kind
                source.mkdir()
                if kind == "oversized":
                    (source / "SKILL.md").write_bytes(b"x" * 65537)
                elif kind == "symlink":
                    (source / "SKILL.md").symlink_to(self.source / "SKILL.md")
                else:
                    os.link(self.source / "SKILL.md", source / "SKILL.md")
                with self.assertRaises(SkillMutationError):
                    files.stage(uuid.uuid4().hex, source, self.proof, guard=lambda: None)
                if kind == "hardlink":
                    (source / "SKILL.md").unlink()

    def test_stage_from_other_filesystem_preserves_source(self):
        from kairos_skills.publication_contract import publication_inventory_hash

        external_root = os.environ.get("KAIROS_PUBLICATION_CROSS_DEVICE_ROOT")
        if not external_root:
            self.skipTest("Requer raiz temporária em outro device; repetir em tmpfs no aceite T7.")
        external = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=external_root)))
        source = external / "skill"
        source.mkdir(mode=0o700)
        (source / "SKILL.md").write_bytes(b"external filesystem")
        proof = capture(source)
        self.assertNotEqual(source.stat().st_dev, self.home.stat().st_dev)
        staged = self.files().stage(self.operation_id, source, proof, guard=lambda: None)
        self.assertNotEqual(staged.entries[0].device, proof.entries[0].device)
        self.assertEqual(publication_inventory_hash(staged), publication_inventory_hash(proof))
        self.assertEqual(capture(source), proof)
