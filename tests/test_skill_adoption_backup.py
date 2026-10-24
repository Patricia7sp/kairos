"""Backups retain the canonical A/U ledger and refuse to refresh tree authority."""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from skill_org_fixtures import personal_operation, personal_policy

from kairos_cli.backup import run_backup
from kairos_domain.ownership import Actor
from kairos_skills.adoption_contract import SkillAdoptionAction
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import reconcile_pending
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import SkillTreeAction
from kairos_skills.removal_io import SkillRemovalFiles
from kairos_state import connect
from kairos_state.migrations import canonical_fingerprint, migrate, repair_derived_objects
from kairos_state.repositories.skill_adoptions import SkillAdoptionRepository
from kairos_state.repositories.skill_mutations import SkillMutationRepository

REPO = Path(__file__).resolve().parents[1]
BINARY = b"\x00\xff\x80adoption-snapshot"
PRIVATE_FIXTURE = "sentinela-privada-backup-á"
CANONICAL_TABLE_QUERIES = (
    ("skill_mutation_operations", "SELECT * FROM skill_mutation_operations ORDER BY rowid"),
    ("skill_mutation_events", "SELECT * FROM skill_mutation_events ORDER BY rowid"),
    ("skill_mutation_contents", "SELECT * FROM skill_mutation_contents ORDER BY rowid"),
    ("skill_mutation_timeline", "SELECT * FROM skill_mutation_timeline ORDER BY rowid"),
    ("skill_tree_operations", "SELECT * FROM skill_tree_operations ORDER BY rowid"),
    ("skill_tree_events", "SELECT * FROM skill_tree_events ORDER BY rowid"),
    ("skill_tree_snapshots", "SELECT * FROM skill_tree_snapshots ORDER BY rowid"),
    ("skill_tree_blobs", "SELECT * FROM skill_tree_blobs ORDER BY rowid"),
    ("skill_tree_adoptions", "SELECT * FROM skill_tree_adoptions ORDER BY rowid"),
    ("skill_adoption_operations", "SELECT * FROM skill_adoption_operations ORDER BY rowid"),
    ("skill_adoption_events", "SELECT * FROM skill_adoption_events ORDER BY rowid"),
    ("skill_authority_timeline", "SELECT * FROM skill_authority_timeline ORDER BY rowid"),
    ("skill_authority_migration", "SELECT * FROM skill_authority_migration ORDER BY rowid"),
)


@personal_operation
def _stopped_adoption(home, name, pipe, *, policy):
    database = connect(home / "state.db")
    migrate(database)
    repository = SkillMutationRepository(database, home_id=catalog_home_id(home))
    original = SkillAdoptionRepository.prepare_adopt

    def prepare(adoptions, draft, snapshot):
        record = original(adoptions, draft, snapshot)
        pipe.send(draft.operation_id)
        pipe.recv()
        return record

    with patch.object(SkillAdoptionRepository, "prepare_adopt", prepare):
        SkillAdoptionService(home, repository, policy=policy).adopt(
            name,
            actor=Actor.USER_FOREGROUND,
            confirmed=True,
        )


@personal_operation
def _stopped_restore(home, name, remove_id, pipe, *, policy):
    database = connect(home / "state.db")
    migrate(database)
    repository = SkillMutationRepository(database, home_id=catalog_home_id(home))
    service = SkillMutationService(home, repository, policy=policy)
    original = repository.prepare_restore

    def prepare(draft, **kwargs):
        record = original(draft, **kwargs)
        pipe.send(draft.operation_id)
        pipe.recv()
        return record

    repository.prepare_restore = prepare
    service.rollback(
        remove_id,
        actor=Actor.USER_FOREGROUND,
    )


def _canonical_rows(database):
    return {
        table: tuple(tuple(row) for row in database.execute(query))
        for table, query in CANONICAL_TABLE_QUERIES
    }


