"""Recuperação observa layouts reais, preserva conflitos e repete IDs."""

from unittest.mock import patch

from test_skill_publication_files import capture
from test_skill_publication_service import Interrupted, PublicationServiceFixture

from kairos_skills.mutation_contract import SkillMutationState
from kairos_skills.publication_recovery import reconcile_publication


class PublicationRecoveryTests(PublicationServiceFixture):
    def test_recovery_table_and_repeat(self):
        for replacement, point, committed in (
            (False, "prepare", False),
            (False, "publish", True),
            (True, "prepare", False),
            (True, "retire", False),
            (True, "publish", True),
        ):
            with self.subTest(replacement=replacement, point=point):
                fixture = PublicationServiceFixture()
                fixture.setUp()
                try:
                    session = fixture.start(replacement=replacement)
                    owner = session.publications if point == "prepare" else session.files
                    record = fixture.stop_after(owner, point)
                    result = reconcile_publication(session, record)
                    self.assertEqual(result.complete, committed)
                    self.assertIs(
                        result.state,
                        SkillMutationState.COMMITTED if committed else SkillMutationState.ABORTED,
                    )
                    self.assertEqual(fixture.service.recover(record.operation_id), result)
                    if committed:
                        self.assertEqual(capture(fixture.target), record.draft.staged_tree)
                        self.assertFalse(fixture.source.exists())
                    elif replacement:
                        self.assertEqual(capture(fixture.target), fixture.previous)
                        self.assertTrue(fixture.source.exists())
                    else:
                        self.assertFalse(fixture.target.exists())
                    self.assertIsNone(session.files.capture_staged(record.operation_id))
                finally:
                    fixture.doCleanups()

    def test_duplicate_new_tree_stays_pending_and_preserved(self):
        session = self.start(replacement=True)
        record = self.stop_after(session.files, "publish")
        staged = self.home / ".skill-publications" / record.operation_id / "staged"
        staged.mkdir(mode=0o700)
        (staged / "SKILL.md").write_bytes(b"unknown duplicate")
        before = capture(staged)
        result = self.service.recover(record.operation_id)
        self.assertFalse(result.complete)
        self.assertIs(result.state, SkillMutationState.CONFLICT)
        self.assertEqual(capture(staged), before)
        self.assertEqual(capture(self.target), record.draft.staged_tree)
        self.assertEqual(session.files.capture_previous(record.operation_id), self.previous)

    def test_changed_source_preserves_both_versions_pending(self):
        session = self.start(replacement=True)
        record = self.stop_after(session.files, "publish")
        (self.source / "SKILL.md").write_bytes(b"changed source")
        result = self.service.recover(record.operation_id)
        self.assertFalse(result.complete)
        self.assertEqual(capture(self.target), record.draft.staged_tree)
        self.assertEqual(session.files.capture_previous(record.operation_id), self.previous)

    def test_committed_does_not_repeat_install_movement(self):
        session = self.start(replacement=True)
        result = self.publish()
        with (
            patch.object(session.files, "publish") as publish,
            patch.object(session.files, "retire") as retire,
        ):
            repeated = self.service.recover(result.operation_id)
        self.assertEqual(repeated, result)
        publish.assert_not_called()
        retire.assert_not_called()

    def test_recovery_in_fresh_session_uses_same_id(self):
        session = self.start(replacement=True)
        record = self.stop_after(session.files, "publish")
        self.reopen()
        result = self.service.recover(record.operation_id)
        self.assertTrue(result.complete)
        self.assertEqual(result.operation_id, record.operation_id)
        self.assertEqual(self.service.recover(record.operation_id), result)

    def test_new_policy_veto_before_return_preserves_both_private_trees(self):
        session = self.start(replacement=True)
        record = self.stop_after(session.files, "retire")
        self.protect()
        session = self.reopen()
        result = self.service.recover(record.operation_id)
        self.assertFalse(result.complete)
        self.assertFalse(self.target.exists())
        self.assertEqual(session.files.capture_previous(record.operation_id), self.previous)
        self.assertEqual(
            session.files.capture_staged(record.operation_id), record.draft.staged_tree
        )

    def test_cleanup_without_confirmation_stays_pending_if_source_absent(self):
        from kairos_skills import publications

        session = self.start(replacement=True)
        original = publications.delete_publication

        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise Interrupted

        with (
            patch.object(publications, "delete_publication", side_effect=interrupt),
            self.assertRaises(Interrupted),
        ):
            self.publish()
        record = session.publications.history(name=self.name)[-1]
        self.assertFalse(record.cleanup_complete)
        self.assertFalse(self.source.exists())
        session = self.reopen()
        result = self.service.recover(record.operation_id)
        self.assertFalse(result.complete)
        self.assertIs(result.state, SkillMutationState.COMMITTED)
        self.assertFalse(result.cleanup_complete)
        self.assertEqual(capture(self.target), record.draft.staged_tree)
        self.assertEqual(session.files.capture_previous(record.operation_id), self.previous)

    def test_unknown_extra_private_artifact_prevents_commit(self):
        session = self.start(replacement=True)
        record = self.stop_after(session.files, "publish")
        unknown = self.home / ".skill-publications" / record.operation_id / "unknown"
        unknown.write_bytes(b"preserve me")
        result = self.service.recover(record.operation_id)
        self.assertFalse(result.complete)
        self.assertEqual(unknown.read_bytes(), b"preserve me")

    def test_complete_id_still_requires_compatible_quarantine_context(self):
        self.start()
        result = self.publish()
        self.quarantine = self.root / "other-quarantine"
        self.quarantine.mkdir(mode=0o700)
        self.reopen()
        repeated = self.service.recover(result.operation_id)
        self.assertFalse(repeated.complete)
        self.assertEqual(repeated.operation_id, result.operation_id)
