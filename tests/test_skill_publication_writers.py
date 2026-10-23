"""Escritores públicos exigem composição real e respeitam intenção corrente."""

import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.hub import SkillBundle, install_from_quarantine, quarantine_bundle
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.mutations import SkillMutationService
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import observe_bundled_source
from kairos_skills.removal_state import database_connection
from kairos_skills.sync import MANIFEST_NAME, NO_BUNDLED_SKILLS_MARKER, sync_bundled_skills
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class PublicationWriterFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.bundle = self.root / "package"
        self.bundle.mkdir()
        self.policy = self.stack.enter_context(
            observe_org_policy(OrgPolicyContext("none", None), self.home)
        )

    def skill(self, name="personal-skill", data=b"package version"):
        path = self.bundle / name
        path.mkdir(exist_ok=True)
        (path / "SKILL.md").write_bytes(data)
        return path

    @contextmanager
    def context(self):
        with (
            patch(
                "kairos_skills.publication_sources.trusted_bundle_root", return_value=self.bundle
            ),
            observe_bundled_source(self.home, policy=self.policy) as context,
        ):
            yield context

    def sync(self):
        with self.context() as context:
            return sync_bundled_skills(
                self.bundle, self.home / "skills", policy=self.policy, context=context
            )

    def quarantine_replacement_of_bundle(self):
        from skill_publication_writer_fixtures import acquired_source
        from test_skill_publication_files import capture

        source = self.skill()
        self.assertFalse(self.sync().failed)
        target = self.home / "skills" / source.name
        quarantine = self.root / "quarantine"
        acquired, context, acquisition_id = acquired_source(
            self,
            self.home,
            quarantine,
            SkillBundle(source.name, {"SKILL.md": b"package version"}),
            policy=self.policy,
            replacement=capture(target),
        )
        install_from_quarantine(
            acquired,
            quarantine,
            self.home / "skills",
            policy=self.policy,
            context=context,
            acquisition_id=acquisition_id,
        )
        return source, target


