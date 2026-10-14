"""Integração da linhagem revogável com remoção e restauração."""

import unittest

import test_skill_adoption_service as adoption_fixtures
from skill_mutation_fixtures import creation_text

from kairos_domain.ownership import Actor
from kairos_skills.adoption_contract import AdoptionAuthority
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutations import SkillMutationService


class SkillAdoptionLineageTests(unittest.TestCase):
    name = "revisar-docs"

    def setUp(self):
        adoption_fixtures.SkillAdoptionServiceTests.setUp(self)
        adoption_fixtures.SkillAdoptionServiceTests.legacy(self)
        self.repository = self.ledger()
        self.adoptions = adoption_fixtures.adoption_service_type()(self.home, self.repository)
        self.mutations = SkillMutationService(self.home, self.repository)

    def ledger(self):
        return adoption_fixtures.SkillAdoptionServiceTests.ledger(self)

    def adopt(self):
        result = self.adoptions.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIsNotNone(result.record)
        return result.record

    def remove(self):
        return self.mutations.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

    def restore(self, removed):
        return self.mutations.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)

    def inventory(self):
        return adoption_fixtures.SkillAdoptionServiceTests.inventory(self)

    def test_A_M_R_U_remove_denied(self):
        adoption = self.adopt()
        (self.target / "SKILL.md").write_bytes(b"# internal edit after adoption\n")

        removed = self.remove()
        self.assertEqual(
            removed.draft.adoption,
            AdoptionAuthority(adoption.operation_id, adoption.draft.installation_id, True),
        )
        self.assertEqual(removed.draft.installation_source, adoption.draft.installation_source)
        restored = self.restore(removed)
        self.assertEqual(
            restored.draft.adoption,
            AdoptionAuthority(adoption.operation_id, restored.operation_id, True),
        )
        self.assertEqual(restored.draft.installation_source, adoption.draft.installation_source)
        self.assertEqual(identify_skill(self.home, self.name).adoption_id, adoption.operation_id)

        revoked = self.adoptions.unadopt(
            self.name,
            actor=Actor.USER_FOREGROUND,
            confirmed=True,
            expect_adoption=adoption.operation_id,
        )
        before = self.inventory()
        history = self.repository.history(name=self.name)
        with self.assertRaises(SkillMutationError):
            self.remove()

        self.assertIs(revoked.state, SkillMutationState.COMMITTED)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.repository.history(name=self.name), history)

    def test_multiple_restores_do_not_escape_U(self):
        adoption = self.adopt()
        first_removed = self.remove()
        first_restore = self.restore(first_removed)
        second_removed = self.remove()
        second_restore = self.restore(second_removed)
        self.assertEqual(first_restore.draft.adoption.adoption_id, adoption.operation_id)
        self.assertEqual(second_restore.draft.adoption.adoption_id, adoption.operation_id)

        self.adoptions.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        before = self.inventory()
        history = self.repository.history(name=self.name)
        for removed in (first_removed, second_removed):
            with self.subTest(remove_id=removed.operation_id):
                with self.assertRaises(SkillMutationError) as raised:
                    self.restore(removed)
                self.assertEqual(raised.exception.kind, "denied")

        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.repository.history(name=self.name), history)

    def test_B_after_U_requires_confirmation(self):
        adoption = self.adopt()
        removed = self.remove()
        self.restore(removed)
        self.adoptions.unadopt(
            self.name,
            actor=Actor.USER_FOREGROUND,
            confirmed=True,
            expect_adoption=adoption.operation_id,
        )
        before = self.inventory()

        with self.assertRaises(SkillMutationError) as raised:
            self.adoptions.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=False)
        self.assertEqual(raised.exception.kind, "denied")
        self.assertEqual(self.inventory(), before)

        second = self.adoptions.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIsNotNone(second.record)
        self.assertNotEqual(second.record.operation_id, adoption.operation_id)
        self.assertTrue(second.identification.adoption_active)
        self.assertEqual(self.repository.get(adoption.operation_id), adoption)

    def test_expected_A_preserves_B(self):
        adoption = self.adopt()
        removed = self.remove()
        self.restore(removed)
        self.adoptions.unadopt(
            self.name,
            actor=Actor.USER_FOREGROUND,
            confirmed=True,
            expect_adoption=adoption.operation_id,
        )
        second = self.adoptions.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True).record
        before = self.repository.history(name=self.name)

        with self.assertRaises(SkillMutationError):
            self.adoptions.unadopt(
                self.name,
                actor=Actor.USER_FOREGROUND,
                confirmed=True,
                expect_adoption=adoption.operation_id,
            )

        current = self.repository.installation_evidence(self.name)
        self.assertEqual(current.adoption.adoption_id, second.operation_id)
        self.assertTrue(current.adoption.active)
        self.assertEqual(self.repository.history(name=self.name), before)

    def test_independent_grant_not_revoked(self):
        independent_name = "manual-proof"
        independent_target = self.home / "skills" / independent_name
        source = self.root / "created.md"
        source.write_text(creation_text(name=independent_name))
        created = self.mutations.add(source, actor=Actor.USER_FOREGROUND)
        proof = self.adoptions.adopt(independent_name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIsNone(proof.record)

        with self.assertRaises(SkillMutationError):
            self.adoptions.unadopt(independent_name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(
            self.repository.current_installation(independent_name).operation_id,
            created.operation_id,
        )
        self.assertEqual((independent_target / "SKILL.md").read_bytes(), source.read_bytes())

    def test_rollback_A_U_points_to_inverse_without_file_changes(self):
        adoption = self.adopt()
        before = self.inventory()
        for operation_id in (adoption.operation_id,):
            with (
                self.subTest(operation_id=operation_id),
                self.assertRaises(SkillMutationError) as raised,
            ):
                self.mutations.rollback(operation_id, actor=Actor.USER_FOREGROUND)
            self.assertIn("unadopt", str(raised.exception).lower())
            self.assertIn("adopt", str(raised.exception).lower())
        self.assertEqual(self.inventory(), before)

        revoked = self.adoptions.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        before = self.inventory()
        with self.assertRaises(SkillMutationError) as raised:
            self.mutations.rollback(revoked.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertIn("adopt", str(raised.exception).lower())
        self.assertNotIn("unadopt", str(raised.exception).lower())
        self.assertEqual(self.inventory(), before)
