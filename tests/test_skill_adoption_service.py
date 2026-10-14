"""Adoção foreground e revogação duráveis sem alterar a instalação."""

import shutil
import sqlite3
import stat
import tempfile
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_skills.adoption_contract import SkillAdoptionAction, SkillAdoptionDraft
from kairos_skills.catalog import catalog_home_id
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import (
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationState,
    validate_skill_creation,
)
from kairos_skills.mutation_io import SkillMutationFiles
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_skills.removal_io import SkillRemovalFiles
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_adoptions import SkillAdoptionRepository
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def adoption_service_type():
    from kairos_skills.adoptions import SkillAdoptionService

    return SkillAdoptionService


class SkillAdoptionServiceTests(unittest.TestCase):
    name = "revisar-docs"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.database = None
        self.repository = None
        self.target = self.home / "skills" / self.name

    def ledger(self):
        self.home.mkdir(parents=True, exist_ok=True)
        if self.database is None:
            self.database = connect(self.home / "state.db")
            self.addCleanup(self.database.close)
            migrate(self.database)
            self.repository = SkillMutationRepository(
                self.database, home_id=catalog_home_id(self.home)
            )
        return self.repository

    def ledger_for(self, home):
        home.mkdir(parents=True, exist_ok=True)
        database = connect(home / "state.db")
        self.addCleanup(database.close)
        migrate(database)
        return SkillMutationRepository(database, home_id=catalog_home_id(home))

    def service(self):
        return adoption_service_type()(self.home, self.ledger())

    def legacy(self, *, name=None):
        target = self.home / "skills" / (name or self.name)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(b"\x00\xfflegacy without semver\n")
        scripts = target / "scripts"
        scripts.mkdir()
        executable = scripts / "run.bin"
        executable.write_bytes(b"\x00\xff\x01")
        executable.chmod(0o751)
        (target / "empty").mkdir(mode=0o710)
        return target

    def inventory(self, target=None):
        target = self.target if target is None else target
        result = {}
        for path in (target, *sorted(target.rglob("*"))):
            info = path.lstat()
            result[str(path.relative_to(target))] = (
                stat.S_IMODE(info.st_mode),
                info.st_dev,
                info.st_ino,
                info.st_nlink,
                path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
            )
        return result

    def adopt_record(self):
        result = self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIsNotNone(result.record)
        return result.record

    def test_denied_before_home_database_or_lock_access(self):
        repository = _empty_repository(self.home)
        self.addCleanup(repository._conn.close)
        service = adoption_service_type()(self.home, repository)
        denied = (
            (lambda: service.adopt(self.name, actor=Actor.CURATOR, confirmed=True), "denied"),
            (
                lambda: service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=False),
                "denied",
            ),
            (
                lambda: service.unadopt("../escape", actor=Actor.USER_FOREGROUND, confirmed=True),
                "input",
            ),
            (
                lambda: service.unadopt(
                    self.name,
                    actor=Actor.USER_FOREGROUND,
                    confirmed=True,
                    expect_adoption="invalid",
                ),
                "input",
            ),
        )
        for operation, kind in denied:
            with self.subTest(kind=kind), self.assertRaises(SkillMutationError) as caught:
                operation()
            self.assertEqual(caught.exception.kind, kind)
        self.assertFalse(self.home.exists())
        self.assertEqual(repository._conn.execute("SELECT 1").fetchone(), (1,))

    def test_adopt_preserves_bytes_modes_and_unknown_creation(self):
        self.legacy()
        before = self.inventory()

        result = self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertIsNotNone(result.record)
        self.assertIs(result.record.state, SkillMutationState.COMMITTED)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(
            self.repository.snapshot(result.record.operation_id).contents["SKILL.md"],
            b"\x00\xfflegacy without semver\n",
        )
        entries = {
            entry.path: entry
            for entry in self.repository.snapshot(result.record.operation_id).entries
        }
        self.assertEqual(entries["scripts/run.bin"].mode, 0o751)
        self.assertEqual(entries["empty"].mode, 0o710)
        self.assertEqual(result.identification.authorization_basis, "user_adoption")
        self.assertEqual(result.identification.historical_creation_origin, "unknown")
        self.assertIsNone(result.identification.historical_creation_id)
        self.assertEqual(result.identification.adoption_id, result.record.operation_id)
        self.assertTrue(result.identification.adoption_active)

    def test_repeat_returns_A_without_new_activation(self):
        self.legacy()
        first = self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        history = self.repository.history(name=self.name)

        repeated = self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(repeated.record, first.record)
        self.assertEqual(self.repository.history(name=self.name), history)
        self.assertEqual(repeated.identification.adoption_id, first.record.operation_id)

    def test_independent_USER_BUNDLED_unchanged(self):
        self.ledger()
        source = self.root / "source.md"
        source.write_text(
            "---\nname: revisar-docs\ndescription: Legado.\nversion: '0.1.0'\n---\nCorpo.\n"
        )
        created = SkillMutationService(self.home, self.repository).add(
            source, actor=Actor.USER_FOREGROUND
        )
        before = self.repository.history(name=self.name)
        user = self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertIsNone(user.record)
        self.assertEqual(self.repository.history(name=self.name), before)
        self.assertEqual(user.identification.authorization_basis, "proven_write")
        self.assertEqual(user.identification.current_installation_id, created.operation_id)

        bundled_home = self.root / "bundled-home"
        bundled = bundled_home / "skills" / self.name
        bundled.parent.mkdir(parents=True)
        shutil.copytree(self.target, bundled)
        bundle = self.root / "bundle"
        shutil.copytree(bundled, bundle / self.name)
        (bundled.parent / ".bundled_manifest").write_text(self.name + ":" + "0" * 32 + "\n")
        package_repository = self.ledger_for(bundled_home)
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle):
            package = adoption_service_type()(bundled_home, package_repository).adopt(
                self.name, actor=Actor.USER_FOREGROUND, confirmed=True
            )
        self.assertIsNone(package.record)
        self.assertEqual(package.identification.authorization_basis, "verified_bundle")
        self.assertEqual(package.identification.ownership, Provenance.BUNDLED.value)

    def test_direct_calls_enforce_guards(self):
        self.legacy()
        service = self.service()
        for operation in (
            lambda: service.adopt("_org", actor=Actor.USER_FOREGROUND, confirmed=True),
            lambda: service.adopt(self.name, actor=Actor.CURATOR, confirmed=True),
            lambda: service.unadopt(self.name, actor=Actor.BACKGROUND_REVIEW, confirmed=True),
            lambda: service.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=False),
        ):
            with self.subTest(operation=operation), self.assertRaises(SkillMutationError):
                operation()
        self.assertEqual(self.repository.history(name=self.name), ())

    def test_internal_edit_vs_replaced_root(self):
        self.legacy()
        service = self.service()
        first = service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True).record
        (self.target / "SKILL.md").write_bytes(b"Internal edit, no frontmatter\n")
        identified = identify_skill(self.home, self.name)
        self.assertEqual(identified.authorization_basis, "user_adoption")
        self.assertTrue(identified.adoption_active)
        self.assertEqual(identified.adoption_id, first.operation_id)

        self.target.rename(self.root / "previous")
        shutil.copytree(self.root / "previous", self.target)
        replaced = identify_skill(self.home, self.name)
        self.assertEqual(replaced.authorization_basis, "none")
        self.assertFalse(replaced.adoption_active)
        second = service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True).record
        self.assertNotEqual(second.operation_id, first.operation_id)
        self.assertEqual(second.draft.installation_id, second.operation_id)
        self.assertEqual(identify_skill(self.home, self.name).adoption_id, second.operation_id)

    def test_replaced_root_preserves_recorded_source_without_authority(self):
        self.legacy()
        repository = self.ledger()
        operation_id = uuid.uuid4().hex
        with skill_mutation_lock(self.home), SkillRemovalFiles(self.home) as files:
            captured = files.capture_installed(self.name)
            root = captured.entries[0]
            draft = SkillAdoptionDraft(
                operation_id=operation_id,
                home_id=repository.home_id,
                action=SkillAdoptionAction.ADOPT,
                name=self.name,
                actor=Actor.USER_FOREGROUND,
                created_at=time.time(),
                adoption_id=operation_id,
                installation_id=operation_id,
                device=root.device,
                inode=root.inode,
                installation_source="verified_acquisition",
                snapshot_id=operation_id,
            )
            SkillAdoptionRepository(self.database, home_id=repository.home_id).prepare_adopt(
                draft, captured
            )
            founder = repository.finish(operation_id, SkillMutationState.COMMITTED)

        initial = identify_skill(self.home, self.name)
        self.assertEqual(initial.installation_source, "verified_acquisition")
        self.assertTrue(initial.adoption_active)
        self.target.rename(self.root / "previous")
        shutil.copytree(self.root / "previous", self.target)
        replaced = identify_skill(self.home, self.name)
        self.assertEqual(replaced.installation_source, "verified_acquisition")
        self.assertFalse(replaced.identity_valid)
        self.assertFalse(replaced.adoption_active)
        self.assertFalse(replaced.removivel)
        self.assertEqual(replaced.authorization_basis, "none")
        self.assertEqual(replaced.adoption_id, operation_id)
        self.assertEqual(repository.get(operation_id), founder)

    def test_identify_rechecks_root_after_founder_read(self):
        self.legacy()
        adopted = self.adopt_record()
        from kairos_skills.identification import _read_adoption_record

        def replace_root_after_read(repository, adoption_id):
            record = _read_adoption_record(repository, adoption_id)
            self.assertEqual(adoption_id, adopted.operation_id)
            self.target.rename(self.root / "before-read-race")
            shutil.copytree(self.root / "before-read-race", self.target)
            return record

        with (
            patch("kairos_skills.identification._read_adoption_record", replace_root_after_read),
            self.assertRaises(SkillMutationError) as caught,
        ):
            identify_skill(self.home, self.name)

        self.assertEqual(caught.exception.kind, "conflict", str(caught.exception))

    def test_unadopt_preserves_every_file_and_snapshot(self):
        self.legacy()
        adopted = self.adopt_record()
        before_files = self.inventory()
        snapshot = self.repository.snapshot(adopted.operation_id)

        revoked = self.service().unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertIs(revoked.state, SkillMutationState.COMMITTED)
        self.assertEqual(self.inventory(), before_files)
        self.assertEqual(self.repository.snapshot(adopted.operation_id), snapshot)
        identification = identify_skill(self.home, self.name)
        self.assertEqual(identification.current_installation_id, adopted.draft.installation_id)
        self.assertEqual(identification.adoption_id, adopted.operation_id)
        self.assertFalse(identification.adoption_active)
        self.assertEqual(identification.authorization_basis, "none")
        self.assertFalse(identification.removivel)

    def test_restore_transports_A_and_identify_repeat_and_unadopt_follow_R(self):
        self.legacy()
        founder = self.adopt_record()

        with skill_mutation_lock(self.home), SkillRemovalFiles(self.home) as files:
            captured = files.capture_installed(self.name)
            current = self.repository.current_installation(self.name)
            remove_id = uuid.uuid4().hex
            removal_draft = SkillTreeDraft(
                remove_id,
                self.repository.home_id,
                SkillTreeAction.REMOVE,
                self.name,
                Actor.USER_FOREGROUND,
                time.time(),
                current.provenance,
                TreeCapture(captured.entries, None),
                remove_id,
                adoption=current.adoption,
                installation_source=founder.draft.installation_source,
            )
            removed = self.repository.prepare_remove(removal_draft, captured)
            files.retire(self.name, remove_id)
            files.sync_parents()
            removed = self.repository.finish(remove_id, SkillMutationState.COMMITTED)
            self.assertIs(removed.state, SkillMutationState.COMMITTED)

            restore_id = uuid.uuid4().hex
            staged = files.stage(restore_id, self.repository.snapshot(remove_id))
            restore_draft = replace(
                removal_draft,
                operation_id=restore_id,
                action=SkillTreeAction.RESTORE,
                proof=TreeCapture(staged.entries, None),
                reverts=remove_id,
                adoption=replace(removal_draft.adoption, installation_id=restore_id),
            )
            self.repository.prepare_restore(restore_draft)
            files.publish(restore_id, self.name)
            files.sync_parents()
            restored = self.repository.finish(restore_id, SkillMutationState.COMMITTED)

        self.assertIs(restored.state, SkillMutationState.COMMITTED)
        evidence = self.repository.installation_evidence(self.name)
        self.assertEqual(evidence.adoption.adoption_id, founder.operation_id)
        self.assertEqual(evidence.adoption.installation_id, restored.operation_id)
        self.assertTrue(evidence.adoption.active)

        identified = identify_skill(self.home, self.name)
        self.assertEqual(identified.adoption_id, founder.operation_id)
        self.assertEqual(identified.current_installation_id, restored.operation_id)
        self.assertTrue(identified.adoption_active)
        self.assertEqual(identified.authorization_basis, "user_adoption")

        service = self.service()
        before_repeat = self.repository.history(name=self.name)
        repeated = service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(repeated.record, founder)
        self.assertEqual(repeated.identification.current_installation_id, restored.operation_id)
        self.assertEqual(self.repository.history(name=self.name), before_repeat)

        revoked = service.unadopt(
            self.name,
            actor=Actor.USER_FOREGROUND,
            confirmed=True,
            expect_adoption=founder.operation_id,
        )
        self.assertIs(revoked.action, SkillAdoptionAction.UNADOPT)
        self.assertIs(revoked.state, SkillMutationState.COMMITTED)
        after_unadopt = identify_skill(self.home, self.name)
        self.assertEqual(after_unadopt.adoption_id, founder.operation_id)
        self.assertEqual(after_unadopt.current_installation_id, restored.operation_id)
        self.assertFalse(after_unadopt.adoption_active)
        self.assertEqual(after_unadopt.authorization_basis, "none")

    def test_expected_A_cannot_revoke_B(self):
        self.legacy()
        service = self.service()
        first = service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True).record
        service.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        second = service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True).record
        before = self.repository.history(name=self.name)
        self.assertEqual(second.draft.installation_id, first.draft.installation_id)
        self.assertEqual(second.draft.installation_source, first.draft.installation_source)

        with self.assertRaises(SkillMutationError) as caught:
            service.unadopt(
                self.name,
                actor=Actor.USER_FOREGROUND,
                confirmed=True,
                expect_adoption=first.operation_id,
            )

        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(self.repository.history(name=self.name), before)
        self.assertEqual(identify_skill(self.home, self.name).adoption_id, second.operation_id)
        self.assertTrue(identify_skill(self.home, self.name).adoption_active)

    def test_change_before_commit_refused_without_expectation(self):
        self.legacy()
        service = self.service()
        adopted = service.adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True).record
        commit = SkillAdoptionRepository.commit_unadopt

        def race_at_persistence(repository, draft, *, revalidate=None):
            (self.target / "SKILL.md").write_bytes(b"Concurrent edit\n")
            return commit(repository, draft, revalidate=revalidate)

        with (
            patch.object(SkillAdoptionRepository, "commit_unadopt", race_at_persistence),
            self.assertRaises(SkillMutationError) as caught,
        ):
            service.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(self.repository.history(name=self.name), (adopted,))
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"Concurrent edit\n")
        self.assertTrue(identify_skill(self.home, self.name).adoption_active)

    def test_unsafe_tree_after_prepare_becomes_conflict(self):
        self.legacy()
        external = self.root / "external.md"
        external.write_bytes(b"outside the captured tree\n")
        prepare = SkillAdoptionRepository.prepare_adopt

        def inject_symlink_after_prepare(repository, draft, snapshot):
            record = prepare(repository, draft, snapshot)
            (self.target / "SKILL.md").unlink()
            (self.target / "SKILL.md").symlink_to(external)
            return record

        with (
            patch.object(SkillAdoptionRepository, "prepare_adopt", inject_symlink_after_prepare),
            self.assertRaises(SkillMutationError) as caught,
        ):
            self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(caught.exception.kind, "conflict")
        record = self.repository.history(name=self.name)[0]
        self.assertIs(record.state, SkillMutationState.CONFLICT)
        self.assertTrue((self.target / "SKILL.md").is_symlink())
        self.assertIsNotNone(self.repository.snapshot(record.operation_id))

    def test_new_bundle_veto_after_prepare_aborts_adoption(self):
        self.legacy()
        repository = self.ledger()
        before = self.inventory()
        bundle = self.root / "new-bundle"
        bundle.mkdir()
        prepare = SkillAdoptionRepository.prepare_adopt

        def add_bundle_after_prepare(state, draft, snapshot):
            record = prepare(state, draft, snapshot)
            shutil.copytree(self.target, bundle / self.name)
            (self.home / "skills" / ".bundled_manifest").write_text(
                self.name + ":" + "0" * 32 + "\n"
            )
            return record

        with (
            patch.object(SkillAdoptionRepository, "prepare_adopt", add_bundle_after_prepare),
            patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle),
            self.assertRaises(SkillMutationError) as caught,
        ):
            self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(caught.exception.kind, "denied")
        record = repository.history(name=self.name)[0]
        self.assertIs(record.state, SkillMutationState.ABORTED)
        self.assertEqual(self.inventory(), before)
        self.assertIsNone(repository.installation_evidence(self.name).current)
        self.assertEqual(repository.installation_evidence(self.name).pending_operation_ids, ())
        self.assertIsNotNone(repository.snapshot(record.operation_id))

    def test_unadopt_refuses_missing_or_independent_installation(self):
        self.ledger()
        source = self.root / "source.md"
        source.write_text(
            "---\nname: revisar-docs\ndescription: Independente.\nversion: '0.1.0'\n---\nCorpo.\n"
        )
        created = SkillMutationService(self.home, self.repository).add(
            source, actor=Actor.USER_FOREGROUND
        )
        service = self.service()
        history = self.repository.history(name=self.name)

        with self.assertRaises(SkillMutationError) as caught:
            service.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(caught.exception.kind, "denied")
        self.assertEqual(self.repository.history(name=self.name), history)
        self.target.rename(self.root / "missing-installation")
        with self.assertRaises(SkillMutationError) as caught:
            service.unadopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(self.repository.history(name=self.name), (created,))

    def test_pending_operation_blocks_adoption(self):
        self.legacy()
        repository = self.ledger()
        text = "---\nname: revisar-docs\ndescription: Legado.\nversion: '0.1.0'\n---\nCorpo.\n"
        creation = validate_skill_creation(text)
        operation_id = uuid.uuid4().hex
        with SkillMutationFiles(self.home) as files:
            entry = files.stage(operation_id, creation)
            draft = SkillMutationDraft(
                operation_id,
                repository.home_id,
                SkillMutationAction.CREATE,
                self.name,
                Actor.USER_FOREGROUND,
                1.0,
                entry.identity,
                creation.sha256,
                creation.size_bytes,
            )
            repository.prepare(draft, creation.text)

        with self.assertRaises(SkillMutationError) as caught:
            self.service().adopt(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(repository.pending(self.name)[0].operation_id, operation_id)

    def test_adoption_recovery_completes_only_matching_tree(self):
        self.legacy()
        repository = self.ledger()
        draft, record = _prepare_adoption(repository, self.home, self.target)

        from kairos_skills.adoption_recovery import reconcile_adoption_operation

        with SkillRemovalFiles(self.home) as files:
            recovered = reconcile_adoption_operation(repository, files, record)

        self.assertIs(recovered.state, SkillMutationState.COMMITTED)
        self.assertEqual(
            repository.installation_evidence(self.name).adoption.adoption_id, draft.operation_id
        )

    def test_adoption_recovery_absent_aborts_without_reconstruction(self):
        self.legacy()
        repository = self.ledger()
        _, record = _prepare_adoption(repository, self.home, self.target)
        self.target.rename(self.root / "outside")

        from kairos_skills.adoption_recovery import reconcile_adoption_operation

        with SkillRemovalFiles(self.home) as files:
            recovered = reconcile_adoption_operation(repository, files, record)

        self.assertIs(recovered.state, SkillMutationState.ABORTED)
        self.assertFalse(self.target.exists())
        self.assertTrue((self.root / "outside" / "SKILL.md").exists())

    def test_adoption_recovery_divergence_conflicts_and_preserves_proof(self):
        self.legacy()
        repository = self.ledger()
        draft, record = _prepare_adoption(repository, self.home, self.target)
        (self.target / "SKILL.md").write_bytes(b"changed after prepare\n")

        from kairos_skills.adoption_recovery import reconcile_adoption_operation

        with SkillRemovalFiles(self.home) as files, self.assertRaises(SkillMutationError):
            reconcile_adoption_operation(repository, files, record)

        self.assertIs(repository.get(draft.operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"changed after prepare\n")
        self.assertIsNotNone(repository.snapshot(draft.operation_id))

    def test_adoption_recovery_external_root_with_same_bytes_conflicts(self):
        self.legacy()
        repository = self.ledger()
        draft, record = _prepare_adoption(repository, self.home, self.target)
        self.target.rename(self.root / "previous-root")
        shutil.copytree(self.root / "previous-root", self.target)

        from kairos_skills.adoption_recovery import reconcile_adoption_operation

        with SkillRemovalFiles(self.home) as files, self.assertRaises(SkillMutationError) as caught:
            reconcile_adoption_operation(repository, files, record)

        self.assertEqual(caught.exception.kind, "conflict")
        self.assertIs(repository.get(draft.operation_id).state, SkillMutationState.CONFLICT)
        self.assertEqual(
            (self.target / "SKILL.md").read_bytes(), b"\x00\xfflegacy without semver\n"
        )
        self.assertIsNotNone(repository.snapshot(draft.operation_id))

    def test_adoption_recovery_bundle_veto_aborts_without_touching_tree(self):
        self.legacy()
        repository = self.ledger()
        draft, record = _prepare_adoption(repository, self.home, self.target)
        before = self.inventory()
        bundle = self.root / "trusted-bundle"
        shutil.copytree(self.target, bundle / self.name)
        (self.home / "skills" / ".bundled_manifest").write_text(self.name + ":" + "0" * 32 + "\n")

        from kairos_skills.adoption_recovery import reconcile_adoption_operation

        with (
            SkillRemovalFiles(self.home) as files,
            patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle),
        ):
            recovered = reconcile_adoption_operation(repository, files, record)

        self.assertIs(recovered.state, SkillMutationState.ABORTED)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(repository.installation_evidence(self.name).pending_operation_ids, ())
        self.assertEqual(repository.get(draft.operation_id).state, SkillMutationState.ABORTED)

    def test_adoption_recovery_attestation_unavailable_stays_prepared(self):
        self.legacy()
        repository = self.ledger()
        draft, record = _prepare_adoption(repository, self.home, self.target)

        from kairos_skills.adoption_recovery import reconcile_adoption_operation

        with (
            SkillRemovalFiles(self.home) as files,
            patch("kairos_filesystem.tree.os.listxattr", side_effect=OSError("unavailable")),
            self.assertRaises(SkillMutationError) as caught,
        ):
            reconcile_adoption_operation(repository, files, record)

        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertIs(repository.get(draft.operation_id).state, SkillMutationState.PREPARED)

        with (
            SkillRemovalFiles(self.home) as files,
            patch.object(
                SkillRemovalFiles,
                "capture_installed",
                side_effect=SkillMutationError("io", "injetado no limite real de atestação"),
            ),
            self.assertRaises(SkillMutationError) as caught,
        ):
            reconcile_adoption_operation(repository, files, record)

        self.assertEqual(caught.exception.kind, "io")
        self.assertIs(repository.get(draft.operation_id).state, SkillMutationState.PREPARED)


def _empty_repository(home):
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    return SkillMutationRepository(connection, home_id=catalog_home_id(home))


def _prepare_adoption(repository, home, target):
    from kairos_skills.adoption_contract import SkillAdoptionAction, SkillAdoptionDraft
    from kairos_state.repositories.skill_adoptions import SkillAdoptionRepository

    with SkillRemovalFiles(home) as files:
        snapshot = files.capture_installed(target.name)
    if snapshot is None:
        raise AssertionError("fixture installation unexpectedly absent")
    operation_id = uuid.uuid4().hex
    draft = SkillAdoptionDraft(
        operation_id,
        repository.home_id,
        SkillAdoptionAction.ADOPT,
        target.name,
        Actor.USER_FOREGROUND,
        1.0,
        operation_id,
        operation_id,
        snapshot.entries[0].device,
        snapshot.entries[0].inode,
        "unknown",
        operation_id,
    )
    state = SkillAdoptionRepository(repository._conn, home_id=repository.home_id)
    record = state.prepare_adopt(draft, snapshot)
    return draft, record


if __name__ == "__main__":
    unittest.main()
