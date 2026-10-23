"""Backup integral preserva provas históricas sem reautorizar cópias restauradas."""

import argparse
import asyncio
import fcntl
import io
import multiprocessing
import os
import shutil
import sqlite3
import tarfile
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from skill_publication_fixtures import canonical_rows
from test_skill_publication_files import capture
from test_skill_publication_service import Interrupted, PublicationServiceFixture

from kairos_cli.backup import run_backup
from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_skills.publication_contract import publication_inventory_hash
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import observe_quarantine_source
from kairos_skills.publications import SkillPublicationService
from kairos_state import connect
from kairos_state.migrations import CANONICAL_TABLES, repair_derived_objects
from kairos_state.repositories.skill_publications import SkillPublicationRepository


def paused_backup(home, archive, pipe):
    original = tarfile.TarFile.add

    def pause(tar, path, *args, **kwargs):
        result = original(tar, path, *args, **kwargs)
        if kwargs.get("arcname") == "state.db":
            pipe.send("snapshot_ready")
            pipe.recv()
        return result

    with patch.object(tarfile.TarFile, "add", new=pause):
        code = asyncio.run(run_backup(home, argparse.Namespace(output=str(archive), json=True)))
    pipe.send(code)
    pipe.close()


def waiting_creation(home, source, pipe):
    from kairos_skills.mutations import SkillMutationService
    from kairos_state.repositories.skill_mutations import SkillMutationRepository

    with (
        closing(connect(home / "state.db")) as connection,
        observe_org_policy(OrgPolicyContext("none", None), home) as policy,
    ):
        repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
        pipe.send("starting")
        result = SkillMutationService(home, repository, policy=policy).add(
            source, actor=Actor.USER_FOREGROUND
        )
        pipe.send(result.state.value)
    pipe.close()


def rows(connection):
    present = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return canonical_rows(connection, sorted(present & CANONICAL_TABLES))


