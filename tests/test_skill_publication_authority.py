"""Publicação renova instalação sem transportar autoria ou adoção anterior."""

import hashlib
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from skill_mutation_fixtures import creation_text
from skill_org_fixtures import personal_policy
from skill_publication_fixtures import PublicationStateFixture
from test_skill_publication_files import capture

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutations import SkillMutationService
from kairos_skills.publication_contract import PublicationWriter
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def home_contents(home):
    return {
        str(path.relative_to(home)): (
            path.lstat().st_dev,
            path.lstat().st_ino,
            # SHM muda com locks de leitores; conteúdo canônico continua no DB/WAL.
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file() and path.name != "state.db-shm"
            else None,
        )
        for path in (home, *sorted(home.rglob("*")))
    }


class PublicationAuthorityFixture(PublicationStateFixture):
    name = "personal-skill"

    def setUp(self):
        super().setUp()
        self.policy = personal_policy(self, self.home)
        self.common = SkillMutationRepository(self.connection, home_id=self.home_id)
        self.source = self.home / "input.md"
        self.source.write_text(creation_text(name=self.name, body="previous version"))

    def capture(self, path, text=None):
        if text is None:
            text = creation_text(name=self.name, body="published version").encode()
        return super().capture(path, text)

    def service(self):
        return SkillMutationService(self.home, self.common, policy=self.policy)

    def manual(self):
        return self.service().add(self.source, actor=Actor.USER_FOREGROUND)

    def prepare_publication(self, *, bundled=False, manifest=False):
        from kairos_skills.mutation_lock import skill_mutation_lock

        with skill_mutation_lock(self.home):
            pass
        self.upgrade()
        target = self.home / "skills" / self.name
        previous = capture(target) if target.exists() else None
        previous_installation = self.common.current_installation(self.name)
        receipt, draft = self.publication(manifest=manifest)
        if bundled:
            draft = replace(
                draft,
                writer=PublicationWriter.BUNDLED_SYNC,
                actor=None,
                source_key="package_bundle",
                source_ref=self.name,
            )
        else:
            self.upgrade().issue(receipt, guard=self.policy.revalidate)
        draft = replace(draft, previous_tree=previous, previous_installation=previous_installation)
        publications = self.upgrade()
        publications.prepare(draft, guard=self.policy.revalidate)
        return publications, draft

    def publish(self, *, bundled=False, complete=True, manifest=False):
        publications, draft = self.prepare_publication(bundled=bundled, manifest=manifest)
        target = self.home / "skills" / self.name
        target.parent.mkdir(exist_ok=True)
        area = self.home / ".skill-publications" / draft.operation_id
        if target.exists():
            target.rename(area / "previous")
        (area / "staged").rename(target)
        # T3 exercita projeção com journal e arquivos reais; o coordenador pertence a T4.
        publications.finish(
            draft.operation_id, SkillMutationState.COMMITTED, guard=self.policy.revalidate
        )
        if complete:
            publications.confirm_manifest(draft.operation_id, guard=self.policy.revalidate)
            publications.confirm_cleanup(draft.operation_id, guard=self.policy.revalidate)
        return publications, draft


