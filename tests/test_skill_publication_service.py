"""Publicação real preserva versões e só confirma efeitos comprovados."""

import json
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from test_skill_publication_files import capture

from kairos_domain.ownership import Actor
from kairos_skills.acquisitions import SkillAcquisitionService
from kairos_skills.hub import SkillBundle
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import observe_quarantine_source
from kairos_skills.publications import SkillPublicationService


class Interrupted(BaseException):
    """Interrupção que o serviço não pode transformar em resultado."""


class PublicationServiceFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.live = self.stack.enter_context(ExitStack())
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.quarantine = self.root / "quarantine"
        self.quarantine.mkdir(mode=0o700)
        self.policy_file = self.root / "policy.json"
        self.policy_file.write_text(json.dumps({"version": 1, "bindings": []}))
        self.policy = self.live.enter_context(
            observe_org_policy(OrgPolicyContext("file", self.policy_file), self.home)
        )
        self.name = "personal-skill"
        self.target = self.home / "skills" / self.name
        self.source_key = uuid.uuid4().hex

    def start(self, *, replacement=False):
        previous = None
        if replacement:
            self.target.mkdir(parents=True, mode=0o700)
            (self.target / "SKILL.md").write_bytes(b"previous version")
            previous = capture(self.target)
        context = self.live.enter_context(
            observe_quarantine_source(
                self.home,
                self.quarantine,
                source_key=self.source_key,
                actor=Actor.USER_FOREGROUND,
                policy=self.policy,
                replacement=previous,
            )
        )
        session = self.live.enter_context(
            publication_session(self.home, policy=self.policy, context=context)
        )
        receipt = SkillAcquisitionService(session).acquire(
            SkillBundle(self.name, {"SKILL.md": b"new version"})
        )
        self.session = session
        self.service = SkillPublicationService(session)
        self.receipt = receipt
        self.source = self.quarantine / receipt.acquisition_id / self.name
        self.previous = previous
        return session

    def reopen(self):
        self.live.close()
        self.policy = self.live.enter_context(
            observe_org_policy(OrgPolicyContext("file", self.policy_file), self.home)
        )
        context = self.live.enter_context(
            observe_quarantine_source(
                self.home,
                self.quarantine,
                source_key=self.source_key,
                actor=Actor.USER_FOREGROUND,
                policy=self.policy,
                replacement=self.previous,
            )
        )
        self.session = self.live.enter_context(
            publication_session(self.home, policy=self.policy, context=context)
        )
        self.service = SkillPublicationService(self.session)
        return self.session

    def protect(self):
        self.policy_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "bindings": [
                        {
                            "home": str(self.home),
                            "name": self.name,
                            "organization_id": "private-org-sentinel",
                            "active": False,
                        }
                    ],
                }
            )
        )

    def publish(self, **kwargs):
        return self.service.publish(self.name, acquisition_id=self.receipt.acquisition_id, **kwargs)

    def stop_after(self, object_, method):
        original = getattr(object_, method)

        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise Interrupted

        with patch.object(object_, method, side_effect=interrupt), self.assertRaises(Interrupted):
            self.publish()
        return self.session.publications.history(name=self.name)[-1]