class PublicationWriterTests(PublicationWriterFixture):
    def test_bundle_update_preserves_identical_quarantine_replacement(self):
        from test_skill_publication_files import capture

        from kairos_skills.identification import identify_skill

        source, target = self.quarantine_replacement_of_bundle()
        before = capture(target)
        self.assertEqual(
            identify_skill(self.home, source.name, policy=self.policy).installation_source,
            "verified_acquisition",
        )
        (source / "SKILL.md").write_bytes(b"future bundled update")
        result = self.sync()
        self.assertEqual(result.updated, [])
        self.assertEqual(result.user_modified, [source.name])
        self.assertFalse(result.failed)
        self.assertEqual(capture(target), before)
        observed = identify_skill(self.home, source.name, policy=self.policy)
        self.assertEqual(observed.installation_source, "verified_acquisition")
        self.assertFalse(observed.removivel)

    def test_direct_bundled_publication_refuses_quarantine_origin(self):
        from test_skill_publication_files import capture

        from kairos_skills.publication_contract import ManifestDelta
        from kairos_skills.publications import SkillPublicationService
        from kairos_skills.sync import origin_hash, read_manifest

        source, target = self.quarantine_replacement_of_bundle()
        before = capture(target)
        (source / "SKILL.md").write_bytes(b"future bundled update")
        with (
            self.context() as context,
            publication_session(self.home, policy=self.policy, context=context) as session,
        ):
            history = session.publications.history(name=source.name)
            delta = ManifestDelta(
                source.name,
                read_manifest(self.home / "skills" / MANIFEST_NAME)[source.name],
                origin_hash(source),
            )
            with self.assertRaises(SkillMutationError) as caught:
                SkillPublicationService(session).publish(source.name, manifest_delta=delta)
            self.assertEqual(caught.exception.kind, "input")
            self.assertEqual(session.publications.history(name=source.name), history)
        self.assertEqual(capture(target), before)

    def test_legacy_hash_eligibility_matches_attested_path_order(self):
        import shutil

        from kairos_skills.sync import origin_hash, read_manifest, write_manifest

        source = self.skill()
        (source / "dir").mkdir()
        (source / "dir/part.txt").write_bytes(b"nested")
        (source / "dir-part.txt").write_bytes(b"flat")
        target = self.home / "skills/personal-skill"
        target.parent.mkdir()
        shutil.copytree(source, target)
        before = origin_hash(source)
        write_manifest(target.parent / MANIFEST_NAME, {target.name: before})
        (source / "SKILL.md").write_bytes(b"updated package")
        result = self.sync()
        self.assertFalse(result.failed)
        self.assertEqual(result.updated, [target.name])
        self.assertEqual(
            read_manifest(target.parent / MANIFEST_NAME)[target.name], origin_hash(source)
        )

    def test_sync_never_hashes_target_symlink_before_refusing(self):
        from kairos_skills import sync

        self.skill()
        self.sync()
        target = self.home / "skills/personal-skill"
        target.rename(self.root / "previous-installed")
        private = self.root / "private-target"
        private.mkdir()
        (private / "SKILL.md").write_bytes(b"private fixture")
        target.symlink_to(private)
        self.skill(data=b"new package")
        original = sync.origin_hash

        def hash_regular(path):
            self.assertNotEqual(path, target, "Não ler alvo de link para decidir escrita.")
            return original(path)

        with patch.object(sync, "origin_hash", side_effect=hash_regular):
            result = self.sync()
        self.assertTrue(result.failed)
        self.assertTrue(target.is_symlink())
        self.assertEqual((private / "SKILL.md").read_bytes(), b"private fixture")

    def test_batch_releases_source_descriptors_after_complete_operations(self):
        import multiprocessing

        from skill_publication_writer_fixtures import descriptor_budget_worker

        for index in range(10):
            source = self.skill(f"skill-{index:02}")
            for attachment in range(4):
                (source / f"reference-{attachment}.txt").write_bytes(b"reference")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=descriptor_budget_worker, args=(self.home, self.bundle, child)
        )
        process.start()
        try:
            self.assertTrue(parent.poll(300), "Lote não concluiu sob o limite real de descritores.")
            payload = parent.recv()
            process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(
                payload, {"failed": False, "copied": 10, "skipped": 10, "complete": True}
            )
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
            parent.close()
            child.close()

    def test_dangling_optout_marker_preserves_manifest_and_installation(self):
        from test_skill_publication_files import capture

        self.skill()
        self.sync()
        target = self.home / "skills/personal-skill"
        before = capture(target)
        manifest = (self.home / "skills" / MANIFEST_NAME).read_bytes()
        (self.home / "skills" / NO_BUNDLED_SKILLS_MARKER).symlink_to(
            self.root / "missing-marker-target"
        )
        result = self.sync()
        self.assertTrue(result.skipped_opt_out)
        self.assertEqual((self.home / "skills" / MANIFEST_NAME).read_bytes(), manifest)
        self.assertEqual(capture(target), before)

    def test_identical_replaced_root_is_not_sync_eligible(self):
        import shutil

        from test_skill_publication_files import capture

        self.skill()
        self.sync()
        target = self.home / "skills/personal-skill"
        preserved = self.home / "preserved"
        target.rename(preserved)
        shutil.copytree(preserved, target)
        before = capture(target)
        self.skill(data=b"updated package")
        result = self.sync()
        self.assertTrue(result.failed)
        self.assertEqual(result.updated, [])
        self.assertEqual(capture(target), before)
        self.assertEqual((preserved / "SKILL.md").read_bytes(), b"package version")

    def test_direct_legacy_promote_cannot_bypass_journal(self):
        quarantine = self.root / "quarantine"
        source = quarantine_bundle(
            SkillBundle("personal-skill", {"SKILL.md": b"version"}), quarantine
        )
        with self.assertRaises(SkillMutationError) as caught:
            install_from_quarantine(source, quarantine, self.home / "skills", policy=self.policy)
        self.assertEqual(caught.exception.kind, "input")
        self.assertTrue(source.exists())
        self.assertFalse((self.home / "state.db").exists())
        self.assertFalse((self.home / "skills").exists())

    def test_direct_legacy_sync_cannot_bypass_journal(self):
        self.skill()
        with self.assertRaises(SkillMutationError) as caught:
            sync_bundled_skills(self.bundle, self.home / "skills", policy=self.policy)
        self.assertEqual(caught.exception.kind, "input")
        self.assertFalse((self.home / "state.db").exists())
        self.assertFalse((self.home / "skills").exists())

    def test_sync_preserves_edits_deletions_optout_and_tombstones(self):
        for name in ("edited", "deleted", "removed"):
            self.skill(name)
        first = self.sync()
        self.assertEqual(set(first.copied), {"edited", "deleted", "removed"})
        edited = self.home / "skills/edited/SKILL.md"
        edited.write_bytes(b"user edit")
        deleted = self.home / "skills/deleted"
        (deleted / "SKILL.md").unlink()
        deleted.rmdir()
        with database_connection(self.home, write=True) as connection:
            repo = SkillMutationRepository(connection, home_id=catalog_home_id(self.home))
            SkillMutationService(self.home, repo, policy=self.policy).remove(
                "removed", actor=Actor.USER_FOREGROUND, confirmed=True
            )
        for name in ("edited", "deleted", "removed"):
            self.skill(name, b"changed package")
        result = self.sync()
        self.assertEqual(result.user_modified, ["edited"])
        self.assertEqual(set(result.user_deleted), {"deleted", "removed"})
        self.assertEqual(edited.read_bytes(), b"user edit")
        self.assertFalse(deleted.exists())
        self.assertFalse((self.home / "skills/removed").exists())
        (self.home / "skills" / NO_BUNDLED_SKILLS_MARKER).touch()
        self.skill("extra")
        self.assertTrue(self.sync().skipped_opt_out)
        self.assertFalse((self.home / "skills/extra").exists())

    def test_optout_after_staging_refuses_publication(self):
        from kairos_skills.publication_files import PublicationFiles

        self.skill()
        original = PublicationFiles.stage

        def stage(files, *args, **kwargs):
            proof = original(files, *args, **kwargs)
            (self.home / "skills" / NO_BUNDLED_SKILLS_MARKER).touch()
            return proof

        with patch.object(PublicationFiles, "stage", new=stage):
            result = self.sync()
        self.assertTrue(result.failed)
        self.assertFalse(result.copied)
        self.assertFalse((self.home / "skills/personal-skill").exists())

    def test_changed_tombstone_before_effect_refuses_publication(self):
        import uuid
        from dataclasses import replace

        from kairos_domain.ownership import Provenance
        from kairos_skills.mutation_contract import SkillMutationState
        from kairos_skills.publication_files import PublicationFiles, capture_source
        from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
        from kairos_skills.removal_io import SkillRemovalFiles

        self.skill()
        self.sync()
        self.skill(data=b"changed package")
        original = PublicationFiles.stage

        def stage(files, *args, **kwargs):
            proof = original(files, *args, **kwargs)
            # Remoção real, composta com o lock já detido pela sessão de publicação.
            with database_connection(self.home, write=True) as connection:
                repo = SkillMutationRepository(connection, home_id=catalog_home_id(self.home))
                captured = capture_source(self.home / "skills/personal-skill")
                operation_id = uuid.uuid4().hex
                draft = SkillTreeDraft(
                    operation_id,
                    repo.home_id,
                    SkillTreeAction.REMOVE,
                    "personal-skill",
                    Actor.USER_FOREGROUND,
                    1.0,
                    Provenance.BUNDLED,
                    replace(captured, contents=None),
                    operation_id,
                )
                repo.prepare_remove(draft, captured)
                with SkillRemovalFiles(self.home) as removal:
                    removal.retire("personal-skill", operation_id)
                    removal.sync_parents()
                repo.finish(operation_id, SkillMutationState.COMMITTED)
            return proof

        with patch.object(PublicationFiles, "stage", new=stage):
            result = self.sync()
        self.assertTrue(result.failed)
        self.assertFalse((self.home / "skills/personal-skill").exists())
        with database_connection(self.home) as connection:
            self.assertIn(
                "personal-skill",
                SkillMutationRepository(
                    connection, home_id=catalog_home_id(self.home)
                ).bundled_tombstones(),
            )