class PublicationBackupTests(PublicationServiceFixture):
    def test_v11_database_created_after_absence_decision_restarts_locked_capture(self):
        from kairos_state import initialize_schema

        late = self.home / "z-after-state.txt"
        late.write_bytes(b"preserved")
        original = tarfile.TarFile.add
        created = False

        def add(tar, path, *args, **kwargs):
            nonlocal created
            result = original(tar, path, *args, **kwargs)
            if Path(path) == late and not created:
                with (
                    skill_mutation_lock(self.home),
                    closing(connect(self.home / "state.db")) as connection,
                ):
                    initialize_schema(connection)
                created = True
            return result

        archive = self.root / "backup.tar.gz"
        with patch.object(tarfile.TarFile, "add", new=add):
            self.assertEqual(self.backup(archive)[0], 0)
        self.assertTrue(created)
        restored = self.restored(archive)
        self.assertEqual((restored / late.name).read_bytes(), b"preserved")
        with closing(sqlite3.connect(restored / "state.db")) as connection:
            self.assertEqual(
                connection.execute("SELECT version FROM schema_version").fetchone()[0], 11
            )

    def test_initialized_v11_without_skills_can_be_backed_up_without_creating_them(self):
        from kairos_state import initialize_schema

        with closing(connect(self.home / "state.db")) as connection:
            initialize_schema(connection)
            before = rows(connection)
        self.assertFalse((self.home / "skills").exists())
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        self.assertFalse((self.home / "skills").exists())
        with tarfile.open(archive) as tar:
            self.assertIn("state.db", tar.getnames())
        restored = self.restored(archive)
        with closing(sqlite3.connect(restored / "state.db")) as connection:
            self.assertEqual(rows(connection), before)

    def test_backup_restarts_under_lock_when_v11_appears_during_snapshot(self):
        from kairos_cli.backup import _snapshot_database
        from kairos_state.migrations import migrate
        from kairos_state.repositories.skill_mutations import validate_skill_timeline

        with closing(connect(self.home / "state.db")) as connection:
            migrate(connection, target=10)
        migrated = False

        def snapshot(*args, **kwargs):
            nonlocal migrated
            if not migrated:
                with (
                    skill_mutation_lock(self.home),
                    closing(connect(self.home / "state.db")) as connection,
                ):
                    migrate(connection, target=11)
                migrated = True
            return _snapshot_database(*args, **kwargs)

        def timeline(connection):
            if connection.execute("SELECT version FROM schema_version").fetchone()[0] == 11:
                fd = os.open(self.home / ".skills-write.lock", os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            return validate_skill_timeline(connection)

        archive = self.root / "backup.tar.gz"
        with (
            patch("kairos_cli.backup._snapshot_database", side_effect=snapshot),
            patch(
                "kairos_state.repositories.skill_mutations.validate_skill_timeline",
                side_effect=timeline,
            ),
        ):
            self.assertEqual(self.backup(archive)[0], 0)
        self.assertTrue(migrated)
        with closing(sqlite3.connect(self.restored(archive) / "state.db")) as connection:
            self.assertEqual(
                connection.execute("SELECT version FROM schema_version").fetchone()[0], 11
            )

    def test_published_tree_in_pending_removal_is_backed_up_at_known_retired_id(self):
        from kairos_skills.adoptions import SkillAdoptionService
        from kairos_skills.mutations import SkillMutationService
        from kairos_skills.publication_backup import publication_backup_inventory
        from kairos_skills.removal_io import SkillRemovalFiles
        from kairos_state.repositories.skill_mutations import SkillMutationRepository

        self.start(replacement=True)
        publication = self.publish()
        self.assertTrue(publication.complete)
        self.live.close()
        with (
            closing(connect(self.home / "state.db")) as connection,
            observe_org_policy(OrgPolicyContext("none", None), self.home) as policy,
        ):
            common = SkillMutationRepository(connection, home_id=catalog_home_id(self.home))
            SkillAdoptionService(self.home, common, policy=policy).adopt(
                self.name, actor=Actor.USER_FOREGROUND, confirmed=True
            )
            original = SkillRemovalFiles.retire

            def interrupt(files, *args, **kwargs):
                original(files, *args, **kwargs)
                raise Interrupted

            with (
                patch.object(SkillRemovalFiles, "retire", new=interrupt),
                self.assertRaises(Interrupted),
            ):
                SkillMutationService(self.home, common, policy=policy).remove(
                    self.name, actor=Actor.USER_FOREGROUND, confirmed=True
                )
            removal = common.pending(self.name)[0]
            self.canonical = rows(connection)
            with skill_mutation_lock(self.home):
                inventory = publication_backup_inventory(
                    self.home, SkillPublicationRepository(connection, home_id=common.home_id)
                )
        expected = f".skill-mutations/retired/{removal.operation_id}"
        self.assertIn(expected, {entry.relative_path for entry in inventory})
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        restored = self.restored(archive)
        self.assertEqual((restored / expected / "SKILL.md").read_bytes(), b"new version")
        self.assertFalse((restored / "skills" / self.name).exists())

    def test_aborted_initial_publication_restores_proved_absence(self):
        from kairos_skills.publication_backup import validate_restored_publication

        session = self.start()
        record = self.stop_after(session.publications, "prepare")
        result = self.service.recover(record.operation_id)
        self.assertIs(result.state, SkillMutationState.ABORTED)
        record = session.publications.get(record.operation_id)
        self.canonical = rows(session.connection)
        self.live.close()
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        restored = self.restored(archive)
        with observe_org_policy(OrgPolicyContext("none", None), restored) as policy:
            observation = validate_restored_publication(restored, record, policy=policy)
            self.assertTrue(observation.inventory_valid)
            self.assertFalse(observation.identity_valid)
        self.assertFalse((restored / "skills" / self.name).exists())

    def test_backup_holds_common_lock_for_trees_and_database(self):
        from skill_mutation_fixtures import creation_text

        self.stopped("publish")
        archive = self.root / "backup.tar.gz"
        source = self.root / "new-skill.md"
        source.write_text(creation_text(name="new-skill"))
        context = multiprocessing.get_context("spawn")
        backup_parent, backup_child = context.Pipe()
        writer_parent, writer_child = context.Pipe()
        backup = context.Process(target=paused_backup, args=(self.home, archive, backup_child))
        writer = context.Process(target=waiting_creation, args=(self.home, source, writer_child))
        backup.start()
        try:
            self.assertTrue(backup_parent.poll(15))
            self.assertEqual(backup_parent.recv(), "snapshot_ready")
            writer.start()
            self.assertTrue(writer_parent.poll(15))
            self.assertEqual(writer_parent.recv(), "starting")
            self.assertFalse(writer_parent.poll(0.2))
            self.assertFalse((self.home / "skills/new-skill").exists())
            backup_parent.send("continue")
            self.assertTrue(backup_parent.poll(15))
            self.assertEqual(backup_parent.recv(), 0)
            self.assertTrue(writer_parent.poll(15))
            self.assertEqual(writer_parent.recv(), "committed")
            for process in (backup, writer):
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            restored = self.restored(archive)
            self.assertFalse((restored / "skills/new-skill").exists())
            with closing(sqlite3.connect(restored / "state.db")) as connection:
                self.assertEqual(rows(connection), self.canonical)
        finally:
            for process in (backup, writer):
                if process.is_alive():
                    process.kill()
                if process.pid is not None:
                    process.join(5)
            for pipe in (backup_parent, backup_child, writer_parent, writer_child):
                pipe.close()

    def test_archive_sync_failure_preserves_previous_output(self):
        self.stopped("publish")
        archive = self.root / "backup.tar.gz"
        archive.write_bytes(b"previous archive sentinel")
        with (
            self.assertLogs("kairos_cli.backup", level="WARNING") as logs,
            patch("kairos_cli.backup.os.fsync", side_effect=OSError("private fsync failure")),
        ):
            code, _out, error = self.backup(archive)
        self.assertNotEqual(code, 0)
        self.assertNotIn("private fsync failure", error)
        self.assertEqual(archive.read_bytes(), b"previous archive sentinel")
        self.assertEqual(logs.records[-1].kind, "io")
        self.assertNotIn("private fsync failure", repr(logs.records[-1].__dict__))

    def test_archive_rejects_snapshot_changed_during_copy(self):
        record = self.stopped("publish")
        archive = self.root / "backup.tar.gz"
        archive.write_bytes(b"previous archive sentinel")
        previous = self.home / ".skill-publications" / record.operation_id / "previous/SKILL.md"
        original = tarfile.TarFile.add

        def change(tar, path, *args, **kwargs):
            if Path(path) == previous:
                previous.write_bytes(b"changed snapshot")
            return original(tar, path, *args, **kwargs)

        with patch.object(tarfile.TarFile, "add", new=change):
            self.assertNotEqual(self.backup(archive)[0], 0)
        self.assertEqual(archive.read_bytes(), b"previous archive sentinel")
        self.assertEqual(previous.read_bytes(), b"changed snapshot")

    def stopped(self, point="publish"):
        session = self.start(replacement=True)
        owner = session.publications if point in {"prepare", "finish"} else session.files
        record = self.stop_after(owner, point)
        self.canonical = rows(session.connection)
        self.live.close()
        return record

    def backup(self, output, *, home=None):
        out, error = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(error):
            code = asyncio.run(
                run_backup(home or self.home, argparse.Namespace(output=str(output), json=True))
            )
        self.assertNotIn(str(self.quarantine), error.getvalue())
        return code, out.getvalue(), error.getvalue()

    def restored(self, archive, *, database_only=False):
        restored = self.root / "restored"
        restored.mkdir(mode=0o700)
        with tarfile.open(archive) as tar:
            members = [tar.getmember("state.db")] if database_only else tar.getmembers()
            tar.extractall(restored, members=members, filter="data")
            for member in members:
                safe = tarfile.data_filter(member, str(restored))
                if safe is not None and (safe.isdir() or safe.isfile()):
                    (restored / safe.name).chmod(member.mode & 0o777)
        return restored

    def test_backup_covers_all_referenced_artifacts(self):
        from kairos_skills.publication_backup import publication_backup_inventory

        record = self.stopped("retire")
        with (
            closing(sqlite3.connect(self.home / "state.db")) as connection,
            skill_mutation_lock(self.home),
        ):
            repository = SkillPublicationRepository(connection, home_id=record.home_id)
            inventory = publication_backup_inventory(self.home, repository)
        paths = {entry.relative_path for entry in inventory}
        area = f".skill-publications/{record.operation_id}"
        self.assertEqual(paths, {f"{area}/staged", f"{area}/previous"})
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        restored = self.restored(archive)
        for entry in inventory:
            actual = capture(restored / entry.relative_path)
            self.assertEqual(
                publication_inventory_hash(actual), publication_inventory_hash(entry.proof)
            )
            self.assertNotEqual(actual.entries[0].inode, entry.proof.entries[0].inode)
        self.assertFalse((restored / "skills" / self.name).exists())
        self.assertFalse((restored / "quarantine").exists())
        with closing(sqlite3.connect(restored / "state.db")) as connection:
            self.assertEqual(rows(connection), self.canonical)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_missing_artifact_preserves_previous_archive(self):
        record = self.stopped("publish")
        previous = self.home / ".skill-publications" / record.operation_id / "previous"
        shutil.rmtree(previous)
        archive = self.root / "backup.tar.gz"
        archive.write_bytes(b"previous archive sentinel")
        code, _out, error = self.backup(archive)
        self.assertNotEqual(code, 0)
        self.assertTrue(error)
        self.assertEqual(archive.read_bytes(), b"previous archive sentinel")

    def test_restore_new_inodes_has_no_new_authority(self):
        from kairos_skills.publication_backup import validate_restored_publication

        record = self.stopped("finish")
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        restored = self.restored(archive)
        before = sorted(str(path.relative_to(restored)) for path in restored.rglob("*"))
        with observe_org_policy(OrgPolicyContext("none", None), restored) as policy:
            observation = validate_restored_publication(restored, record, policy=policy)
            self.assertTrue(observation.inventory_valid)
            self.assertFalse(observation.identity_valid)
            self.assertEqual(observation.observed_home_id, catalog_home_id(restored))
            with self.assertRaises(SkillMutationError):
                identify_skill(restored, self.name, policy=policy)
        with closing(sqlite3.connect(restored / "state.db")) as connection:
            self.assertEqual(rows(connection), self.canonical)
            historical = SkillPublicationRepository(connection, home_id=record.home_id).get(
                record.operation_id
            )
            self.assertTrue(historical.pending)
            self.assertFalse(historical.cleanup_complete)
        self.assertEqual(
            sorted(str(path.relative_to(restored)) for path in restored.rglob("*")), before
        )

    def test_database_only_restore_blocks_recovery(self):
        from kairos_skills.publication_backup import validate_restored_publication

        record = self.stopped("publish")
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        restored = self.restored(archive, database_only=True)
        with observe_org_policy(OrgPolicyContext("none", None), restored) as policy:
            observation = validate_restored_publication(restored, record, policy=policy)
            self.assertFalse(observation.inventory_valid)
            self.assertFalse(observation.identity_valid)
            self.assertEqual(observation.present_artifacts, ())
            with (
                observe_quarantine_source(
                    restored,
                    self.quarantine,
                    source_key=self.source_key,
                    actor=Actor.USER_FOREGROUND,
                    policy=policy,
                    replacement=record.draft.previous_tree,
                ) as context,
                publication_session(restored, policy=policy, context=context) as session,
                self.assertRaises(SkillMutationError),
            ):
                SkillPublicationService(session).recover(record.operation_id)
        self.assertFalse((restored / "skills" / self.name).exists())
        with closing(sqlite3.connect(restored / "state.db")) as connection:
            self.assertEqual(rows(connection), self.canonical)

    def test_foreign_database_cannot_hide_references_from_backup(self):
        self.stopped("publish")
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        restored = self.restored(archive, database_only=True)
        with (
            observe_org_policy(OrgPolicyContext("none", None), restored) as policy,
            observe_quarantine_source(
                restored,
                self.quarantine,
                source_key=self.source_key,
                actor=Actor.USER_FOREGROUND,
                policy=policy,
            ) as context,
            publication_session(restored, policy=policy, context=context),
        ):
            pass
        self.assertTrue((restored / "skills").is_dir())
        output = self.root / "second-backup.tar.gz"
        output.write_bytes(b"previous complete archive")
        self.assertNotEqual(self.backup(output, home=restored)[0], 0)
        self.assertEqual(output.read_bytes(), b"previous complete archive")

    def test_repair_does_not_complete_pending_publication(self):
        record = self.stopped("publish")
        with closing(connect(self.home / "state.db")) as connection:
            connection.execute("DROP TRIGGER skill_acquisition_receipts_writer")
            repaired = repair_derived_objects(connection)
            self.assertIn("skill_acquisition_receipts_writer", repaired)
            self.assertEqual(rows(connection), self.canonical)
            repository = SkillPublicationRepository(connection, home_id=record.home_id)
            self.assertIs(repository.get(record.operation_id).state, SkillMutationState.PREPARED)
            self.assertEqual(len(repository.pending(self.name)), 1)

    def test_same_home_new_inodes_remain_pending_without_cleanup_authority(self):
        from kairos_skills.publication_backup import validate_restored_publication

        record = self.stopped("finish")
        archive = self.root / "backup.tar.gz"
        self.assertEqual(self.backup(archive)[0], 0)
        restored = self.restored(archive)
        shutil.rmtree(self.home)
        restored.rename(self.home)
        installed = capture(self.home / "skills" / self.name)
        with observe_org_policy(OrgPolicyContext("none", None), self.home) as policy:
            observation = validate_restored_publication(self.home, record, policy=policy)
            self.assertTrue(observation.inventory_valid)
            self.assertFalse(observation.identity_valid)
            self.assertFalse(identify_skill(self.home, self.name, policy=policy).removivel)
            with (
                observe_quarantine_source(
                    self.home,
                    self.quarantine,
                    source_key=self.source_key,
                    actor=Actor.USER_FOREGROUND,
                    policy=policy,
                    replacement=record.draft.previous_tree,
                ) as context,
                publication_session(self.home, policy=policy, context=context) as session,
            ):
                result = SkillPublicationService(session).recover(record.operation_id)
                self.assertFalse(result.complete)
                self.assertFalse(result.cleanup_complete)
                self.assertEqual(rows(session.connection), self.canonical)
        self.assertEqual(capture(self.home / "skills" / self.name), installed)
        self.assertTrue(self.source.exists())
