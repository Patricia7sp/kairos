"""Escritores legados recusam alvo organizacional e preservam resíduos vetados."""

import argparse
import gc
import io
import json
import shutil
from contextlib import redirect_stdout
from unittest.mock import patch

from test_skill_org_services import OrgServiceFixture

from kairos_cli import handlers
from kairos_domain.ownership import Actor
from kairos_skills import sync
from kairos_skills.hub import SkillBundle, install_from_quarantine, quarantine_bundle
from kairos_skills.mutation_contract import SkillMutationError


class OrgPublicationTests(OrgServiceFixture):
    def assert_pending_promotion_preserves_versions(self, install):
        with (
            patch.object(
                self.repository, "finish", side_effect=SkillMutationError("io", "interrupted")
            ),
            self.assertRaises(SkillMutationError),
        ):
            install()
        pending = self.repository.pending("org-skill")
        self.assertEqual(len(pending), 1)
        self.assertTrue(self.protections.prepared_receipt(pending[0].operation_id).bindings)
        self.assertFalse(self.protections.protected_organizations("org-skill"))
        target = self.home / "skills/org-skill"
        before = target.stat().st_ino, (target / "SKILL.md").read_bytes()
        history = self.repository.history()
        quarantine = self.root / "quarantine"
        source = quarantine_bundle(
            SkillBundle("org-skill", {"SKILL.md": b"replacement"}), quarantine
        )
        source_before = source.stat().st_ino
        with self.assertRaises(SkillMutationError) as caught:
            install_from_quarantine(source, quarantine, self.home / "skills", policy=self.none)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(caught.exception.operation_id, pending[0].operation_id)
        self.assertEqual((target.stat().st_ino, (target / "SKILL.md").read_bytes()), before)
        self.assertEqual(source.stat().st_ino, source_before)
        self.assertEqual((source / "SKILL.md").read_bytes(), b"replacement")
        self.assertEqual(self.repository.history(), history)
        self.assertEqual(self.repository.pending("org-skill"), pending)

    def test_promotion_preserves_pending_protected_creation_without_current_binding(self):
        self.assert_pending_promotion_preserves_versions(
            lambda: self.service(self.policy).add(self.source, actor=Actor.USER_FOREGROUND)
        )

    def test_promotion_preserves_pending_protected_restore_without_current_binding(self):
        self.service().add(self.source, actor=Actor.USER_FOREGROUND)
        removed = self.service().remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assert_pending_promotion_preserves_versions(
            lambda: self.service(self.policy).rollback(
                removed.operation_id, actor=Actor.USER_FOREGROUND
            )
        )

    def bundle(self, name="org-skill", text="bundle"):
        bundled = self.root / "bundle"
        target = bundled / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(text)
        return bundled

    def test_initial_sync_blocks_bound_destination_without_publication(self):
        result = sync.sync_bundled_skills(self.bundle(), self.home / "skills", policy=self.policy)
        self.assertEqual(result.blocked, ["org-skill"])
        self.assertTrue(result.failed)
        self.assertEqual(result.copied, [])
        self.assertFalse((self.home / "skills/org-skill").exists())
        manifest = self.home / "skills" / sync.MANIFEST_NAME
        self.assertNotIn("org-skill", sync.read_manifest(manifest))

    def test_update_block_preserves_installed_root_and_manifest(self):
        bundle = self.bundle()
        sync.sync_bundled_skills(bundle, self.home / "skills", policy=self.none)
        target = self.home / "skills/org-skill"
        before = (
            target.stat(),
            (target / "SKILL.md").read_bytes(),
            (self.home / "skills" / sync.MANIFEST_NAME).read_bytes(),
        )
        self.bundle(text="new")
        result = sync.sync_bundled_skills(bundle, self.home / "skills", policy=self.policy)
        self.assertTrue(result.failed)
        self.assertEqual(result.updated, [])
        self.assertEqual(
            (
                target.stat(),
                (target / "SKILL.md").read_bytes(),
                (self.home / "skills" / sync.MANIFEST_NAME).read_bytes(),
            ),
            before,
        )

    def test_partial_batch_reports_completed_personal_skill_and_block(self):
        bundle = self.bundle("aaa-personal")
        self.bundle()
        result = sync.sync_bundled_skills(bundle, self.home / "skills", policy=self.policy)
        self.assertEqual(result.copied, ["aaa-personal"])
        self.assertEqual(result.blocked, ["org-skill"])
        self.assertTrue(result.failed)
        self.assertTrue((self.home / "skills/aaa-personal/SKILL.md").exists())

    def test_promotion_veto_preserves_quarantine_and_existing_destination(self):
        quarantine = self.root / "quarantine"
        source = quarantine_bundle(SkillBundle("org-skill", {"SKILL.md": b"new"}), quarantine)
        skills = self.home / "skills"
        target = skills / "org-skill"
        for occupied in (False, True):
            with self.subTest(occupied=occupied):
                if occupied:
                    target.mkdir(parents=True)
                    (target / "SKILL.md").write_bytes(b"old")
                with self.assertRaises(SkillMutationError) as caught:
                    install_from_quarantine(source, quarantine, skills, policy=self.policy)
                self.assertEqual(caught.exception.kind, "denied")
                self.assertEqual((source / "SKILL.md").read_bytes(), b"new")
                self.assertEqual(target.exists(), occupied)
                if occupied:
                    self.assertEqual((target / "SKILL.md").read_bytes(), b"old")

    def test_direct_publication_requires_same_home_session(self):
        bundle = self.bundle()
        with self.assertRaises(TypeError):
            sync.sync_bundled_skills(bundle, self.home / "skills")
        with self.assertRaises(SkillMutationError):
            sync.sync_bundled_skills(bundle, self.root / "other/skills", policy=self.none)
        with self.assertRaises(TypeError):
            install_from_quarantine(self.root, self.root, self.home / "skills")

    def test_source_change_after_staging_never_replaces_destination(self):
        bundle = self.bundle()
        sync.sync_bundled_skills(bundle, self.home / "skills", policy=self.none)
        self.bundle(text="new")
        original = shutil.copytree

        # A file-mode policy with no binding permits this personal publication.
        from kairos_skills.org_policy import observe_org_policy
        from kairos_skills.org_policy_contract import OrgPolicyContext

        source = self.root / "personal-policy.json"
        source.write_text('{"version":1,"bindings":[]}')
        policy = self.enterContext(observe_org_policy(OrgPolicyContext("file", source), self.home))

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[1].name == "new":
                source.write_text('{"version":1, "bindings": []}')
            return result

        with (
            patch.object(shutil, "copytree", side_effect=changed),
            self.assertRaises(SkillMutationError),
        ):
            sync.sync_bundled_skills(bundle, self.home / "skills", policy=policy)
        self.assertEqual((self.home / "skills/org-skill/SKILL.md").read_text(), "bundle")
        gc.collect()
        self.assertTrue(list((self.home / "skills").glob(".skill-sync-*/*/new/SKILL.md")))

    def test_vetoed_compensation_preserves_both_versions_after_gc(self):
        from kairos_skills.org_policy import observe_org_policy
        from kairos_skills.org_policy_contract import OrgPolicyContext

        bundle = self.bundle()
        sync.sync_bundled_skills(bundle, self.home / "skills", policy=self.none)
        self.bundle(text="new")
        source = self.root / "personal-policy.json"
        source.write_text('{"version":1,"bindings":[]}')
        policy = self.enterContext(observe_org_policy(OrgPolicyContext("file", source), self.home))

        def fail(*args):
            source.write_text('{"version":1, "bindings": []}')
            raise OSError("private-backend-sentinel")

        with (
            patch.object(sync, "write_manifest", side_effect=fail),
            self.assertRaises(SkillMutationError),
        ):
            sync.sync_bundled_skills(bundle, self.home / "skills", policy=policy)
        gc.collect()
        self.assertEqual((self.home / "skills/org-skill/SKILL.md").read_text(), "new")
        old = list((self.home / "skills").glob(".skill-sync-*/*/old/SKILL.md"))
        self.assertEqual([path.read_text() for path in old], ["bundle"])

    def test_cli_partial_batch_returns_77_with_json_fields(self):
        self.bundle("aaa-personal")
        self.bundle()
        # cmd_skills derives its bundled root from this module's path.
        bundle_project = self.root / "project"
        (bundle_project / "kairos_cli").mkdir(parents=True)
        shutil.copytree(self.root / "bundle", bundle_project / "skills")
        output = io.StringIO()
        with (
            patch.object(handlers, "__file__", str(bundle_project / "kairos_cli/handlers.py")),
            patch.object(handlers, "_home", return_value=self.home),
            patch.dict(
                handlers.os.environ,
                {"KAIROS_SKILL_ORG_POLICY": str(self.root / "private-source-sentinel.json")},
            ),
            redirect_stdout(output),
        ):
            code = handlers.cmd_skills(argparse.Namespace(skills_command="sync", json=True))
        self.assertEqual(code, 77)
        result = json.loads(output.getvalue())
        self.assertEqual(result["blocked"], ["org-skill"])
        self.assertTrue(result["failed"])