class PublicationAuthorityTests(PublicationAuthorityFixture):
    def test_identical_bytes_do_not_transfer_manual_creation_authority(self):
        self.source.write_text(creation_text(name=self.name, body="published version"))
        created = self.manual()
        original = (self.home / "skills" / self.name / "SKILL.md").read_bytes()
        _publications, draft = self.publish()
        self.assertEqual((self.home / "skills" / self.name / "SKILL.md").read_bytes(), original)
        evidence = self.common.installation_evidence(self.name)
        self.assertEqual(evidence.current.operation_id, draft.operation_id)
        self.assertNotEqual(evidence.current.operation_id, created.operation_id)
        self.assertIsNone(evidence.historical_creation_id)
        self.assertIsNone(evidence.current.provenance)
        self.assertEqual(self.common.get(created.operation_id), created)

    def test_publication_clears_old_adoption(self):
        self.capture(self.home / "skills" / self.name, b"legacy version")
        adoption = SkillAdoptionService(self.home, self.common, policy=self.policy).adopt(
            self.name, actor=Actor.USER_FOREGROUND, confirmed=True
        )
        before = self.common.installation_evidence(self.name)
        self.assertIsNotNone(before.adoption)
        _publications, draft = self.publish()
        after = self.common.installation_evidence(self.name)
        self.assertNotEqual(after.current.operation_id, before.current.operation_id)
        self.assertEqual(after.current.operation_id, draft.operation_id)
        self.assertIsNone(after.adoption)
        self.assertIsNone(after.current.provenance)
        self.assertIsNotNone(adoption)

    def test_unproven_acquisition_never_becomes_user(self):
        _publications, draft = self.publish()
        report = identify_skill(self.home, self.name, policy=self.policy)
        self.assertEqual(report.current_installation_id, draft.operation_id)
        self.assertEqual(report.installation_source, "verified_acquisition")
        self.assertEqual(report.ownership, "unknown")
        self.assertEqual(report.authorization_basis, "none")
        self.assertFalse(report.removivel)
        with (
            self.assertLogs("kairos_skills.mutations", level="WARNING") as logs,
            self.assertRaises(SkillMutationError),
        ):
            self.service().ownership(self.name)
        self.assertEqual(logs.records[-1].kind, "denied")
        self.assertEqual(logs.records[-1].operation_id, draft.operation_id)
        self.assertNotIn(str(self.home), repr(logs.records[-1].__dict__))
        before = home_contents(self.home)
        with self.assertRaises(SkillMutationError):
            self.service().remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(home_contents(self.home), before)

    def test_trusted_bundled_publication_has_bundled_provenance(self):
        _publications, draft = self.publish(bundled=True)
        current = self.common.current_installation(self.name)
        self.assertEqual(current.operation_id, draft.operation_id)
        self.assertIs(current.provenance, Provenance.BUNDLED)
        report = identify_skill(self.home, self.name, policy=self.policy)
        self.assertEqual(report.installation_source, "package_bundle")
        self.assertTrue(report.removivel)

    def test_replaced_acquisition_root_does_not_inherit_source_or_authority(self):
        self.publish()
        target = self.home / "skills" / self.name
        previous = target.with_name("preserved-original")
        target.rename(previous)
        self.capture(target, (previous / "SKILL.md").read_bytes())
        report = identify_skill(self.home, self.name, policy=self.policy)
        self.assertFalse(report.identity_valid)
        self.assertEqual(report.installation_source, "unknown")
        self.assertEqual(report.ownership, "unknown")
        self.assertEqual(report.authorization_basis, "none")
        self.assertFalse(report.removivel)

    def test_manifest_maintenance_preserves_installation_and_explicit_adoption(self):
        from kairos_skills.publication_contract import ManifestDelta

        publications, draft = self.publish(manifest=True)
        SkillAdoptionService(self.home, self.common, policy=self.policy).adopt(
            self.name, actor=Actor.USER_FOREGROUND, confirmed=True
        )
        before = self.common.installation_evidence(self.name)
        publications.prepare_manifest(
            draft.operation_id,
            ManifestDelta(self.name, "a" * 32, None),
            guard=self.policy.revalidate,
        )
        pending = self.common.installation_evidence(self.name)
        self.assertEqual(pending.current, before.current)
        self.assertEqual(pending.adoption, before.adoption)
        self.assertIn(draft.operation_id, pending.pending_operation_ids)
        publications.confirm_manifest(draft.operation_id, guard=self.policy.revalidate)
        self.assertEqual(self.common.installation_evidence(self.name), before)

    def test_confirmation_does_not_reapply_publication(self):
        publications, draft = self.publish(complete=False, manifest=True)
        before = self.common.current_installation(self.name)
        self.assertIn(
            draft.operation_id, self.common.installation_evidence(self.name).pending_operation_ids
        )
        publications.confirm_manifest(draft.operation_id, guard=self.policy.revalidate)
        publications.confirm_cleanup(draft.operation_id, guard=self.policy.revalidate)
        self.assertEqual(self.common.current_installation(self.name), before)
        adopted = SkillAdoptionService(self.home, self.common, policy=self.policy).adopt(
            self.name, actor=Actor.USER_FOREGROUND, confirmed=True
        )
        self.assertIsNotNone(adopted)
        adoption = self.common.installation_evidence(self.name).adoption
        self.assertIsNotNone(adoption)
        for _ in range(2):
            publications.confirm_cleanup(draft.operation_id, guard=self.policy.revalidate)
        self.assertEqual(self.common.installation_evidence(self.name).adoption, adoption)

    def test_new_v11_tree_operations_remain_readable_and_restorable(self):
        self.publish(bundled=True)
        removed = self.service().remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(self.common.get(removed.operation_id), removed)
        restored = self.service().rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(
            self.common.current_installation(self.name).operation_id, restored.operation_id
        )
        self.assertEqual(self.common.get(restored.operation_id), restored)

    def test_explicit_adoption_after_publication_can_be_revoked(self):
        self.publish()
        service = SkillAdoptionService(self.home, self.common, policy=self.policy)
        adopted = service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(adopted.identification.authorization_basis, "user_adoption")
        revoked = service.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(self.common.get(revoked.operation_id), revoked)
        report = identify_skill(self.home, self.name, policy=self.policy)
        self.assertFalse(report.removivel)
        self.assertEqual(report.authorization_basis, "none")
        self.assertEqual(report.installation_source, "verified_acquisition")

    def test_history_serializes_unknown_publication_without_user_fallback(self):
        from kairos_cli.main import main

        _publications, draft = self.publish(complete=False)
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"KAIROS_HOME": str(self.home)}),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = main(["skills", "history", "--json"])
        self.assertEqual(code, 0, err.getvalue())
        publication = next(
            row for row in json.loads(out.getvalue()) if row["id"] == draft.operation_id
        )
        self.assertIsNone(publication["origin"])
        self.assertEqual(publication["writer"], "quarantine_promote")
        self.assertTrue(publication["pending"])
        self.assertFalse(publication["cleanup_complete"])
        self.assertNotIn(str(self.home), out.getvalue())

    def test_legacy_reader_has_no_home_effects(self):
        from kairos_skills.catalog import catalog_home_id
        from kairos_state import connect
        from kairos_state.migrations import migrate

        for version in (7, 8, 9, 10):
            with self.subTest(version=version):
                home = self.home / f"legacy-{version}"
                home.mkdir()
                connection = connect(home / "state.db")
                migrate(connection, target=version)
                connection.close()
                self.capture(home / "skills" / self.name)
                policy = personal_policy(self, home)
                before = home_contents(home)
                report = identify_skill(home, self.name, policy=policy)
                self.assertEqual(report.installation_source, "unknown")
                self.assertEqual(home_contents(home), before)
                self.assertNotEqual(catalog_home_id(home), self.home_id)

    def test_session_catalog_remains_immutable_after_publication(self):
        from kairos_skills.catalog import catalog_asset
        from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset
        from kairos_state.repositories import SessionRepository
        from kairos_state.repositories.skill_catalogs import SkillCatalogRepository

        self.manual()
        sessions = SessionRepository(self.connection)
        sessions.ensure("before", source="cli")
        catalogs = SkillCatalogRepository(self.connection)
        before = capture_skill_catalog(self.home)
        asset = catalog_asset(before, self.name)
        content = read_catalog_asset(self.home, asset)
        catalogs.create_if_absent("before", before)
        catalogs.put_asset("before", asset, content)
        self.publish()
        self.assertEqual(catalogs.get("before", home_id=self.home_id), before)
        self.assertEqual(catalogs.get_asset("before", asset), content)
        after = capture_skill_catalog(self.home)
        self.assertNotEqual(after.digest, before.digest)
        self.assertNotEqual(read_catalog_asset(self.home, catalog_asset(after, self.name)), content)
