"""Diagnóstico de instalações usa provas reais sem escrever no home."""

import dataclasses
import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_skills import identification
from kairos_skills.catalog import catalog_home_id
from kairos_skills.identification import identify_skill, read_installation_capture
from kairos_skills.mutation_contract import (
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationState,
)
from kairos_skills.mutation_io import SkillMutationFiles, read_skill_creation
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class SkillIdentificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.name = "revisar-docs"
        self.source = self.root / "source.md"
        self.source.write_text(creation_text())

    def ledger(self):
        self.home.mkdir(exist_ok=True)
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db)
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.service = SkillMutationService(self.home, self.repo)

    def create(self):
        self.ledger()
        return self.service.add(self.source, actor=Actor.USER_FOREGROUND)

    def legacy(self, content=b"# Legado sem semver\n"):
        target = self.home / "skills" / self.name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(content)
        (target / "scripts").mkdir()
        script = target / "scripts" / "run.bin"
        script.write_bytes(b"\x00\xff\x01")
        script.chmod(0o751)
        (target / "empty").mkdir(mode=0o710)
        return target

    def bundle(self):
        target = self.legacy()
        bundled = self.root / "bundle"
        shutil.copytree(target, bundled / self.name)
        (target.parent / ".bundled_manifest").write_text(self.name + ":" + "0" * 32 + "\n")
        return bundled

    def home_inventory(self):
        if not self.home.exists():
            return None
        result = {}
        for path in (self.home, *sorted(self.home.rglob("*"))):
            info = path.lstat()
            data = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
            result[str(path.relative_to(self.home))] = (
                info.st_mode,
                info.st_dev,
                info.st_ino,
                info.st_nlink,
                data,
            )
        return result

    def observe(self):
        before = self.home_inventory()
        report = identify_skill(self.home, self.name)
        self.assertEqual(self.home_inventory(), before)
        self.assertEqual(report.name, self.name)
        self.assertEqual(report.known_protection, ())
        self.assertEqual(report.protection_coverage, "namespace_and_recorded_only")
        return report

    def assert_origin(self, report, *, historical, source, basis, ownership, identity, removable):
        self.assertEqual(report.historical_creation_origin, historical)
        self.assertEqual(report.installation_source, source)
        self.assertEqual(report.authorization_basis, basis)
        self.assertEqual(report.ownership, ownership)
        self.assertIs(report.identity_valid, identity)
        self.assertIs(report.removivel, removable)

    def assert_concurrent(self, operation):
        with self.assertRaises(SkillMutationError) as raised:
            operation()
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertIn("consulta", str(raised.exception).lower())
        self.assertIn("repita", str(raised.exception).lower())
        self.assertNotIn(str(self.root), str(raised.exception))
        return raised.exception

    def test_unknown_origin_is_diagnostic_and_preserves_home(self):
        self.legacy()
        report = self.observe()
        self.assert_origin(
            report,
            historical="unknown",
            source="unknown",
            basis="none",
            ownership="user",
            identity=None,
            removable=False,
        )
        self.assertEqual(report.reasons, ("unknown_origin",))
        self.assertIsNone(report.current_installation_id)
        self.assertIsNone(report.historical_creation_id)
        self.assertIsNone(report.current_removal_id)
        self.assertEqual(report.pending_operation_ids, ())
        self.assertFalse((self.home / "state.db").exists())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.removivel = True

    def test_frontmatter_source_and_url_do_not_grant_authority(self):
        self.legacy(
            b"---\nname: revisar-docs\norigin: user\nsource: local\n"
            b"provenance: bundled\ntrust_level: trusted\n"
            b"url: https://example.invalid/skill\n---\nSem versao.\n"
        )
        report = self.observe()
        self.assert_origin(
            report,
            historical="unknown",
            source="unknown",
            basis="none",
            ownership="user",
            identity=None,
            removable=False,
        )

    def test_created_user_and_internal_edits_keep_write_authority(self):
        created = self.create()
        target = self.home / "skills" / self.name
        for edited in (False, True):
            with self.subTest(edited=edited):
                if edited:
                    (target / "SKILL.md").write_bytes(b"# Edicao sem frontmatter\n")
                    (target / "asset.bin").write_bytes(b"\x00\xff")
                    (target / "empty").mkdir()
                report = self.observe()
                self.assert_origin(
                    report,
                    historical="user",
                    source="local",
                    basis="proven_write",
                    ownership="user",
                    identity=True,
                    removable=True,
                )
                self.assertEqual(report.current_installation_id, created.operation_id)
                self.assertEqual(report.historical_creation_id, created.operation_id)
                self.assertEqual(report.reasons, ())

    def test_edited_user_restore_keeps_original_creation_id(self):
        created = self.create()
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        restored = self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        (self.home / "skills" / self.name / "SKILL.md").write_bytes(b"Restaurada e editada\n")
        report = self.observe()
        self.assert_origin(
            report,
            historical="user",
            source="local",
            basis="proven_write",
            ownership="user",
            identity=True,
            removable=True,
        )
        self.assertEqual(report.current_installation_id, restored.operation_id)
        self.assertEqual(report.historical_creation_id, created.operation_id)
        self.assertIsNone(report.current_removal_id)

    def test_exact_bundle_has_distribution_proof_without_creation(self):
        bundle = self.bundle()
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle):
            report = self.observe()
        self.assert_origin(
            report,
            historical="unknown",
            source="package_bundle",
            basis="verified_bundle",
            ownership="bundled",
            identity=None,
            removable=True,
        )
        self.assertIsNone(report.current_installation_id)
        self.assertIsNone(report.historical_creation_id)
        self.assertEqual(report.reasons, ())

    def test_bundled_restore_proves_write_without_inventing_user_creation(self):
        bundle = self.bundle()
        self.ledger()
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle):
            removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        restored = self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        (self.home / "skills" / self.name / "SKILL.md").write_bytes(b"Edicao local do bundle\n")
        report = self.observe()
        self.assert_origin(
            report,
            historical="unknown",
            source="package_bundle",
            basis="proven_write",
            ownership="bundled",
            identity=True,
            removable=True,
        )
        self.assertEqual(report.current_installation_id, restored.operation_id)
        self.assertIsNone(report.historical_creation_id)
        self.assertEqual(report.reasons, ())

    def test_restored_independent_bundle_does_not_attribute_previous_user_creation(self):
        created = self.create()
        first_removal = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        bundle = self.bundle()
        target = self.home / "skills" / self.name
        contents = (target / "SKILL.md").read_bytes()
        self.assertNotEqual(contents, self.source.read_bytes())
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle):
            report = self.observe()
            self.assert_origin(
                report,
                historical="unknown",
                source="package_bundle",
                basis="verified_bundle",
                ownership="bundled",
                identity=None,
                removable=True,
            )
            removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        restored = self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
        report = self.observe()
        self.assert_origin(
            report,
            historical="unknown",
            source="package_bundle",
            basis="proven_write",
            ownership="bundled",
            identity=True,
            removable=True,
        )
        evidence = self.repo.installation_evidence(self.name)
        self.assertIs(evidence.current.provenance, Provenance.BUNDLED)
        self.assertEqual(report.current_installation_id, restored.operation_id)
        self.assertEqual(report.current_installation_id, evidence.current.operation_id)
        self.assertIsNone(report.historical_creation_id)
        self.assertEqual(report.historical_creation_id, evidence.historical_creation_id)
        self.assertIsNone(report.current_removal_id)
        self.assertEqual(report.current_removal_id, evidence.current_removal_id)
        self.assertEqual(report.pending_operation_ids, ())
        self.assertEqual(report.pending_operation_ids, evidence.pending_operation_ids)
        self.assertEqual(report.reasons, ())
        self.assertEqual((target / "SKILL.md").read_bytes(), contents)
        history = self.repo.history(name=self.name)
        for record in (created, first_removal, removed, restored):
            self.assertIs(record.state, SkillMutationState.COMMITTED)
            self.assertIn(record, history)
        self.assertIs(removed.provenance, Provenance.BUNDLED)
        self.assertIs(restored.provenance, Provenance.BUNDLED)
        repeated_removal = self.service.remove(
            self.name, actor=Actor.USER_FOREGROUND, confirmed=True
        )
        self.assertIs(repeated_removal.state, SkillMutationState.COMMITTED)
        self.assertIs(repeated_removal.provenance, Provenance.BUNDLED)

    def test_byte_identical_replacement_keeps_ids_without_attributing_previous_origin(self):
        created = self.create()
        target = self.home / "skills" / self.name
        target.rename(self.root / "previous")
        shutil.copytree(self.root / "previous", target)
        report = self.observe()
        self.assert_origin(
            report,
            historical="unknown",
            source="unknown",
            basis="none",
            ownership="user",
            identity=False,
            removable=False,
        )
        self.assertEqual(report.current_installation_id, created.operation_id)
        self.assertEqual(report.historical_creation_id, created.operation_id)
        self.assertEqual(report.reasons, ("replaced_root",))

    def test_recreation_retains_previous_removal_diagnostic(self):
        created = self.create()
        removed = self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        newer = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        report = self.observe()
        self.assertNotEqual(newer.operation_id, created.operation_id)
        self.assertEqual(report.current_installation_id, newer.operation_id)
        self.assertEqual(report.historical_creation_id, newer.operation_id)
        self.assertEqual(report.current_removal_id, removed.operation_id)
        self.assertTrue(report.removivel)

    def prepare_remove(self):
        capture = read_installation_capture(self.home, self.name)
        operation_id = uuid.uuid4().hex
        draft = SkillTreeDraft(
            operation_id,
            self.repo.home_id,
            SkillTreeAction.REMOVE,
            self.name,
            Actor.USER_FOREGROUND,
            1.0,
            Provenance.USER,
            TreeCapture(capture.entries, None),
            operation_id,
        )
        return self.repo.prepare_remove(draft, capture)

    def test_pending_remove_is_diagnostic_without_reconciliation(self):
        created = self.create()
        pending = self.prepare_remove()
        for state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT):
            if state is SkillMutationState.CONFLICT:
                self.repo.finish(pending.operation_id, state)
            with self.subTest(state=state):
                report = self.observe()
                self.assertEqual(report.current_installation_id, created.operation_id)
                self.assertEqual(report.authorization_basis, "proven_write")
                self.assertTrue(report.identity_valid)
                self.assertFalse(report.removivel)
                self.assertEqual(report.pending_operation_ids, (pending.operation_id,))
                self.assertEqual(report.reasons, ("pending_operation",))
                self.assertIs(self.repo.get(pending.operation_id).state, state)

    def test_prepared_real_creation_stays_prepared_and_preserves_staging(self):
        self.ledger()
        operation_id = uuid.uuid4().hex
        creation = read_skill_creation(self.source)
        with SkillMutationFiles(self.home) as files:
            entry = files.stage(operation_id, creation)
            draft = SkillMutationDraft(
                operation_id,
                self.repo.home_id,
                SkillMutationAction.CREATE,
                self.name,
                Actor.USER_FOREGROUND,
                1.0,
                entry.identity,
                creation.sha256,
                creation.size_bytes,
            )
            self.repo.prepare(draft, creation.text)
        target = self.home / "skills" / self.name
        shutil.copytree(self.home / ".skill-mutations" / "staging" / operation_id, target)
        report = self.observe()
        self.assertEqual(report.authorization_basis, "none")
        self.assertIsNone(report.current_installation_id)
        self.assertEqual(report.pending_operation_ids, (operation_id,))
        self.assertEqual(report.reasons, ("unknown_origin", "pending_operation"))
        self.assertFalse(report.removivel)
        self.assertIs(self.repo.get(operation_id).state, SkillMutationState.PREPARED)

    def test_closed_database_has_no_effects_or_new_sidecars(self):
        self.create()
        self.db.close()
        self.assertFalse((self.home / "state.db-wal").exists())
        self.assertFalse((self.home / "state.db-shm").exists())
        report = self.observe()
        self.assertTrue(report.removivel)

    def test_wal_only_creation_is_visible_without_writer_capability(self):
        self.ledger()
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        created = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertGreater((self.home / "state.db-wal").stat().st_size, 0)
        with sqlite3.connect((self.home / "state.db").as_uri() + "?immutable=1", uri=True) as main:
            self.assertIsNone(
                main.execute(
                    "SELECT operation_id FROM skill_mutation_operations WHERE operation_id=?",
                    (created.operation_id,),
                ).fetchone()
            )
        original = identification.database_connection
        opened = []

        @contextmanager
        def read_without_writer(home, *, write=False):
            self.assertFalse(write)
            with original(home, write=write) as connection:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("SELECT kairos_skill_writer_version()")
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE accidental_write(value)")
                opened.append(Path(connection.execute("PRAGMA database_list").fetchone()[2]))
                yield connection

        with patch.object(identification, "database_connection", read_without_writer):
            report = self.observe()
        self.assertEqual(report.current_installation_id, created.operation_id)
        self.assertTrue(report.removivel)
        self.assertGreaterEqual(len(set(opened)), 2)
        self.assertTrue(all(not path.parent.exists() for path in opened))

    def test_read_capture_preserves_binary_executable_and_empty_directory(self):
        target = self.legacy(b"\x00\xffSKILL legado\n")
        before = self.home_inventory()
        capture = read_installation_capture(self.home, self.name)
        entries = {entry.path: entry for entry in capture.entries}
        self.assertEqual(capture.contents["SKILL.md"], b"\x00\xffSKILL legado\n")
        self.assertEqual(capture.contents["scripts/run.bin"], b"\x00\xff\x01")
        self.assertEqual(entries["scripts/run.bin"].mode, 0o751)
        self.assertEqual(entries["empty"].kind, "directory")
        self.assertEqual(entries["empty"].mode, 0o710)
        self.assertEqual(entries[""].inode, target.stat().st_ino)
        self.assertEqual(self.home_inventory(), before)

    def test_missing_home_or_target_is_conflict_without_creation(self):
        for missing in ("home", "skills", "target"):
            for method in (identify_skill, read_installation_capture):
                with self.subTest(missing=missing, method=method.__name__):
                    if missing == "skills":
                        self.home.mkdir(exist_ok=True)
                    elif missing == "target":
                        (self.home / "skills").mkdir(exist_ok=True)
                    before = self.home_inventory()
                    with self.assertRaises(SkillMutationError) as raised:
                        method(self.home, self.name)
                    self.assertEqual(raised.exception.kind, "conflict")
                    self.assertEqual(self.home_inventory(), before)

    def test_invalid_names_are_rejected_before_filesystem_access(self):
        class NoFilesystemAccess:
            def __fspath__(self):
                raise AssertionError("Nome inválido acessou o filesystem")

        for method in (identify_skill, read_installation_capture):
            for name in ("_org", "../escape", "not valid", "Legacy_Name"):
                with self.subTest(method=method.__name__, name=name):
                    with self.assertRaises(SkillMutationError) as raised:
                        method(NoFilesystemAccess(), name)
                    self.assertEqual(raised.exception.kind, "input")

    def test_corrupt_and_incompatible_ledgers_are_errors_without_effects(self):
        self.create()
        for corrupt in ("partial", "incompatible"):
            with self.subTest(corrupt=corrupt):
                if corrupt == "partial":
                    self.db.execute("DROP TABLE skill_tree_events")
                else:
                    self.db.execute("UPDATE schema_version SET version=9")
                self.db.commit()
                before = self.home_inventory()
                with self.assertRaises(SkillMutationError) as raised:
                    identify_skill(self.home, self.name)
                self.assertEqual(raised.exception.kind, "corrupt")
                self.assertNotIn(str(self.root), str(raised.exception))
                self.assertEqual(self.home_inventory(), before)

    def test_legacy_database_is_read_without_migration(self):
        self.legacy()
        with sqlite3.connect(self.home / "state.db") as connection:
            connection.execute("CREATE TABLE schema_version(version INTEGER)")
            connection.execute("INSERT INTO schema_version VALUES(6)")
            connection.execute("CREATE TABLE prior_state(value TEXT)")
            connection.execute("INSERT INTO prior_state VALUES('preservar')")
        report = self.observe()
        self.assertEqual(report.authorization_basis, "none")
        self.assertFalse(report.removivel)
        with sqlite3.connect(self.home / "state.db") as connection:
            self.assertEqual(
                connection.execute("SELECT version FROM schema_version").fetchone(), (6,)
            )
            self.assertEqual(
                connection.execute("SELECT value FROM prior_state").fetchone(), ("preservar",)
            )

    def test_database_appearance_between_observations_is_conflict(self):
        self.legacy()
        original = identification.resolve_removal_origin

        def resolve_then_create_database(*args):
            result = original(*args)
            with sqlite3.connect(self.home / "state.db") as connection:
                connection.execute("CREATE TABLE prior_state(value TEXT)")
                connection.execute("INSERT INTO prior_state VALUES('concorrente')")
            return result

        with patch.object(identification, "resolve_removal_origin", resolve_then_create_database):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))
        self.assertTrue((self.home / "state.db").exists())

    def test_pending_transition_with_same_ids_between_observations_is_conflict(self):
        self.create()
        pending = self.prepare_remove()
        original = identification.resolve_removal_origin

        def resolve_then_mark_conflict(*args):
            result = original(*args)
            self.repo.finish(pending.operation_id, SkillMutationState.CONFLICT)
            return result

        with patch.object(identification, "resolve_removal_origin", resolve_then_mark_conflict):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))
        self.assertIs(self.repo.get(pending.operation_id).state, SkillMutationState.CONFLICT)

    def test_second_database_change_is_conflict_before_new_corruption_is_interpreted(self):
        self.create()
        original = identification.resolve_removal_origin

        def resolve_then_drop_table(*args):
            result = original(*args)
            self.db.execute("DROP TABLE skill_tree_events")
            self.db.commit()
            return result

        with patch.object(identification, "resolve_removal_origin", resolve_then_drop_table):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))

    def test_second_database_with_unreadable_sqlite_bytes_is_concurrent_conflict(self):
        self.create()
        self.db.close()
        original = identification.resolve_removal_origin

        def resolve_then_corrupt_database(*args):
            result = original(*args)
            (self.home / "state.db").write_bytes(b"Nao e um banco SQLite\n")
            return result

        with patch.object(identification, "resolve_removal_origin", resolve_then_corrupt_database):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))

    def test_real_wal_commit_during_copy_is_conflict_and_preserves_committed_change(self):
        self.create()
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        original = os.read
        database_inode = (self.home / "state.db").stat().st_ino
        committed = []

        def read_then_commit(fd, length):
            result = original(fd, length)
            if os.fstat(fd).st_ino == database_inode and not committed:
                committed.append(self.prepare_remove())
            return result

        with patch("kairos_skills.removal_state.os.read", read_then_commit):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))
        self.assertTrue(committed)
        self.assertEqual(self.repo.pending(self.name), tuple(committed))

    def test_evidence_change_between_observations_is_conflict(self):
        self.create()
        original = identification.resolve_removal_origin
        pending = []

        def resolve_then_prepare(*args):
            result = original(*args)
            if not pending:
                pending.append(self.prepare_remove())
            return result

        with patch.object(identification, "resolve_removal_origin", resolve_then_prepare):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))
        self.assertEqual(self.repo.pending(self.name), tuple(pending))

    def test_tree_change_between_observations_is_conflict(self):
        self.create()
        original = identification.resolve_removal_origin
        target = self.home / "skills" / self.name / "SKILL.md"

        def resolve_then_edit(*args):
            result = original(*args)
            target.write_bytes(b"Edicao concorrente\n")
            return result

        with patch.object(identification, "resolve_removal_origin", resolve_then_edit):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))
        self.assertEqual(target.read_bytes(), b"Edicao concorrente\n")

    def test_target_renamed_after_first_capture_is_concurrent_conflict(self):
        self.create()
        original = identification.read_installation_capture
        target = self.home / "skills" / self.name
        after_rename = []

        def capture_then_rename(home, name):
            result = original(home, name)
            if not after_rename:
                target.rename(self.root / "renamed")
                after_rename.append(self.home_inventory())
            return result

        with patch.object(identification, "read_installation_capture", capture_then_rename):
            self.assert_concurrent(lambda: identify_skill(self.home, self.name))
        self.assertEqual(self.home_inventory(), after_rename[0])
        self.assertFalse(target.exists())
        self.assertEqual(
            (self.root / "renamed" / "SKILL.md").read_bytes(), self.source.read_bytes()
        )

    def test_stable_bundle_io_failure_is_preserved_without_home_effects(self):
        self.bundle()
        before = self.home_inventory()
        with (
            patch(
                "kairos_skills.removal_origin.trusted_bundle_root",
                return_value=self.root / "missing-bundle",
            ),
            self.assertRaises(SkillMutationError) as raised,
        ):
            identify_skill(self.home, self.name)
        self.assertEqual(raised.exception.kind, "io")
        self.assertEqual(self.home_inventory(), before)

    def test_home_chain_replacement_during_capture_is_refused(self):
        self.legacy()
        original = identification.capture_skill
        changed = False

        def capture_then_replace(parent, name):
            nonlocal changed
            result = original(parent, name)
            if not changed:
                changed = True
                self.home.rename(self.root / "previous-home")
                shutil.copytree(self.root / "previous-home", self.home)
            return result

        with (
            patch.object(identification, "capture_skill", capture_then_replace),
            self.assertRaises(SkillMutationError) as raised,
        ):
            read_installation_capture(self.home, self.name)
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertEqual(
            (self.home / "skills" / self.name / "scripts/run.bin").read_bytes(), b"\x00\xff\x01"
        )

    def test_unavailable_attestation_is_error_and_preserves_home(self):
        self.legacy()
        before = self.home_inventory()
        with (
            patch("kairos_filesystem.tree.os.listxattr", side_effect=OSError),
            self.assertRaises(SkillMutationError) as raised,
        ):
            identify_skill(self.home, self.name)
        self.assertEqual(raised.exception.kind, "unavailable")
        self.assertEqual(self.home_inventory(), before)
