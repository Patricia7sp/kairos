"""Leituras e chamadas diretas não podem afastar proteção organizacional."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text
from skill_org_fixtures import bound_policy, personal_policy

from kairos_domain.ownership import Actor
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.catalog import catalog_home_id
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.mutations import SkillMutationService
from kairos_skills.org_policy_contract import OrgPolicyAction
from kairos_skills.org_policy_guard import SkillOrgGuard
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_org_protections import SkillOrgProtectionRepository


class OrgServiceFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.connection = connect(self.home / "state.db")
        self.addCleanup(self.connection.close)
        migrate(self.connection)
        self.repository = SkillMutationRepository(
            self.connection, home_id=catalog_home_id(self.home)
        )
        self.none = personal_policy(self, self.home)
        self.policy = bound_policy(self, self.home, self.root / "private-source-sentinel.json")
        self.source = self.root / "SKILL.md"
        self.source.write_text(creation_text(name="org-skill"))
        self.protections = SkillOrgProtectionRepository(
            self.connection, home_id=self.repository.home_id
        )

    def service(self, policy=None):
        return SkillMutationService(self.home, self.repository, policy=policy or self.none)

    def assert_denied(self, call):
        before = self.repository.history()
        with self.assertRaises(SkillMutationError) as caught:
            call()
        self.assertEqual(caught.exception.kind, "denied")
        self.assertEqual(self.repository.history(), before)
        self.assertNotIn("private-", str(caught.exception))


class OrgServicesTests(OrgServiceFixture):
    def test_home_created_during_session_keeps_identity_before_service_effects(self):
        for mode in ("none", "file"):
            for replaced in ("home", "ancestor"):
                with self.subTest(mode=mode, replaced=replaced):
                    parent = self.root / f"new-{mode}-{replaced}"
                    home = parent / "home"
                    policy = (
                        personal_policy(self, home)
                        if mode == "none"
                        else bound_policy(self, home, self.root / f"{mode}-{replaced}.json")
                    )
                    home.mkdir(parents=True)
                    connection = connect(home / "state.db")
                    self.addCleanup(connection.close)
                    migrate(connection)
                    repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
                    service = SkillMutationService(home, repository, policy=policy)
                    moved = self.root / f"moved-{mode}-{replaced}"
                    (home if replaced == "home" else parent).rename(moved)
                    home.mkdir(parents=True)
                    with self.assertRaises(SkillMutationError) as caught:
                        service.add(self.source, actor=Actor.USER_FOREGROUND)
                    self.assertEqual(caught.exception.kind, "conflict")
                    self.assertEqual(repository.history(), ())
                    self.assertFalse((home / "skills").exists())
                    self.assertFalse((home / "state.db").exists())

    def test_guard_action_matrix_and_reserved_file_components(self):
        guard = SkillOrgGuard(self.policy, self.protections)
        for action in OrgPolicyAction:
            with self.subTest(action=action):
                if action in (
                    OrgPolicyAction.CREATE,
                    OrgPolicyAction.RESTORE,
                    OrgPolicyAction.UNADOPT,
                ):
                    self.assertTrue(guard.check("org-skill", action).bindings)
                else:
                    self.assert_denied(lambda action=action: guard.check("org-skill", action))
        for path in ("_org", "_org/skill/SKILL.md", "normal/_org/SKILL.md"):
            with self.subTest(path=path):
                self.assert_denied(lambda path=path: guard.check(path, OrgPolicyAction.REMOVE_FILE))

    def test_services_require_live_session_of_same_home(self):
        for constructor in (SkillMutationService, SkillAdoptionService):
            with self.subTest(constructor=constructor):
                with self.assertRaises(TypeError):
                    constructor(self.home, self.repository)
                with self.assertRaises(SkillMutationError):
                    constructor(self.home, self.repository, policy=personal_policy(self, self.root))
        policy = personal_policy(self, self.home)
        service = self.service(policy)
        policy._closed = True
        with self.assertRaises(SkillMutationError):
            service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(self.repository.history(), ())

    def test_confirmation_and_adoption_do_not_override_current_binding(self):
        created = self.service().add(self.source, actor=Actor.USER_FOREGROUND)
        target = self.home / "skills/org-skill"
        before = target.stat(), (target / "SKILL.md").read_bytes()
        service = self.service(self.policy)
        self.assert_denied(
            lambda: service.remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        )
        self.assert_denied(
            lambda: service.rollback(created.operation_id, actor=Actor.USER_FOREGROUND)
        )
        adoption = SkillAdoptionService(self.home, self.repository, policy=self.policy)
        self.assert_denied(
            lambda: adoption.adopt("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        )
        self.assertEqual((target.stat(), (target / "SKILL.md").read_bytes()), before)
        self.assertEqual(self.protections.protected_organizations("org-skill"), frozenset())

    def test_creation_records_binding_after_commit_and_survives_inode_replacement(self):
        service = self.service(self.policy)
        created = service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(
            self.protections.protected_organizations("org-skill"), {"private-organization-sentinel"}
        )
        self.assert_denied(
            lambda: self.service().rollback(created.operation_id, actor=Actor.USER_FOREGROUND)
        )
        target = self.home / "skills/org-skill"
        target.rename(self.root / "old")
        shutil.copytree(self.root / "old", target)
        self.assert_denied(
            lambda: self.service().remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        )

    def test_restore_records_new_binding_with_original_authority(self):
        service = self.service()
        service.add(self.source, actor=Actor.USER_FOREGROUND)
        removed = service.remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        restored = self.service(self.policy).rollback(
            removed.operation_id, actor=Actor.USER_FOREGROUND
        )
        self.assertEqual(restored.reverts, removed.operation_id)
        self.assertEqual(
            self.protections.protected_organizations("org-skill"), {"private-organization-sentinel"}
        )
        self.assert_denied(
            lambda: service.remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        )

    def test_revocation_preserves_recorded_protection(self):
        target = self.home / "skills/org-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("legacy")
        adopter = SkillAdoptionService(self.home, self.repository, policy=self.none)
        adopter.adopt("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        removed = self.service().remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.service(self.policy).rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        adopter.unadopt("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assert_denied(
            lambda: self.service().remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        )
        self.assertTrue(self.protections.protected_organizations("org-skill"))

    def test_identify_reports_current_and_recorded_veto_without_writing(self):
        self.service().add(self.source, actor=Actor.USER_FOREGROUND)
        before = self.repository.history()
        report = identify_skill(self.home, "org-skill", policy=self.policy)
        self.assertIn("organization_policy", report.known_protection)
        self.assertEqual(report.protection_coverage, "policy_and_recorded")
        self.assertFalse(report.removivel)
        self.assertIn("organization_observation_not_persisted", report.reasons)
        self.assertEqual(self.repository.history(), before)
        self.assertFalse(self.protections.protected_organizations("org-skill"))
        self.assertNotIn("private-", repr(report))

    def test_policy_change_at_preparation_preserves_staging_and_pending_record(self):
        prepare = self.repository.prepare

        def changed(*args, **kwargs):
            result = prepare(*args, **kwargs)
            (self.root / "private-source-sentinel.json").write_text('{"version":1,"bindings":[]}')
            return result

        with (
            patch.object(self.repository, "prepare", side_effect=changed),
            self.assertRaises(SkillMutationError) as caught,
        ):
            self.service(self.policy).add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertFalse((self.home / "skills/org-skill").exists())
        self.assertEqual(len(self.repository.pending("org-skill")), 1)
        self.assertTrue(any((self.home / ".skill-mutations/staging").iterdir()))
