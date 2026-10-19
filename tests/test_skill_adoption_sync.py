"""Exclusões de sync derivadas de remoções adotadas comprovadas."""

import unittest

import test_skill_adoption_service as adoption_fixtures
from skill_mutation_fixtures import creation_text
from skill_org_fixtures import personal_policy

from kairos_domain.ownership import Actor
from kairos_skills.mutation_contract import SkillMutationState
from kairos_skills.mutations import SkillMutationService
from kairos_skills.sync import sync_bundled_skills


class SkillAdoptionSyncTests(unittest.TestCase):
    name = "revisar-docs"

    def setUp(self):
        adoption_fixtures.SkillAdoptionServiceTests.setUp(self)
        adoption_fixtures.SkillAdoptionServiceTests.legacy(self)
        self.repository = adoption_fixtures.SkillAdoptionServiceTests.ledger(self)
        self.adoptions = adoption_fixtures.adoption_service_type()(
            self.home, self.repository, policy=personal_policy(self, self.home)
        )
        self.mutations = SkillMutationService(
            self.home, self.repository, policy=personal_policy(self, self.home)
        )

    def legacy(self):
        return adoption_fixtures.SkillAdoptionServiceTests.legacy(self)

    def bundle(self, name=None):
        name = self.name if name is None else name
        root = self.root / "future-bundle"
        skill = root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Future bundled skill\n")
        return root

    def adopted_removal(self):
        adopted = self.adoptions.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        removed = self.mutations.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        return adopted.record, removed

    def test_adopted_removal_blocks_future_bundle(self):
        _, removed = self.adopted_removal()
        future = self.bundle()

        result = sync_bundled_skills(
            future,
            self.home / "skills",
            policy=personal_policy(self, ((self.home / "skills").parent)),
        )

        self.assertEqual(removed.name, self.name)
        self.assertIn(self.name, self.repository.bundled_tombstones())
        self.assertIn(self.name, result.user_deleted)
        self.assertFalse(self.target.exists())

    def test_U_B_create_do_not_clear_local_exclusion(self):
        adoption_a, _ = self.adopted_removal()
        self.assertIn(self.name, self.repository.bundled_tombstones())

        source = self.root / "created-after-removal.md"
        source.write_text(creation_text(name=self.name, body="new independent version"))
        created = self.mutations.add(source, actor=Actor.USER_FOREGROUND)
        self.assertIn(self.name, self.repository.bundled_tombstones())
        self.mutations.rollback(created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertIn(self.name, self.repository.bundled_tombstones())

        self.legacy()
        adopted_b = self.adoptions.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertNotEqual(adopted_b.record.operation_id, adoption_a.operation_id)
        self.adoptions.unadopt(
            self.name,
            actor=Actor.USER_FOREGROUND,
            confirmed=True,
            expect_adoption=adopted_b.record.operation_id,
        )

        self.assertIn(self.name, self.repository.bundled_tombstones())
        future = self.bundle()
        result = sync_bundled_skills(
            future,
            self.home / "skills",
            policy=personal_policy(self, ((self.home / "skills").parent)),
        )
        self.assertIn(self.name, result.user_deleted)
        self.assertTrue(self.target.exists())

    def test_only_linked_committed_restore_clears_exclusion(self):
        _, removed = self.adopted_removal()
        self.assertIn(self.name, self.repository.bundled_tombstones())

        restored = self.mutations.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)

        self.assertEqual(restored.reverts, removed.operation_id)
        self.assertIs(restored.state, SkillMutationState.COMMITTED)
        self.assertNotIn(self.name, self.repository.bundled_tombstones())

    def test_unlinked_USER_restore_preserves_adopted_exclusion(self):
        self.adopted_removal()
        source = self.root / "created-after-adopted-removal.md"
        source.write_text(creation_text(name=self.name, body="independent version"))
        created = self.mutations.add(source, actor=Actor.USER_FOREGROUND)
        removed_user = self.mutations.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIsNone(removed_user.draft.adoption)
        restored_user = self.mutations.rollback(
            removed_user.operation_id, actor=Actor.USER_FOREGROUND
        )
        self.assertIsNone(restored_user.draft.adoption)
        self.assertEqual(restored_user.reverts, removed_user.operation_id)
        self.assertNotEqual(created.operation_id, removed_user.operation_id)

        future = self.bundle()
        result = sync_bundled_skills(
            future,
            self.home / "skills",
            policy=personal_policy(self, ((self.home / "skills").parent)),
        )

        self.assertIn(self.name, self.repository.bundled_tombstones())
        self.assertIn(self.name, result.user_deleted)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), source.read_bytes())

    def test_old_USER_removals_unchanged(self):
        source = self.root / "independent.md"
        source.write_text(creation_text(name="manual-proof", body="independent"))
        independent = self.mutations.add(source, actor=Actor.USER_FOREGROUND)
        self.mutations.remove(independent.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        future = self.bundle(independent.name)

        result = sync_bundled_skills(
            future,
            self.home / "skills",
            policy=personal_policy(self, ((self.home / "skills").parent)),
        )

        self.assertNotIn(independent.name, self.repository.bundled_tombstones())
        self.assertIn(independent.name, result.copied)
        self.assertTrue((self.home / "skills" / independent.name / "SKILL.md").exists())
