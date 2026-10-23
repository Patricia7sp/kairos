"""Publicações incompletas bloqueiam todos os escritores, inclusive repetições."""

from test_skill_publication_authority import PublicationAuthorityFixture, home_contents

from kairos_domain.ownership import Actor
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.hub import SkillBundle, install_from_quarantine, quarantine_bundle
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_recovery import reconcile_pending
from kairos_skills.removal_io import SkillRemovalFiles
from kairos_skills.sync import sync_bundled_skills


class PublicationPendingTests(PublicationAuthorityFixture):
    def test_every_writer_refuses_committed_cleanup_pending(self):
        original = self.manual()
        _publications, draft = self.publish(complete=False)
        adoption = SkillAdoptionService(self.home, self.common, policy=self.policy)
        quarantine = self.home / "legacy-quarantine"
        source = quarantine_bundle(SkillBundle(self.name, {"SKILL.md": b"replacement"}), quarantine)
        bundle = self.home / "empty-package"
        bundle.mkdir()
        calls = {
            "add": lambda: self.service().add(self.source, actor=Actor.USER_FOREGROUND),
            "remove": lambda: self.service().remove(
                self.name, actor=Actor.USER_FOREGROUND, confirmed=True
            ),
            "rollback": lambda: self.service().rollback(
                original.operation_id, actor=Actor.USER_FOREGROUND
            ),
            "adopt": lambda: adoption.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True),
            "unadopt": lambda: adoption.unadopt(
                self.name, actor=Actor.USER_FOREGROUND, confirmed=True
            ),
            "sync": lambda: sync_bundled_skills(bundle, self.home / "skills", policy=self.policy),
            "promote": lambda: install_from_quarantine(
                source, quarantine, self.home / "skills", policy=self.policy
            ),
        }
        with SkillRemovalFiles(self.home) as files:
            calls["legacy_recovery"] = lambda: reconcile_pending(
                self.common, files, name=self.name, policy=self.policy
            )
            before = home_contents(self.home)
            history = self.common.history()
            for action, call in calls.items():
                with self.subTest(action=action), self.assertRaises(SkillMutationError) as caught:
                    call()
                self.assertEqual(caught.exception.kind, "conflict")
                self.assertEqual(caught.exception.operation_id, draft.operation_id)
                self.assertEqual(home_contents(self.home), before)
                self.assertEqual(self.common.history(), history)

    def test_prepared_publication_blocks_before_legacy_recovery(self):
        self.manual()
        publications, draft = self.prepare_publication()
        with SkillRemovalFiles(self.home) as files:
            before = home_contents(self.home)
            with (
                self.assertLogs("kairos_skills.mutation_recovery", level="WARNING") as logs,
                self.assertRaises(SkillMutationError) as caught,
            ):
                reconcile_pending(self.common, files, name=self.name, policy=self.policy)
            self.assertEqual(caught.exception.operation_id, draft.operation_id)
            self.assertEqual(caught.exception.kind, "conflict")
            self.assertEqual(logs.records[-1].operation_id, draft.operation_id)
            self.assertNotIn(str(self.home), repr(logs.records[-1].__dict__))
            self.assertEqual(
                publications.get(draft.operation_id).state, SkillMutationState.PREPARED
            )
            self.assertEqual(home_contents(self.home), before)

    def test_sync_refuses_pending_name_present_only_in_publication_journal(self):
        _publications, draft = self.publish(complete=False)
        missing = self.home / "missing-package"
        before = home_contents(self.home)
        with self.assertRaises(SkillMutationError) as caught:
            sync_bundled_skills(missing, self.home / "skills", policy=self.policy)
        self.assertEqual(caught.exception.operation_id, draft.operation_id)
        self.assertEqual(home_contents(self.home), before)

    def test_rollback_repetition_checks_publication_before_returning_old_success(self):
        original = self.manual()
        self.service().rollback(original.operation_id, actor=Actor.USER_FOREGROUND)
        _publications, draft = self.publish(complete=False)
        before = home_contents(self.home)
        with self.assertRaises(SkillMutationError) as caught:
            self.service().rollback(original.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(caught.exception.operation_id, draft.operation_id)
        self.assertEqual(home_contents(self.home), before)

    def test_rollback_of_publication_id_refuses_without_restore(self):
        _publications, draft = self.publish()
        before = home_contents(self.home)
        with self.assertRaises(SkillMutationError) as caught:
            self.service().rollback(draft.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(caught.exception.kind, "input")
        self.assertEqual(home_contents(self.home), before)

    def test_committed_manifest_gap_blocks_even_after_source_cleanup(self):
        publications, draft = self.publish(manifest=True, complete=False)
        publications.confirm_cleanup(draft.operation_id, guard=self.policy.revalidate)
        self.assertTrue(publications.get(draft.operation_id).cleanup_complete)
        self.assertFalse(publications.get(draft.operation_id).manifest_complete)
        before = home_contents(self.home)
        with self.assertRaises(SkillMutationError) as caught:
            self.service().remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(caught.exception.operation_id, draft.operation_id)
        self.assertEqual(home_contents(self.home), before)

    def test_restore_repetition_checks_publication_before_old_success(self):
        self.manual()
        removed = self.service().remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.service().rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        _publications, draft = self.publish(complete=False)
        before = home_contents(self.home)
        with self.assertRaises(SkillMutationError) as caught:
            self.service().rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(caught.exception.operation_id, draft.operation_id)
        self.assertEqual(home_contents(self.home), before)
