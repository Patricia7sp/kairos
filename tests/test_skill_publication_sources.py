"""Aquisição e fonte viva recusam recibos soltos, colisões e árvores trocadas."""

import os
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from test_skill_publication_files import capture

from kairos_domain.ownership import Actor
from kairos_skills.hub import QuarantineError, SkillBundle, quarantine_bundle
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext


class PublicationSourcesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.quarantine = self.root / "quarantine"
        self.quarantine.mkdir(mode=0o700)
        self.policy = self.stack.enter_context(
            observe_org_policy(OrgPolicyContext("none", None), self.home)
        )
        self.source_key = uuid.uuid4().hex
        self.bundle = SkillBundle("personal-skill", {"SKILL.md": b"new version"})

    def context(self, *, home=None, policy=None):
        from kairos_skills.publication_sources import observe_quarantine_source

        return self.stack.enter_context(
            observe_quarantine_source(
                home or self.home,
                self.quarantine,
                source_key=self.source_key,
                actor=Actor.USER_FOREGROUND,
                policy=policy or self.policy,
            )
        )

    def session(self, context):
        from kairos_skills.publication_session import publication_session

        return self.stack.enter_context(
            publication_session(self.home, policy=self.policy, context=context)
        )

    def acquire(self):
        from kairos_skills.acquisitions import SkillAcquisitionService

        context = self.context()
        session = self.session(context)
        receipt = SkillAcquisitionService(session).acquire(self.bundle)
        return context, session, receipt

    def test_receipt_requires_real_acquisition(self):
        context, session, receipt = self.acquire()
        path, proof = context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)
        self.assertEqual(session.publications.receipt(receipt.acquisition_id), receipt)
        self.assertEqual(capture(path), proof)
        self.assertEqual(proof, receipt.source_proof)
        self.assertEqual(receipt.source_category, "unverified")
        self.assertFalse(session.connection.in_transaction)
        unknown = uuid.uuid4().hex
        quarantine_bundle(self.bundle, self.quarantine / unknown)
        with self.assertRaises(SkillMutationError):
            context.resolve(self.bundle.name, acquisition_id=unknown)
        self.assertIsNone(session.publications.receipt(unknown))

    def test_acquired_source_on_other_device_keeps_receipt_and_identity(self):
        from kairos_skills.publication_contract import publication_inventory_hash

        external_root = os.environ.get("KAIROS_PUBLICATION_CROSS_DEVICE_ROOT")
        if not external_root:
            self.skipTest("Requer quarentena temporária em outro device; repetir no aceite T7.")
        self.quarantine = Path(
            self.stack.enter_context(tempfile.TemporaryDirectory(dir=external_root))
        )
        context, session, receipt = self.acquire()
        source, proof = context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)
        self.assertNotEqual(source.stat().st_dev, self.home.stat().st_dev)
        staged = session.files.stage(uuid.uuid4().hex, source, proof, guard=session.revalidate)
        self.assertNotEqual(staged.entries[0].device, proof.entries[0].device)
        self.assertEqual(publication_inventory_hash(staged), receipt.inventory_sha256)
        self.assertEqual(capture(source), proof)
        self.assertEqual(session.publications.receipt(receipt.acquisition_id), receipt)

    def test_identical_bytes_replaced_root_refuses(self):
        context = self.context()
        self.quarantine.rename(self.root / "original")
        self.quarantine.mkdir(mode=0o700)
        with self.assertRaises(SkillMutationError):
            context.revalidate()

    def test_stale_scan_refuses(self):
        context, session, receipt = self.acquire()
        path = self.quarantine / receipt.acquisition_id / receipt.name
        (path / "SKILL.md").write_bytes(b"different bytes")
        before = capture(path)
        with self.assertRaises(SkillMutationError):
            context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)
        self.assertEqual(capture(path), before)
        self.assertEqual(session.publications.receipt(receipt.acquisition_id), receipt)

    def test_receipt_does_not_follow_tree_into_another_quarantine_root(self):
        from kairos_skills.publication_sources import observe_quarantine_source

        context, _session, receipt = self.acquire()
        context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)
        other = self.root / "other-quarantine"
        other.mkdir(mode=0o700)
        (self.quarantine / receipt.acquisition_id).rename(other / receipt.acquisition_id)
        with (
            observe_quarantine_source(
                self.home,
                other,
                source_key=self.source_key,
                actor=Actor.USER_FOREGROUND,
                policy=self.policy,
            ) as other_context,
            self.assertRaises(SkillMutationError),
        ):
            other_context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)

    def test_changed_bytes_during_scan_never_issue_receipt(self):
        from kairos_skills import acquisitions
        from kairos_skills.acquisitions import SkillAcquisitionService
        from kairos_skills.hub import scan_quarantined

        session = self.session(self.context())

        def change_during_scan(path):
            findings = scan_quarantined(path)
            (path / "SKILL.md").write_bytes(b"altered after capture")
            return findings

        with (
            patch.object(acquisitions, "scan_quarantined", side_effect=change_during_scan),
            self.assertRaises(SkillMutationError),
        ):
            SkillAcquisitionService(session).acquire(self.bundle)
        self.assertEqual(
            session.connection.execute(
                "SELECT COUNT(*) FROM skill_acquisition_receipts"
            ).fetchone()[0],
            0,
        )

    def test_context_authority_cannot_be_reassigned(self):
        from kairos_skills.publication_contract import PublicationWriter

        context = self.context()
        with self.assertRaises(AttributeError):
            context.writer = PublicationWriter.BUNDLED_SYNC

    def test_changed_acquisition_at_commit_refuses_receipt(self):
        from kairos_skills.acquisitions import SkillAcquisitionService

        context = self.context()
        session = self.session(context)
        original = session.revalidate

        def change_before_commit():
            if session.connection.in_transaction:
                skill = next(self.quarantine.glob("*/personal-skill/SKILL.md"))
                skill.write_bytes(b"changed at commit")
            original()

        with (
            patch.object(session, "revalidate", side_effect=change_before_commit),
            self.assertRaises(SkillMutationError),
        ):
            SkillAcquisitionService(session).acquire(self.bundle)
        self.assertEqual(
            session.connection.execute(
                "SELECT COUNT(*) FROM skill_acquisition_receipts"
            ).fetchone()[0],
            0,
        )

    def test_bundled_source_uses_package_composition_and_pins_tree(self):
        from kairos_skills.publication_sources import observe_bundled_source

        bundle_root = self.root / "package-skills"
        quarantine_bundle(self.bundle, bundle_root / "category")
        with (
            patch(
                "kairos_skills.publication_sources.trusted_bundle_root", return_value=bundle_root
            ),
            observe_bundled_source(self.home, policy=self.policy) as context,
        ):
            self.assertTrue(context.contains(self.bundle.name))
            path, _proof = context.resolve(self.bundle.name)
            (path / "SKILL.md").write_bytes(b"new package bytes")
            with self.assertRaises(SkillMutationError):
                context.revalidate()

    def test_identical_acquisition_copy_does_not_inherit_receipt(self):
        context, _session, receipt = self.acquire()
        path = self.quarantine / receipt.acquisition_id / receipt.name
        path.rename(path.with_name("preserved"))
        quarantine_bundle(self.bundle, path.parent)
        with self.assertRaises(SkillMutationError):
            context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)

    def test_acquisition_collision_preserves_tree_and_receipt(self):
        from kairos_skills.acquisitions import SkillAcquisitionService

        context, session, receipt = self.acquire()
        path, before = context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)
        with (
            patch(
                "kairos_skills.acquisitions.uuid.uuid4",
                return_value=uuid.UUID(hex=receipt.acquisition_id),
            ),
            self.assertRaises(SkillMutationError) as caught,
        ):
            SkillAcquisitionService(session).acquire(self.bundle)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(capture(path), before)
        self.assertEqual(session.publications.receipt(receipt.acquisition_id), receipt)

    def test_legacy_quarantine_collision_preserves_existing_acquisition(self):
        source = quarantine_bundle(self.bundle, self.quarantine)
        before = capture(source)
        with self.assertRaises(QuarantineError):
            quarantine_bundle(
                SkillBundle(self.bundle.name, {"SKILL.md": b"changed"}), self.quarantine
            )
        self.assertEqual(capture(source), before)

    def test_legacy_collision_preserves_canonical_receipt(self):
        context, session, receipt = self.acquire()
        source, before = context.resolve(receipt.name, acquisition_id=receipt.acquisition_id)
        with self.assertRaises(QuarantineError):
            quarantine_bundle(self.bundle, source.parent)
        self.assertEqual(capture(source), before)
        self.assertEqual(session.publications.receipt(receipt.acquisition_id), receipt)

    def test_arbitrary_bundle_root_has_no_package_authority(self):
        from kairos_skills.publication_contract import PublicationWriter

        context = self.context()
        self.assertEqual(context.writer, PublicationWriter.QUARANTINE_PROMOTE)
        quarantine_bundle(
            SkillBundle("personal-skill", self.bundle.files, trust_level="bundled"), self.quarantine
        )
        with self.assertRaises(SkillMutationError):
            context.resolve(self.bundle.name, acquisition_id=uuid.uuid4().hex)

    def test_source_requires_foreground_and_matching_home_policy(self):
        from kairos_skills.publication_sources import observe_quarantine_source

        with (
            self.assertRaises(SkillMutationError),
            observe_quarantine_source(
                self.home,
                self.quarantine,
                source_key=self.source_key,
                actor=None,
                policy=self.policy,
            ),
        ):
            self.fail("contexto sem chamador aceito")
        other = self.root / "other"
        other.mkdir()
        with self.assertRaises(SkillMutationError):
            self.context(home=other)

    def test_closed_session_refuses_acquisition(self):
        from kairos_skills.acquisitions import SkillAcquisitionService
        from kairos_skills.publication_session import publication_session

        context = self.context()
        with publication_session(self.home, policy=self.policy, context=context) as session:
            pass
        with self.assertRaises(SkillMutationError):
            SkillAcquisitionService(session).acquire(self.bundle)

    def test_replaced_lock_refuses_before_acquisition(self):
        from kairos_skills.acquisitions import SkillAcquisitionService
        from kairos_skills.publication_session import publication_session

        context = self.context()
        refused = False
        with (
            self.assertRaises(SkillMutationError),
            publication_session(self.home, policy=self.policy, context=context) as session,
        ):
            lock = self.home / ".skills-write.lock"
            lock.rename(self.home / ".old-lock")
            lock.write_bytes(b"")
            try:
                SkillAcquisitionService(session).acquire(self.bundle)
            except SkillMutationError:
                refused = True
                raise
            finally:
                self.assertEqual(tuple(self.quarantine.iterdir()), ())
        self.assertTrue(refused)

    def test_unsafe_bundle_refuses_before_creating_acquisition(self):
        from kairos_skills.acquisitions import SkillAcquisitionService

        session = self.session(self.context())
        before = tuple(self.quarantine.iterdir())
        for files in ({"../escape": b"x"}, {"SKILL.md": b"x" * 65537}, {"data": b"no skill"}):
            with self.subTest(files=tuple(files)), self.assertRaises(SkillMutationError):
                SkillAcquisitionService(session).acquire(SkillBundle("personal-skill", files))
        self.assertEqual(tuple(self.quarantine.iterdir()), before)

    def test_acquisition_refusal_logs_sanitized_context(self):
        from kairos_skills.acquisitions import SkillAcquisitionService

        session = self.session(self.context())
        private_source = "private-provider-reference"
        with (
            self.assertLogs("kairos_skills.acquisitions", level="WARNING") as logs,
            self.assertRaises(SkillMutationError),
        ):
            SkillAcquisitionService(session).acquire(
                SkillBundle("personal-skill", {"data": b"missing skill"}, source=private_source)
            )
        record = logs.records[-1]
        self.assertEqual(record.kind, "input")
        self.assertEqual(record.action, "acquire")
        self.assertEqual(uuid.UUID(hex=record.acquisition_id).version, 4)
        serialized = repr(record.__dict__)
        self.assertNotIn(private_source, serialized)
        self.assertNotIn(str(self.root), serialized)