def _extract_public_archive(archive: Path, target_home: Path) -> None:
    directory_modes = []
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            relative = PurePosixPath(member.name)
            if (
                not (member.isfile() or member.isdir())
                or relative.is_absolute()
                or not relative.parts
                or any(part in ("", ".", "..") for part in relative.parts)
            ):
                raise AssertionError("Backup de fixture contém entrada não permitida.")
            destination = target_home.joinpath(*relative.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                directory_modes.append((destination, member.mode & 0o7777))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise AssertionError("Arquivo de backup sem conteúdo legível.")
            with source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)
            destination.chmod(member.mode & 0o777)
    for directory, mode in sorted(
        directory_modes, key=lambda item: len(item[0].parts), reverse=True
    ):
        directory.chmod(mode)


class SkillAdoptionBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.source = self.root / "source.md"
        self.private_fixture = PRIVATE_FIXTURE

    def command(self, args, *, home=None):
        target_home = self.home if home is None else Path(home)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from kairos_cli.main import main; raise SystemExit(main())",
                *args,
            ],
            cwd=REPO,
            env={**os.environ, "KAIROS_HOME": str(target_home)},
            input="",
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertNotIn(self.private_fixture, result.stdout + result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        return result.returncode, result.stdout, result.stderr

    def success_json(self, args, *, home=None):
        code, output, error = self.command(args, home=home)
        self.assertEqual(code, 0, error)
        return json.loads(output)

    def legacy(self, name="legacy-backup"):
        target = self.home / "skills" / name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(self.private_fixture, encoding="utf-8")
        binary = target / "references" / "private.bin"
        binary.parent.mkdir()
        binary.write_bytes(BINARY)
        binary.chmod(0o641)
        return target

    def repo(self, home=None):
        target_home = self.home if home is None else Path(home)
        db = connect(target_home / "state.db")
        repository = SkillMutationRepository(db, home_id=catalog_home_id(target_home))
        return db, repository

    def canonical_rows(self, home=None):
        target_home = self.home if home is None else Path(home)
        database = connect(target_home / "state.db")
        try:
            return _canonical_rows(database)
        finally:
            database.close()

    def interrupt(self, target, *args):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=target, args=(*args, child))
        process.start()
        try:
            self.assertTrue(parent.poll(20), "Subprocesso não atingiu a fronteira durável.")
            operation_id = parent.recv()
            process.kill()
            process.join(5)
            self.assertEqual(process.exitcode, -9)
            return operation_id
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def reconcile(self, name):
        database = connect(self.home / "state.db")
        try:
            repository = SkillMutationRepository(database, home_id=catalog_home_id(self.home))
            with skill_mutation_lock(self.home), SkillRemovalFiles(self.home) as files:
                return reconcile_pending(
                    repository, files, name=name, policy=personal_policy(self, files.home)
                )
        finally:
            database.close()

    def sqlite_backup(self, target: Path, *, home=None):
        source_home = self.home if home is None else Path(home)
        source = sqlite3.connect(source_home / "state.db")
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    def public_backup(self, target: Path, *, home=None):
        source_home = self.home if home is None else Path(home)
        args = argparse.Namespace(output=str(target), json=True)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = run_backup(source_home, args)
        report = json.loads(output.getvalue())
        with tarfile.open(target, "r:gz") as bundle:
            archive_file_count = sum(member.isfile() for member in bundle.getmembers())
        self.assertEqual(report["files"], archive_file_count)
        return code

    def backup_roundtrip_files(self, label, expected_rows, *, home=None):
        source_home = self.home if home is None else Path(home)
        consistent_db = self.root / f"{label}.db"
        archive = self.root / f"{label}.tar.gz"
        self.sqlite_backup(consistent_db, home=source_home)
        self.assertEqual(self.public_backup(archive, home=source_home), 0)
        copy = sqlite3.connect(consistent_db)
        try:
            self.assertEqual(_canonical_rows(copy), expected_rows)
        finally:
            copy.close()
        return consistent_db, archive

    def restore_archive_in_place(self, archive: Path, *, home=None, retain_root=None):
        target_home = self.home if home is None else Path(home)
        held = None
        if retain_root is not None:
            held = self.root / "held-original-root"
            retain_root.rename(held)
        for entry in target_home.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        _extract_public_archive(archive, target_home)
        if held is not None:
            shutil.rmtree(held)

    def create_A_M_R_U_history(self):
        installed = self.legacy()
        original_root_inode = installed.stat().st_ino
        adoption = self.success_json(["skills", "adopt", "legacy-backup", "--yes", "--json"])
        removal = self.success_json(["skills", "remove", "legacy-backup", "--yes", "--json"])
        restored = self.success_json(["skills", "rollback", removal["operation_id"], "--json"])
        unadopted = self.success_json(["skills", "unadopt", "legacy-backup", "--yes", "--json"])
        return {
            "installed": installed,
            "original_root_inode": original_root_inode,
            "adoption_id": adoption["adoption_id"],
            "removal_id": removal["operation_id"],
            "restore_id": restored["operation_id"],
            "unadoption_id": unadopted["operation_id"],
        }

    def expected_history_and_home_id(self, database_path, name):
        snapshot_db = sqlite3.connect(database_path)
        try:
            repository = SkillMutationRepository(snapshot_db, home_id=catalog_home_id(self.home))
            history = tuple(
                (record.operation_id, record.action.value, record.state.value)
                for record in repository.history(name=name, limit=20)
            )
            return history, repository.home_id
        finally:
            snapshot_db.close()

    def assert_restored_lineage(self, rows, history, ids):
        database, repository = self.repo()
        try:
            self.assertEqual(_canonical_rows(database), rows)
            records = repository.history(name="legacy-backup", limit=20)
            self.assertEqual(
                tuple(
                    (record.operation_id, record.action.value, record.state.value)
                    for record in records
                ),
                history,
            )
            self.assertEqual(
                [record.action for record in records],
                [
                    SkillAdoptionAction.UNADOPT,
                    SkillTreeAction.RESTORE,
                    SkillTreeAction.REMOVE,
                    SkillAdoptionAction.ADOPT,
                ],
            )
            evidence = repository.installation_evidence("legacy-backup")
            self.assertEqual(evidence.current.operation_id, ids["restore_id"])
            self.assertEqual(evidence.adoption.adoption_id, ids["adoption_id"])
            self.assertEqual(evidence.adoption.installation_id, ids["restore_id"])
            self.assertFalse(evidence.adoption.active)
            self.assertEqual(
                repository.snapshot(ids["adoption_id"]).contents["references/private.bin"], BINARY
            )
            self.assertEqual(
                repository.snapshot(ids["removal_id"]).contents["references/private.bin"], BINARY
            )
            self.assertEqual(repository.get(ids["unadoption_id"]).action.value, "unadopt")
        finally:
            database.close()

    def assert_transplant_has_no_authority(self, archive):
        transplanted = self.root / "transplanted-home"
        transplanted.mkdir()
        _extract_public_archive(archive, transplanted)
        cloned_tree = transplanted / "skills" / "legacy-backup"
        self.assertEqual((cloned_tree / "references" / "private.bin").read_bytes(), BINARY)
        code, output, error = self.command(
            ["skills", "identify", "legacy-backup", "--json"], home=transplanted
        )
        if code == 0:
            report = json.loads(output)
            self.assertFalse(report["identity_valid"])
            self.assertFalse(report["adoption_active"])
            self.assertEqual(report["authorization_basis"], "none")
        else:
            self.assertEqual(code, 1)
            self.assertEqual(output, "")
            self.assertTrue(error)

    def test_public_backup_retains_committed_ledger_during_concurrent_checkpoint(self):
        self.home.mkdir()
        keeper = connect(self.home / "state.db")
        self.addCleanup(keeper.close)
        migrate(keeper)
        ids = self.create_A_M_R_U_history()
        expected_rows = self.canonical_rows()
        archive = self.root / "concurrent-backup.tar.gz"
        original_add = tarfile.TarFile.add
        checkpointed = []

        def write_and_checkpoint():
            writer = sqlite3.connect(self.home / "state.db")
            try:
                writer.execute("CREATE TABLE backup_probe (value TEXT)")
                writer.execute("INSERT INTO backup_probe VALUES ('concurrent commit')")
                writer.commit()
                return writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            finally:
                writer.close()

        with ThreadPoolExecutor(max_workers=1) as executor:

            def add_then_checkpoint(bundle, path, *args, **kwargs):
                result = original_add(bundle, path, *args, **kwargs)
                if kwargs.get("arcname") == "state.db":
                    checkpointed.append(executor.submit(write_and_checkpoint).result(timeout=10))
                return result

            with patch.object(tarfile.TarFile, "add", add_then_checkpoint):
                self.assertEqual(self.public_backup(archive), 0)

        self.assertEqual(checkpointed, [(0, 0, 0)])
        restored_home = self.root / "concurrent-restored"
        restored_home.mkdir()
        _extract_public_archive(archive, restored_home)
        restored = sqlite3.connect(restored_home / "state.db")
        try:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            tables = {row[0] for row in restored.execute("SELECT name FROM sqlite_master")}
            self.assertTrue(set(expected_rows).issubset(tables), "Backup perdeu tabelas committed.")
            self.assertEqual(_canonical_rows(restored), expected_rows)
            repository = SkillMutationRepository(restored, home_id=catalog_home_id(self.home))
            self.assertFalse(repository.installation_evidence("legacy-backup").adoption.active)
            self.assertEqual(
                repository.snapshot(ids["adoption_id"]).contents["references/private.bin"], BINARY
            )
        finally:
            restored.close()
        with tarfile.open(archive, "r:gz") as bundle:
            self.assertNotIn("state.db-wal", bundle.getnames())
            self.assertNotIn("state.db-shm", bundle.getnames())

    def test_public_backup_refuses_unreadable_database_without_success_or_secret(self):
        self.home.mkdir()
        (self.home / "state.db").write_text(self.private_fixture)
        output, error = io.StringIO(), io.StringIO()
        args = argparse.Namespace(output=str(self.root / "invalid.tar.gz"), json=True)
        with redirect_stdout(output), redirect_stderr(error):
            code = run_backup(self.home, args)
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(error.getvalue())
        self.assertNotIn(self.private_fixture, error.getvalue())
        self.assertNotIn(str(self.root), error.getvalue())

    def test_backup_restore_preserves_A_U_lineage_and_binary_snapshots(self):
        ids = self.create_A_M_R_U_history()
        canonical_before_backup = self.canonical_rows()
        consistent_db, archive = self.backup_roundtrip_files("home-backup", canonical_before_backup)
        history, expected_home_id = self.expected_history_and_home_id(
            consistent_db, "legacy-backup"
        )
        snapshot_db = sqlite3.connect(consistent_db)
        try:
            snapshot_repository = SkillMutationRepository(
                snapshot_db, home_id=catalog_home_id(self.home)
            )
            for snapshot_id in (ids["adoption_id"], ids["removal_id"]):
                snapshot = snapshot_repository.snapshot(snapshot_id)
                self.assertEqual(snapshot.contents["references/private.bin"], BINARY)
                self.assertIn("references/private.bin", {entry.path for entry in snapshot.entries})
            self.assertEqual(
                next(
                    entry.mode
                    for entry in snapshot_repository.snapshot(ids["removal_id"]).entries
                    if entry.path == "references/private.bin"
                ),
                0o641,
            )
        finally:
            snapshot_db.close()

        self.restore_archive_in_place(archive, retain_root=ids["installed"])
        self.assertTrue(self.home.is_dir())
        for directory in (
            ".skill-mutations",
            ".skill-mutations/staging",
            ".skill-mutations/retired",
        ):
            self.assertEqual(
                (self.home / directory).stat().st_mode & 0o777,
                0o700,
            )
        self.assertEqual(catalog_home_id(self.home), expected_home_id)
        restored_tree = self.home / "skills" / "legacy-backup"
        self.assertNotEqual(restored_tree.stat().st_ino, ids["original_root_inode"])
        self.assertEqual((restored_tree / "references" / "private.bin").read_bytes(), BINARY)
        self.assertEqual(
            (restored_tree / "references" / "private.bin").stat().st_mode & 0o777,
            0o641,
        )

        self.assert_restored_lineage(canonical_before_backup, history, ids)

        from kairos_skills.identification import identify_skill

        identified = identify_skill(
            self.home, "legacy-backup", policy=personal_policy(self, self.home)
        )
        self.assertFalse(identified.identity_valid)
        self.assertFalse(identified.adoption_active)
        self.assertEqual(identified.authorization_basis, "none")

        self.assert_transplant_has_no_authority(archive)

    def test_repair_preserves_all_canonical_rows(self):
        self.legacy()
        code, _output, error = self.command(["skills", "adopt", "legacy-backup", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        self.assertEqual(self.command(["skills", "unadopt", "legacy-backup", "--yes"])[0], 0)

        db, repository = self.repo()
        try:
            self.assertEqual(
                [record.action.value for record in repository.history(name="legacy-backup")],
                ["unadopt", "adopt"],
            )
            before = _canonical_rows(db)
            fingerprint = canonical_fingerprint(db)
            repair_derived_objects(db)
            self.assertEqual(canonical_fingerprint(db), fingerprint)
            self.assertEqual(_canonical_rows(db), before)
            after = repository.history(name="legacy-backup")
            self.assertEqual([record.action.value for record in after], ["unadopt", "adopt"])
            self.assertFalse(repository.installation_evidence("legacy-backup").adoption.active)
        finally:
            db.close()

    def test_backup_roundtrip_preserves_adopted_removal_tombstone(self):
        self.legacy()
        code, output, error = self.command(["skills", "adopt", "legacy-backup", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption_id = json.loads(output)["adoption_id"]
        code, output, error = self.command(["skills", "remove", "legacy-backup", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        removal_id = json.loads(output)["operation_id"]

        canonical_before = self.canonical_rows()
        _, archive = self.backup_roundtrip_files("tombstone-home", canonical_before)

        self.restore_archive_in_place(archive)
        for directory in (
            ".skill-mutations",
            ".skill-mutations/staging",
            ".skill-mutations/retired",
        ):
            self.assertEqual(
                (self.home / directory).stat().st_mode & 0o777,
                0o700,
            )
        database, repository = self.repo()
        try:
            self.assertEqual(_canonical_rows(database), canonical_before)
            removal = repository.get(removal_id)
            self.assertEqual(removal.action, SkillTreeAction.REMOVE)
            self.assertEqual(removal.state, SkillMutationState.COMMITTED)
            self.assertEqual(removal.draft.adoption.adoption_id, adoption_id)
            self.assertEqual(
                repository.snapshot(removal_id).contents["references/private.bin"], BINARY
            )
            self.assertEqual(repository.current_removal("legacy-backup").operation_id, removal_id)
        finally:
            database.close()

        code, output, error = self.command(["skills", "rollback", removal_id, "--json"])
        self.assertEqual(code, 0, error)
        restored_id = json.loads(output)["operation_id"]
        code, output, error = self.command(["skills", "identify", "legacy-backup", "--json"])
        self.assertEqual(code, 0, error)
        identified = json.loads(output)
        self.assertEqual(identified["current_installation_id"], restored_id)
        self.assertEqual(identified["adoption_id"], adoption_id)
        self.assertTrue(identified["adoption_active"])
        self.assertEqual(
            (self.home / "skills" / "legacy-backup" / "references" / "private.bin").read_bytes(),
            BINARY,
        )

    def test_backup_reopen_resolves_prepared_adoption_fail_closed_on_new_root(self):
        installed = self.legacy()
        adoption_id = self.interrupt(_stopped_adoption, self.home, "legacy-backup")
        database, repository = self.repo()
        try:
            self.assertEqual(repository.get(adoption_id).state, SkillMutationState.PREPARED)
        finally:
            database.close()

        canonical_before = self.canonical_rows()
        _, archive = self.backup_roundtrip_files("prepared-adoption-home", canonical_before)

        self.restore_archive_in_place(archive, retain_root=installed)
        self.assertEqual(self.canonical_rows(), canonical_before)
        with self.assertRaises(SkillMutationError) as caught:
            self.reconcile("legacy-backup")
        self.assertEqual(caught.exception.kind, "conflict")
        database, repository = self.repo()
        try:
            self.assertEqual(repository.get(adoption_id).state, SkillMutationState.CONFLICT)
            evidence = repository.installation_evidence("legacy-backup")
            self.assertIsNone(evidence.adoption)
            self.assertEqual(_canonical_rows(database)["skill_adoption_events"][-1][2], "conflict")
        finally:
            database.close()
        self.assertTrue((self.home / "skills" / "legacy-backup" / "SKILL.md").is_file())

        conflict_rows = self.canonical_rows()
        _, conflict_archive = self.backup_roundtrip_files("conflict-home", conflict_rows)
        self.restore_archive_in_place(conflict_archive)
        database, repository = self.repo()
        try:
            self.assertEqual(_canonical_rows(database), conflict_rows)
            self.assertEqual(repository.get(adoption_id).state, SkillMutationState.CONFLICT)
            self.assertIsNone(repository.installation_evidence("legacy-backup").adoption)
        finally:
            database.close()

    def test_backup_reopen_conflicts_prepared_restore_without_renewing_staging_identity(self):
        installed = self.legacy()
        code, output, error = self.command(["skills", "adopt", "legacy-backup", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption_id = json.loads(output)["adoption_id"]
        code, output, error = self.command(["skills", "remove", "legacy-backup", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        removal_id = json.loads(output)["operation_id"]
        self.assertFalse(installed.exists())

        restore_id = self.interrupt(_stopped_restore, self.home, "legacy-backup", removal_id)
        database, repository = self.repo()
        try:
            self.assertEqual(repository.get(restore_id).state, SkillMutationState.PREPARED)
            canonical_before = _canonical_rows(database)
        finally:
            database.close()

        _, archive = self.backup_roundtrip_files("prepared-restore-home", canonical_before)

        self.restore_archive_in_place(archive)
        self.assertEqual(self.canonical_rows(), canonical_before)
        for directory in (
            ".skill-mutations",
            ".skill-mutations/staging",
            ".skill-mutations/retired",
        ):
            self.assertEqual((self.home / directory).stat().st_mode & 0o777, 0o700)
        with self.assertRaises(SkillMutationError) as caught:
            self.reconcile("legacy-backup")
        self.assertEqual(caught.exception.kind, "conflict")
        database, repository = self.repo()
        try:
            self.assertEqual(repository.get(restore_id).state, SkillMutationState.CONFLICT)
            self.assertEqual(repository.current_removal("legacy-backup").operation_id, removal_id)
            self.assertEqual(repository.get(removal_id).draft.adoption.adoption_id, adoption_id)
            self.assertEqual(
                repository.snapshot(removal_id).contents["references/private.bin"], BINARY
            )
        finally:
            database.close()
        self.assertFalse((self.home / "skills" / "legacy-backup").exists())
        conflict_rows = self.canonical_rows()
        _, conflict_archive = self.backup_roundtrip_files("conflicted-restore-home", conflict_rows)
        self.restore_archive_in_place(conflict_archive)
        database, repository = self.repo()
        try:
            self.assertEqual(_canonical_rows(database), conflict_rows)
            self.assertEqual(repository.get(restore_id).state, SkillMutationState.CONFLICT)
            self.assertEqual(repository.current_removal("legacy-backup").operation_id, removal_id)
        finally:
            database.close()

    def test_partial_backup_refused_without_authority(self):
        installed = self.legacy()
        code, output, error = self.command(["skills", "adopt", "legacy-backup", "--yes", "--json"])
        self.assertEqual(code, 0, error)
        adoption_id = json.loads(output)["adoption_id"]
        consistent_db = self.root / "partial-state.db"
        archive = self.root / "partial-backup.tar.gz"
        self.sqlite_backup(consistent_db)
        self.assertEqual(self.public_backup(archive), 0)

        shutil.rmtree(installed)
        db_path = self.home / "state.db"
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(db_path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        shutil.copy2(consistent_db, db_path)
        self.assertFalse((self.home / "skills" / "legacy-backup").exists())

        code, output, error = self.command(
            [
                "skills",
                "unadopt",
                "legacy-backup",
                "--yes",
                "--expect-adoption",
                adoption_id,
                "--json",
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertTrue(error)
        self.assertFalse((self.home / "skills" / "legacy-backup").exists())
        code, output, error = self.command(["skills", "history", "--json"])
        self.assertEqual(code, 0, error)
        history = json.loads(output)
        self.assertEqual([record["action"] for record in history], ["adopt"])
        self.assertEqual(history[0]["adoption_id"], adoption_id)


if __name__ == "__main__":
    unittest.main()