class PublicationServiceTests(PublicationServiceFixture):
    def test_prepared_precedes_any_installed_move(self):
        session = self.start(replacement=True)
        original = session.files.retire

        def retire(name, operation_id, expected, *, guard):
            self.assertFalse(session.connection.in_transaction)
            record = session.publications.get(operation_id)
            self.assertIs(record.state, SkillMutationState.PREPARED)
            self.assertEqual(capture(self.target), self.previous)
            return original(name, operation_id, expected, guard=guard)

        with patch.object(session.files, "retire", side_effect=retire) as called:
            result = self.publish()
        called.assert_called_once()
        self.assertTrue(result.complete)
        self.assertFalse(self.source.exists())
        self.assertEqual(session.files.capture_previous(result.operation_id), self.previous)
        record = session.publications.get(result.operation_id)
        self.assertEqual(capture(self.target), record.draft.staged_tree)
        self.assertIs(record.state, SkillMutationState.COMMITTED)
        self.assertEqual(len(session.publications.history(name=self.name)), 1)

    def test_failed_publish_preserves_previous(self):
        session = self.start(replacement=True)
        with patch.object(session.files, "publish", side_effect=OSError("private error")):
            result = self.publish()
        self.assertFalse(result.complete)
        self.assertEqual(result.error_kind, "io")
        self.assertEqual(session.files.capture_previous(result.operation_id), self.previous)
        self.assertTrue(self.source.exists())
        self.assertIn(
            result.operation_id, {r.operation_id for r in session.publications.pending(self.name)}
        )

    def test_post_rename_policy_refusal_remains_pending(self):
        session = self.start(replacement=True)
        original = session.files.publish

        def publish(*args, **kwargs):
            original(*args, **kwargs)
            raise SkillMutationError("denied", "Proteção observada após rename.")

        with patch.object(session.files, "publish", side_effect=publish):
            result = self.publish()
        record = session.publications.get(result.operation_id)
        self.assertFalse(result.complete)
        self.assertIs(record.state, SkillMutationState.PREPARED)
        self.assertEqual(session.files.capture_previous(result.operation_id), self.previous)
        self.assertEqual(capture(self.target), record.draft.staged_tree)
        self.assertTrue(self.source.exists())

    def test_unknown_private_tree_is_never_cleaned(self):
        session = self.start()
        operation = uuid.uuid4()
        orphan = self.home / ".skill-publications" / operation.hex
        orphan.mkdir(mode=0o700)
        (orphan / "valuable").write_bytes(b"unknown version")
        with (
            patch("kairos_skills.publications.uuid.uuid4", return_value=operation),
            self.assertRaises(SkillMutationError),
        ):
            self.publish()
        self.assertEqual((orphan / "valuable").read_bytes(), b"unknown version")
        self.assertEqual(session.publications.history(name=self.name), ())
        with self.assertRaises(SkillMutationError):
            self.service.recover(operation.hex)
        self.assertTrue(orphan.exists())

    def test_commit_guard_detects_installed_edit_without_scanning_in_transaction(self):
        session = self.start()
        original = session.revalidate

        def mutate():
            if session.connection.in_transaction and self.target.exists():
                (self.target / "SKILL.md").write_bytes(b"concurrent edit")
            original()

        with patch.object(session, "revalidate", side_effect=mutate):
            result = self.publish()
        self.assertFalse(result.complete)
        self.assertIs(
            session.publications.get(result.operation_id).state, SkillMutationState.PREPARED
        )
        self.assertTrue(self.source.exists())

    def test_commit_error_after_effect_is_never_reported_complete(self):
        session = self.start()
        original = session.publications.finish

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise SkillMutationError("io", "Confirmação interrompida.")

        with patch.object(session.publications, "finish", side_effect=fail):
            result = self.publish()
        self.assertFalse(result.complete)
        self.assertIs(result.state, SkillMutationState.COMMITTED)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.service.recover(result.operation_id).complete)

    def test_pending_blocks_second_publish_before_staging(self):
        session = self.start()
        record = self.stop_after(session.publications, "prepare")
        with (
            self.assertLogs("kairos_skills.publications", level="WARNING") as logs,
            patch.object(session.files, "stage") as stage,
            self.assertRaises(SkillMutationError) as caught,
        ):
            self.publish()
        stage.assert_not_called()
        self.assertEqual(caught.exception.operation_id, record.operation_id)
        self.assertEqual(logs.records[-1].operation_id, record.operation_id)
        self.assertEqual(logs.records[-1].reason, "publication_validation_failed")

    def test_replacement_requires_exact_foreground_intent(self):
        session = self.start()
        self.target.mkdir(mode=0o700)
        (self.target / "SKILL.md").write_bytes(b"unapproved replacement")
        before = capture(self.target)
        with self.assertRaises(SkillMutationError):
            self.publish()
        self.assertEqual(capture(self.target), before)
        self.assertEqual(session.publications.history(name=self.name), ())

    def test_closed_session_has_no_effect(self):
        session = self.start()
        session._closed = True
        with self.assertRaises(SkillMutationError):
            self.publish()
        self.assertFalse(self.target.exists())

    def test_real_policy_change_after_rename_preserves_new_and_previous(self):
        from kairos_skills import publication_files

        session = self.start(replacement=True)
        original = publication_files.rename_no_replace

        def rename(parent, name, target, target_name):
            original(parent, name, target, target_name)
            if name == "staged":
                self.protect()

        with patch.object(publication_files, "rename_no_replace", side_effect=rename):
            result = self.publish()
        self.assertFalse(result.complete)
        record = session.publications.get(result.operation_id)
        self.assertIs(record.state, SkillMutationState.PREPARED)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"new version")
        self.assertTrue(self.source.exists())
        session = self.reopen()
        recovered = self.service.recover(result.operation_id)
        self.assertFalse(recovered.complete)
        self.assertEqual(session.files.capture_previous(record.operation_id), self.previous)
        self.assertEqual(capture(self.target), record.draft.staged_tree)

    def test_no_tree_scan_copy_or_moves_inside_sqlite_transaction(self):
        from kairos_skills import publication_files, publication_sources, removal_io

        session = self.start(replacement=True)
        with ExitStack() as stack:
            for owner, method in (
                (publication_files, "capture_source"),
                (publication_sources, "capture_source"),
                (removal_io, "capture_tree"),
                (publication_files, "rename_no_replace"),
            ):
                original = getattr(owner, method)

                def outside(*args, _original=original, **kwargs):
                    self.assertFalse(session.connection.in_transaction)
                    return _original(*args, **kwargs)

                stack.enter_context(patch.object(owner, method, side_effect=outside))
            self.assertTrue(self.publish().complete)

    def test_error_after_prepare_commit_keeps_canonical_pending_id(self):
        session = self.start(replacement=True)
        original = session.publications.prepare

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("private database path")

        with patch.object(session.publications, "prepare", side_effect=fail):
            result = self.publish()
        self.assertFalse(result.complete)
        self.assertEqual(capture(self.target), self.previous)
        self.assertIs(result.state, SkillMutationState.PREPARED)

    def test_error_after_real_fsync_keeps_both_versions(self):
        session = self.start(replacement=True)
        original = session.files.sync_parents

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("private fsync error")

        with patch.object(session.files, "sync_parents", side_effect=fail):
            result = self.publish()
        self.assertFalse(result.complete)
        self.assertIs(result.state, SkillMutationState.PREPARED)
        self.assertEqual(session.files.capture_previous(result.operation_id), self.previous)
        self.assertTrue(self.service.recover(result.operation_id).complete)

    def test_incomplete_manifest_never_claims_projection(self):
        from kairos_skills.publication_contract import ManifestDelta

        session = self.start()
        result = self.publish(manifest_delta=ManifestDelta(self.name, None, ""))
        self.assertIs(result.state, SkillMutationState.COMMITTED)
        self.assertFalse(result.complete)
        self.assertFalse(result.manifest_complete)
        self.assertTrue(self.source.exists())
        self.assertEqual(len(session.publications.pending(self.name)), 1)

    def test_installed_edit_before_cleanup_preserves_quarantine(self):
        session = self.start(replacement=True)
        original = session.context._release_source_for_cleanup

        def edit(*args, **kwargs):
            original(*args, **kwargs)
            (self.target / "SKILL.md").write_bytes(b"edited installed version")

        with patch.object(session.context, "_release_source_for_cleanup", side_effect=edit):
            result = self.publish()
        self.assertFalse(result.complete)
        self.assertTrue(self.source.exists())
        self.assertFalse(result.cleanup_complete)
