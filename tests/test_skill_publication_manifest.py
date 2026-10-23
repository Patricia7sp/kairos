"""Manifesto compare-and-apply não reinstala nem perde entradas alheias."""

from unittest.mock import patch

from test_skill_publication_files import capture
from test_skill_publication_writers import PublicationWriterFixture

from kairos_skills import sync
from kairos_skills.publication_session import publication_session
from kairos_skills.sync import MANIFEST_NAME, read_manifest, write_manifest


class PublicationManifestTests(PublicationWriterFixture):
    def test_catalog_cleanup_resumes_same_id_after_installed_tree_removed(self):
        from kairos_state.repositories.skill_publications import SkillPublicationRepository

        source = self.skill()
        first = self.sync()
        op = first.publications[0].operation_id
        source.rename(self.root / "old-source")
        installed = self.home / "skills/personal-skill"
        installed.rename(self.root / "user-preserved")
        with patch.object(
            SkillPublicationRepository,
            "confirm_manifest",
            side_effect=OSError("after metadata fsync"),
        ):
            result = self.sync()
        self.assertTrue(result.failed)
        self.assertEqual(result.pending, [op])
        self.assertFalse(installed.exists())
        repeated = self.sync()
        self.assertFalse(repeated.failed)
        self.assertEqual(repeated.publications[0].operation_id, op)
        self.assertFalse(installed.exists())

    def test_changed_manifest_before_confirmation_remains_pending(self):
        from kairos_state.repositories.skill_publications import SkillPublicationRepository

        self.skill()
        original = SkillPublicationRepository.confirm_manifest

        def change(repository, *args, **kwargs):
            from kairos_skills.publication_contract import ManifestDelta
            from kairos_skills.publication_manifest import apply_manifest_delta

            apply_manifest_delta(
                self.home, ManifestDelta("unrelated", None, "b" * 32), guard=self.policy.revalidate
            )
            return original(repository, *args, **kwargs)

        with patch.object(SkillPublicationRepository, "confirm_manifest", new=change):
            result = self.sync()
        self.assertTrue(result.failed)
        self.assertEqual(len(result.pending), 1)
        self.assertFalse(result.publications[0].manifest_complete)
        self.assertEqual(read_manifest(self.home / "skills" / MANIFEST_NAME)["unrelated"], "b" * 32)

    def test_committed_manifest_gap_does_not_republish(self):
        self.skill()
        with patch.object(sync, "reconcile_manifest", side_effect=OSError("private backend")):
            first = self.sync()
        self.assertTrue(first.failed)
        self.assertEqual(len(first.pending), 1)
        installed = capture(self.home / "skills/personal-skill")
        result = self.sync()
        self.assertFalse(result.failed)
        self.assertEqual(capture(self.home / "skills/personal-skill"), installed)
        self.assertEqual(result.publications[0].operation_id, first.pending[0])
        self.assertTrue(result.publications[0].complete)

    def test_manifest_conflict_preserves_other_items(self):
        self.skill()
        with patch.object(sync, "reconcile_manifest", side_effect=OSError("stop")):
            first = self.sync()
        path = self.home / "skills" / MANIFEST_NAME
        values = {"personal-skill": "a" * 32, "unrelated": "b" * 32}
        write_manifest(path, values)
        before = path.read_bytes()
        result = self.sync()
        self.assertTrue(result.failed)
        self.assertEqual(result.pending, first.pending)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(read_manifest(path), values)

    def test_manifest_written_without_confirmation_recognizes_after(self):
        from kairos_state.repositories.skill_publications import SkillPublicationRepository

        self.skill()

        def fail(repository, *args, **kwargs):
            raise OSError("before confirmation")

        with patch.object(SkillPublicationRepository, "confirm_manifest", new=fail):
            first = self.sync()
        self.assertTrue(first.failed)
        installed = capture(self.home / "skills/personal-skill")
        manifest = (self.home / "skills" / MANIFEST_NAME).read_bytes()
        result = self.sync()
        self.assertFalse(result.failed)
        self.assertEqual(result.publications[0].operation_id, first.pending[0])
        self.assertEqual(capture(self.home / "skills/personal-skill"), installed)
        self.assertEqual((self.home / "skills" / MANIFEST_NAME).read_bytes(), manifest)

    def test_package_removal_cleans_manifest_without_installation_event(self):
        source = self.skill()
        first = self.sync()
        op = first.publications[0].operation_id
        target = self.home / "skills/personal-skill"
        installed = capture(target)
        source.rename(self.root / "saved-package")
        result = self.sync()
        self.assertEqual(result.cleaned, ["personal-skill"])
        self.assertNotIn("personal-skill", read_manifest(self.home / "skills" / MANIFEST_NAME))
        self.assertEqual(capture(target), installed)
        with (
            self.context() as context,
            publication_session(self.home, policy=self.policy, context=context) as session,
        ):
            self.assertEqual(
                [r.operation_id for r in session.publications.history(name="personal-skill")], [op]
            )

    def test_strict_manifest_refuses_duplicate_and_link_before_publication(self):
        self.skill()
        skills = self.home / "skills"
        skills.mkdir()
        path = skills / MANIFEST_NAME
        path.write_text("same:\nsame:\n")
        from kairos_skills.mutation_contract import SkillMutationError

        with self.assertRaises(SkillMutationError):
            self.sync()
        self.assertFalse((skills / "personal-skill").exists())
        path.unlink()
        other = self.root / "private-manifest"
        other.write_text("")
        path.symlink_to(other)
        with self.assertRaises(SkillMutationError):
            self.sync()
        self.assertEqual(other.read_text(), "")
