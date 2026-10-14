"""Autoridade revogável, migração atômica e downgrade real do journal."""

import dataclasses
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from skill_mutation_fixtures import creation_text
from test_skill_mutation_state import creation_draft

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture, TreeEntry
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_skills.removal_state import read_skill_repository
from kairos_state import connect
from kairos_state.migrations import CANONICAL_TABLES, migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_removals import SkillRemovalRepository


def capture(inode=2):
    contents = {"SKILL.md": b"# Legacy without semver\n", "run.bin": b"\x00\xff"}
    entries = [TreeEntry("", "directory", 0o700, 0, None, 1, inode, 3)]
    for index, (name, data) in enumerate(contents.items()):
        entries.append(
            TreeEntry(
                name, "file", 0o600, len(data), hashlib.sha256(data).hexdigest(), 1, index + 10, 3
            )
        )
    return TreeCapture(tuple(sorted(entries, key=lambda entry: entry.path)), contents)


def canonical_rows(connection):
    names = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    result = {}
    for name in sorted(names & (CANONICAL_TABLES | {"schema_version", "sqlite_sequence"})):
        query = 'SELECT * FROM "' + name + '" ORDER BY rowid'  # noqa: S608 — noms limitados às tabelas canônicas constantes
        result[name] = tuple(tuple(row) for row in connection.execute(query))
    return result


def mutate_rows_without_guard(connection, trigger_name, statement, parameters=()):
    with connection:
        trigger = connection.execute(
            "SELECT tbl_name,sql FROM sqlite_master WHERE type='trigger' AND name=?",
            (trigger_name,),
        ).fetchone()
        if trigger is None:
            raise AssertionError(f"trigger de teste ausente: {trigger_name}")
        table_name, trigger_sql = trigger
        quoted_name = '"' + trigger_name.replace('"', '""') + '"'
        connection.execute(f"DROP TRIGGER {quoted_name}")
        try:
            connection.execute(statement, parameters)
        finally:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
            ).fetchone()
            if exists is not None:
                connection.execute(trigger_sql)


def installed_inventory(home):
    inventory = []
    for path in sorted(home.rglob("*")):
        if path.name in ("state.db", "state.db-wal", "state.db-shm", "state.db-journal"):
            continue
        info = path.lstat()
        inventory.append(
            (
                str(path.relative_to(home)),
                stat.S_IMODE(info.st_mode),
                info.st_dev,
                info.st_ino,
                path.read_bytes() if path.is_file() else None,
            )
        )
    return tuple(inventory)


class AdoptionStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db, target=8)
        self.home_id = catalog_home_id(self.home)
        self.common = SkillMutationRepository(self.db, home_id=self.home_id)

    def modern(self):
        from kairos_state.repositories.skill_adoptions import SkillAdoptionRepository

        migrate(self.db)
        return SkillAdoptionRepository(self.db, home_id=self.home_id)

    def adopt_draft(self, *, name="legacy", installation_id=None, inode=2):
        from kairos_skills.adoption_contract import SkillAdoptionAction, SkillAdoptionDraft

        operation_id = uuid.uuid4().hex
        return SkillAdoptionDraft(
            operation_id,
            self.home_id,
            SkillAdoptionAction.ADOPT,
            name,
            Actor.USER_FOREGROUND,
            1.0,
            operation_id,
            installation_id or operation_id,
            1,
            inode,
            "unknown",
            operation_id,
        )

    def unadopt_draft(self, adoption):
        from kairos_skills.adoption_contract import SkillAdoptionAction

        proof = self.common.current_installation(adoption.name)
        return dataclasses.replace(
            adoption.draft,
            operation_id=uuid.uuid4().hex,
            action=SkillAdoptionAction.UNADOPT,
            installation_id=proof.operation_id,
            device=proof.device,
            inode=proof.inode,
        )

    def adopt(self, repository, **changes):
        draft = self.adopt_draft(**changes)
        repository.prepare_adopt(draft, capture(draft.inode))
        return self.common.finish(draft.operation_id, SkillMutationState.COMMITTED)

    def tree_draft(self, *, name="bundle", provenance=Provenance.BUNDLED, **changes):
        operation_id = uuid.uuid4().hex
        return dataclasses.replace(
            SkillTreeDraft(
                operation_id,
                self.home_id,
                SkillTreeAction.REMOVE,
                name,
                Actor.USER_FOREGROUND,
                1.0,
                provenance,
                capture(),
                operation_id,
            ),
            **changes,
        )

    def seed_v8(self):
        creation = creation_draft(self.home)
        self.common.prepare(creation, creation_text())
        self.common.finish(creation.operation_id, SkillMutationState.COMMITTED)
        tree = SkillRemovalRepository(self.db, home_id=self.home_id)
        draft = self.tree_draft()
        tree.prepare_remove(draft, capture())
        tree.finish(draft.operation_id, SkillMutationState.COMMITTED)
        return creation, draft

    def test_migration_preserves_old_records_and_blobs(self):
        creation, removed = self.seed_v8()
        before = canonical_rows(self.db)
        schemas = tuple(
            self.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        content = self.common.content(creation.operation_id)
        snapshot = self.common.snapshot(removed.operation_id)
        migrate(self.db)
        self.assertIn("skill_authority_timeline", canonical_rows(self.db))
        for name, rows in before.items():
            if name not in ("schema_version", "sqlite_sequence"):
                self.assertEqual(canonical_rows(self.db)[name], rows, name)
        for name, sql in schemas:
            self.assertEqual(
                self.db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[
                    0
                ],
                sql,
            )
        self.assertEqual(self.common.content(creation.operation_id), content)
        self.assertEqual(self.common.snapshot(removed.operation_id), snapshot)
        migrated = canonical_rows(self.db)
        migrate(self.db)
        self.assertEqual(canonical_rows(self.db), migrated)

    def test_global_event_order(self):
        self.seed_v8()
        adoption = self.modern()
        prefix = list(self.common._timeline())
        draft = self.adopt_draft()
        adoption.prepare_adopt(draft, capture())
        # A segunda criação usa outro nome válido e conteúdo correspondente.
        from kairos_skills.mutation_contract import validate_skill_creation

        text = creation_text(name="second")
        creation = creation_draft(self.home, validate_skill_creation(text))
        self.common.prepare(creation, text)
        removed = self.tree_draft(name="other-bundle")
        self.common.prepare_remove(removed, capture())
        self.common.finish(draft.operation_id, SkillMutationState.COMMITTED)
        self.common.finish(removed.operation_id, SkillMutationState.COMMITTED)
        self.common.finish(creation.operation_id, SkillMutationState.COMMITTED)
        expected = [
            (draft.operation_id, "prepared"),
            (creation.operation_id, "prepared"),
            (removed.operation_id, "prepared"),
            (draft.operation_id, "committed"),
            (removed.operation_id, "committed"),
            (creation.operation_id, "committed"),
        ]
        timeline = self.common._timeline()
        self.assertEqual(timeline[: len(prefix)], prefix)
        self.assertEqual([(row[3], row[4]) for row in timeline[len(prefix) :]], expected)
        self.assertEqual(
            [record.operation_id for record in self.common.history(limit=3)],
            [removed.operation_id, creation.operation_id, draft.operation_id],
        )

    def test_v8_timeline_frozen_after_migration(self):
        self.seed_v8()
        before = tuple(
            tuple(row) for row in self.db.execute("SELECT * FROM skill_mutation_timeline")
        )
        repository = self.modern()
        self.adopt(repository)
        self.assertEqual(
            tuple(tuple(row) for row in self.db.execute("SELECT * FROM skill_mutation_timeline")),
            before,
        )
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("INSERT INTO skill_mutation_timeline(legacy_event) VALUES (999)")

    def test_migration_interruption_is_atomic(self):
        self.seed_v8()
        before = canonical_rows(self.db)
        code = """
import os, sys
from kairos_state import connect
from kairos_state.migrations import migrate
db = connect(sys.argv[1])
def interrupt(statement):
    if 'UPDATE schema_version SET version = 9' in statement:
        os._exit(91)
db.set_trace_callback(interrupt)
migrate(db)
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.home / "state.db")],
            check=False,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 91, result.stderr)
        self.assertEqual(canonical_rows(self.db), before)
        self.assertFalse(
            self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='skill_authority_timeline'"
            ).fetchone()
        )
        self.modern()
        self.assertIsNotNone(read_skill_repository(self.db, self.home))

    def test_event_and_global_position_atomic(self):
        repository = self.modern()
        before = canonical_rows(self.db)
        self.db.execute(
            "CREATE TRIGGER reject_position BEFORE INSERT ON skill_authority_timeline BEGIN SELECT RAISE(ABORT,'position unavailable'); END"
        )
        with self.assertRaises(SkillMutationError):
            repository.prepare_adopt(self.adopt_draft(), capture())
        self.assertEqual(canonical_rows(self.db), before)

    def test_adoption_revocation_projection(self):
        repository = self.modern()
        draft = self.adopt_draft()
        repository.prepare_adopt(draft, capture())
        self.assertIsNone(self.common.current_installation(draft.name))
        adoption = self.common.finish(draft.operation_id, SkillMutationState.COMMITTED)
        evidence = self.common.installation_evidence(draft.name)
        self.assertEqual(evidence.adoption.adoption_id, draft.operation_id)
        self.assertEqual(evidence.adoption.installation_id, draft.installation_id)
        self.assertTrue(evidence.adoption.active)
        self.assertIsNone(evidence.historical_creation_id)
        before_snapshot = self.common.snapshot(adoption.operation_id)
        revoked = repository.commit_unadopt(self.unadopt_draft(adoption))
        after = self.common.installation_evidence(draft.name)
        self.assertEqual(after.current.operation_id, evidence.current.operation_id)
        self.assertEqual(after.current.adoption, after.adoption)
        self.assertFalse(after.adoption.active)
        self.assertEqual(self.common.snapshot(revoked.operation_id), before_snapshot)
        self.assertEqual(self.common.get(adoption.operation_id), adoption)
        self.assertEqual(self.common.history(), (revoked, adoption))
        second = self.adopt(repository, installation_id=draft.installation_id)
        self.assertEqual(
            self.common.installation_evidence(draft.name).adoption.adoption_id, second.operation_id
        )
        self.assertEqual(self.common.snapshot(adoption.operation_id), before_snapshot)

    def test_corrupt_or_missing_lineage_refused(self):
        repository = self.modern()
        adoption = self.adopt(repository)
        removed = self.tree_draft(
            name=adoption.name,
            provenance=Provenance.USER,
            adoption=self.common.current_installation(adoption.name).adoption,
            installation_source="unknown",
        )
        self.common.prepare_remove(removed, capture())
        self.common.finish(removed.operation_id, SkillMutationState.COMMITTED)
        restore_id = uuid.uuid4().hex
        restored = dataclasses.replace(
            removed,
            operation_id=restore_id,
            action=SkillTreeAction.RESTORE,
            reverts=removed.operation_id,
            proof=capture(22),
            adoption=dataclasses.replace(removed.adoption, installation_id=restore_id),
        )
        self.common.prepare_restore(restored)
        self.common.finish(restored.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(
            self.common.installation_evidence(adoption.name).adoption.adoption_id,
            adoption.operation_id,
        )
        repository.commit_unadopt(self.unadopt_draft(adoption))
        self.assertFalse(self.common.current_installation(adoption.name).adoption.active)
        for sql in (
            (
                "skill_tree_adoptions_no_delete",
                "DELETE FROM skill_tree_adoptions WHERE operation_id=?",
            ),
            (
                "skill_tree_adoptions_no_update",
                "UPDATE skill_tree_adoptions SET adoption_id=NULL,installation_id=NULL,installation_source=NULL WHERE operation_id=?",
            ),
        ):
            with self.subTest(sql=sql):
                copied = sqlite3.connect(":memory:")
                self.addCleanup(copied.close)
                self.db.backup(copied)
                mutate_rows_without_guard(copied, sql[0], sql[1], (removed.operation_id,))
                corrupted = SkillMutationRepository(copied, home_id=self.home_id)
                with self.assertRaises(SkillMutationError) as caught:
                    corrupted.installation_evidence(adoption.name)
                self.assertEqual(caught.exception.kind, "corrupt")

    def test_all_family_pending_blocks_writer(self):
        repository = self.modern()
        draft = self.adopt_draft()
        repository.prepare_adopt(draft, capture())
        self.assertEqual(self.common.pending(draft.name)[0].operation_id, draft.operation_id)
        before = canonical_rows(self.db)
        with self.assertRaises(SkillMutationError):
            repository.prepare_adopt(self.adopt_draft(), capture())
        with self.assertRaises(SkillMutationError):
            self.common.prepare_remove(self.tree_draft(name=draft.name), capture())
        from kairos_skills.mutation_contract import validate_skill_creation

        text = creation_text(name=draft.name)
        with self.assertRaises(SkillMutationError):
            self.common.prepare(creation_draft(self.home, validate_skill_creation(text)), text)
        self.assertEqual(canonical_rows(self.db), before)

    def test_partial_schema_refused_without_reimport(self):
        self.modern()
        self.db.execute("DROP TABLE skill_tree_adoptions")
        with self.assertRaises(SkillMutationError):
            read_skill_repository(self.db, self.home)
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(self.db)

    def test_migration_refuses_incomplete_v8_prefix(self):
        self.seed_v8()
        mutate_rows_without_guard(
            self.db,
            "skill_mutation_timeline_no_delete",
            "DELETE FROM skill_mutation_timeline WHERE tree_event IS NOT NULL",
        )
        before = canonical_rows(self.db)
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(self.db)
        self.assertEqual(canonical_rows(self.db), before)

    def test_new_writer_refuses_missing_guards_before_appending(self):
        repository = self.modern()
        self.db.execute("DROP TRIGGER skill_adoption_events_timeline")
        before = canonical_rows(self.db)
        with self.assertRaises(SkillMutationError):
            repository.prepare_adopt(self.adopt_draft(), capture())
        self.assertEqual(canonical_rows(self.db), before)

    def _assert_schema9_guard_corruption_refused(self, repository):
        before = canonical_rows(self.db)
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(self.db)
        with self.assertRaises(SkillMutationError) as caught:
            read_skill_repository(self.db, self.home)
        self.assertEqual(caught.exception.kind, "corrupt")
        with self.assertRaises(SkillMutationError):
            repository.prepare_adopt(self.adopt_draft(), capture())
        self.assertEqual(canonical_rows(self.db), before)

    def test_missing_append_only_guard_blocks_migration_read_and_write(self):
        repository = self.modern()
        self.db.execute("DROP TRIGGER skill_adoption_operations_no_update")
        self._assert_schema9_guard_corruption_refused(repository)

    def test_missing_capability_guard_blocks_migration_read_and_write(self):
        repository = self.modern()
        self.db.execute("DROP TRIGGER skill_adoption_events_writer")
        self._assert_schema9_guard_corruption_refused(repository)

    def test_missing_routing_guard_blocks_migration_read_and_write(self):
        repository = self.modern()
        self.db.execute("DROP TRIGGER skill_adoption_events_timeline")
        self._assert_schema9_guard_corruption_refused(repository)

    def test_permissive_same_name_append_only_guard_blocks_migration_read_and_write(self):
        repository = self.modern()
        self.db.executescript(
            """
            DROP TRIGGER skill_adoption_operations_no_update;
            CREATE TRIGGER skill_adoption_operations_no_update
            BEFORE UPDATE ON skill_adoption_operations BEGIN SELECT 1; END;
            """
        )
        self._assert_schema9_guard_corruption_refused(repository)

    def test_permissive_same_name_capability_guard_blocks_migration_read_and_write(self):
        repository = self.modern()
        self.db.executescript(
            """
            DROP TRIGGER skill_adoption_events_writer;
            CREATE TRIGGER skill_adoption_events_writer
            BEFORE INSERT ON skill_adoption_events BEGIN SELECT 1; END;
            """
        )
        self._assert_schema9_guard_corruption_refused(repository)

    def test_permissive_same_name_routing_guard_blocks_migration_read_and_write(self):
        repository = self.modern()
        self.db.executescript(
            """
            DROP TRIGGER skill_adoption_events_timeline;
            CREATE TRIGGER skill_adoption_events_timeline
            AFTER INSERT ON skill_adoption_events BEGIN SELECT 1; END;
            """
        )
        self._assert_schema9_guard_corruption_refused(repository)

    def test_imported_prefix_refuses_holes_in_frozen_history(self):
        self.seed_v8()
        repository = self.modern()
        self.adopt(repository)
        positions = [
            row[0]
            for row in self.db.execute(
                "SELECT sequence FROM skill_mutation_timeline ORDER BY sequence"
            )
        ]
        for missing in ((positions[0],), (positions[1],), (positions[-1],), tuple(positions)):
            with self.subTest(missing=missing):
                copied = sqlite3.connect(":memory:")
                copied.row_factory = sqlite3.Row
                self.addCleanup(copied.close)
                self.db.backup(copied)
                for position in missing:
                    mutate_rows_without_guard(
                        copied,
                        "skill_mutation_timeline_no_delete",
                        "DELETE FROM skill_mutation_timeline WHERE sequence=?",
                        (position,),
                    )
                before = canonical_rows(copied)
                with self.assertRaises(SkillMutationError):
                    read_skill_repository(copied, self.home)
                with self.assertRaises(sqlite3.DatabaseError):
                    migrate(copied)
                self.assertEqual(canonical_rows(copied), before)

    def test_imported_boundary_is_canonical_immutable_and_required(self):
        from kairos_state.migrations import canonical_fingerprint

        self.seed_v8()
        boundary = self.db.execute("SELECT MAX(sequence) FROM skill_mutation_timeline").fetchone()[
            0
        ]
        self.modern()
        self.assertIn("skill_authority_migration", canonical_fingerprint(self.db))
        self.assertEqual(
            tuple(
                self.db.execute(
                    "SELECT singleton,prefix_last_sequence FROM skill_authority_migration"
                ).fetchone()
            ),
            (1, boundary),
        )
        before = canonical_rows(self.db)
        for statement in (
            "UPDATE skill_authority_migration SET prefix_last_sequence=0",
            "DELETE FROM skill_authority_migration",
            "INSERT OR REPLACE INTO skill_authority_migration SELECT * FROM skill_authority_migration",
            "INSERT INTO skill_authority_migration VALUES (2,0)",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError), self.db:
                    self.db.execute(statement)
                self.assertEqual(canonical_rows(self.db), before)
        for statement in (
            ("skill_authority_migration_no_delete", "DELETE FROM skill_authority_migration"),
            (
                "skill_authority_migration_no_update",
                "UPDATE skill_authority_migration SET prefix_last_sequence=0",
            ),
            (
                "skill_authority_migration_no_update",
                "UPDATE skill_authority_migration SET prefix_last_sequence=prefix_last_sequence+1",
            ),
            ("skill_authority_migration_no_delete", "DROP TABLE skill_authority_migration"),
        ):
            with self.subTest(corruption=statement):
                copied = sqlite3.connect(":memory:")
                copied.row_factory = sqlite3.Row
                self.addCleanup(copied.close)
                self.db.backup(copied)
                mutate_rows_without_guard(copied, statement[0], statement[1])
                corrupted = canonical_rows(copied)
                with self.assertRaises(SkillMutationError):
                    read_skill_repository(copied, self.home)
                with self.assertRaises(sqlite3.DatabaseError):
                    migrate(copied)
                self.assertEqual(canonical_rows(copied), corrupted)

    def test_empty_imported_prefix_has_fixed_boundary_before_new_events(self):
        repository = self.modern()
        before = tuple(
            self.db.execute(
                "SELECT singleton,prefix_last_sequence FROM skill_authority_migration"
            ).fetchone()
        )
        self.assertEqual(before, (1, 0))
        self.adopt(repository)
        self.assertEqual(
            tuple(
                self.db.execute(
                    "SELECT singleton,prefix_last_sequence FROM skill_authority_migration"
                ).fetchone()
            ),
            before,
        )
        self.assertIsNotNone(read_skill_repository(self.db, self.home))

    def test_adoption_snapshot_and_events_remain_append_only(self):
        repository = self.modern()
        adoption = self.adopt(repository)
        before = canonical_rows(self.db)
        statements = (
            "UPDATE skill_adoption_operations SET installation_source='local'",
            "DELETE FROM skill_adoption_operations",
            "UPDATE skill_adoption_events SET state='aborted'",
            "DELETE FROM skill_adoption_events",
            "INSERT OR REPLACE INTO skill_adoption_operations SELECT * FROM skill_adoption_operations",
            "INSERT OR REPLACE INTO skill_adoption_events SELECT * FROM skill_adoption_events",
            "INSERT OR REPLACE INTO skill_authority_timeline SELECT * FROM skill_authority_timeline",
        )
        for statement in statements:
            with (
                self.subTest(statement=statement),
                self.assertRaises(sqlite3.IntegrityError),
                self.db,
            ):
                self.db.execute(statement)
            self.assertEqual(canonical_rows(self.db), before)
        self.assertEqual(self.common.snapshot(adoption.operation_id), capture())

    def test_old_capability_and_absent_capability_cannot_append_any_family(self):
        repository = self.modern()
        adoption = self.adopt(repository)
        legacy = creation_draft(self.home)
        self.common.prepare(legacy, creation_text())
        removed = self.tree_draft()
        self.common.prepare_remove(removed, capture())
        statements = (
            (
                "INSERT INTO skill_adoption_events(operation_id,state) VALUES (?,'committed')",
                adoption.operation_id,
            ),
            (
                "INSERT INTO skill_tree_events(operation_id,state) VALUES (?,'committed')",
                removed.operation_id,
            ),
            (
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?,'committed')",
                legacy.operation_id,
            ),
        )
        before = canonical_rows(self.db)
        for capability in (None, 8):
            writer = sqlite3.connect(self.home / "state.db")
            self.addCleanup(writer.close)
            if capability is not None:
                writer.create_function("kairos_skill_writer_version", 0, lambda: 8)
            for statement, operation_id in statements:
                with (
                    self.subTest(capability=capability, statement=statement),
                    self.assertRaises(sqlite3.DatabaseError),
                    writer,
                ):
                    writer.execute(statement, (operation_id,))
            self.assertEqual(canonical_rows(self.db), before)

    def test_revocation_cannot_change_a_different_installation_or_adoption(self):
        repository = self.modern()
        adoption = self.adopt(repository)
        original = self.unadopt_draft(adoption)
        before = canonical_rows(self.db)
        for changes in (
            {"installation_id": uuid.uuid4().hex},
            {"inode": 99},
            {"installation_source": "local"},
        ):
            with self.subTest(changes=changes), self.assertRaises(SkillMutationError) as caught:
                repository.commit_unadopt(dataclasses.replace(original, **changes))
            self.assertEqual(caught.exception.kind, "conflict")
            self.assertEqual(canonical_rows(self.db), before)
        repository.commit_unadopt(original)
        second = self.adopt(repository, installation_id=adoption.operation_id)
        before = canonical_rows(self.db)
        with self.assertRaises(SkillMutationError):
            repository.commit_unadopt(dataclasses.replace(original, operation_id=uuid.uuid4().hex))
        self.assertEqual(canonical_rows(self.db), before)
        self.assertEqual(
            self.common.current_installation(adoption.name).adoption.adoption_id,
            second.operation_id,
        )

    def test_get_history_and_snapshot_refuse_corrupt_installation_lineage(self):
        repository = self.modern()
        adoption = self.adopt(repository)
        revoked = repository.commit_unadopt(self.unadopt_draft(adoption))
        mutate_rows_without_guard(
            self.db,
            "skill_adoption_operations_no_update",
            "UPDATE skill_adoption_operations SET installation_id=? WHERE operation_id=?",
            (uuid.uuid4().hex, revoked.operation_id),
        )
        for read in (
            lambda: self.common.get(revoked.operation_id),
            self.common.history,
            lambda: self.common.snapshot(revoked.operation_id),
        ):
            with self.subTest(read=read), self.assertRaises(SkillMutationError) as caught:
                read()
            self.assertEqual(caught.exception.kind, "corrupt")

    def test_preexisting_independent_grant_cannot_be_adopted_or_revoked(self):
        repository = self.modern()
        creation = creation_draft(self.home)
        self.common.prepare(creation, creation_text())
        self.common.finish(creation.operation_id, SkillMutationState.COMMITTED)
        before = canonical_rows(self.db)
        with self.assertRaises(SkillMutationError):
            repository.prepare_adopt(self.adopt_draft(name=creation.name), capture())
        self.assertEqual(canonical_rows(self.db), before)
        self.assertIsNone(self.common.current_installation(creation.name).adoption)

    def test_adoption_pending_blocks_sync_and_other_pending_families_block_adoption(self):
        from kairos_skills.removal_state import read_skill_tombstones

        repository = self.modern()
        adoption = self.adopt_draft()
        repository.prepare_adopt(adoption, capture())
        with self.assertRaises(SkillMutationError):
            read_skill_tombstones(self.home)
        self.common.finish(adoption.operation_id, SkillMutationState.ABORTED)
        self.assertIsNone(self.common.installation_evidence(adoption.name).adoption)
        for kind in ("tree", "legacy"):
            name = "bundle" if kind == "tree" else "revisar-docs"
            if kind == "tree":
                self.common.prepare_remove(self.tree_draft(name=name), capture())
            else:
                self.common.prepare(creation_draft(self.home), creation_text())
            before = canonical_rows(self.db)
            with self.subTest(kind=kind), self.assertRaises(SkillMutationError):
                repository.prepare_adopt(self.adopt_draft(name=name), capture())
            self.assertEqual(canonical_rows(self.db), before)

    def test_repair_and_backup_preserve_adoptions_and_frozen_prefix(self):
        from kairos_state.migrations import canonical_fingerprint, repair_derived_objects

        self.seed_v8()
        repository = self.modern()
        adoption = self.adopt(repository)
        revoked = repository.commit_unadopt(self.unadopt_draft(adoption))
        before = canonical_rows(self.db)
        self.assertIn("skill_adoption_operations", canonical_fingerprint(self.db))
        self.db.execute("DROP TRIGGER skill_adoption_operations_no_delete")
        repair_derived_objects(self.db)
        self.assertEqual(canonical_rows(self.db), before)
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("DELETE FROM skill_adoption_operations")
        backup = sqlite3.connect(":memory:")
        self.addCleanup(backup.close)
        self.db.backup(backup)
        restored = SkillMutationRepository(backup, home_id=self.home_id)
        self.assertEqual(restored.snapshot(revoked.operation_id), capture())
        self.assertFalse(restored.installation_evidence(adoption.name).adoption.active)
        self.assertEqual(canonical_rows(backup), before)

    def test_explicit_repair_replaces_permissive_same_name_guard_without_canonical_changes(self):
        from kairos_state.migrations import repair_derived_objects

        repository = self.modern()
        adoption = self.adopt(repository)
        before = canonical_rows(self.db)
        self.db.executescript(
            """
            DROP TRIGGER skill_adoption_operations_no_update;
            CREATE TRIGGER skill_adoption_operations_no_update
            BEFORE UPDATE ON skill_adoption_operations BEGIN SELECT 1; END;
            """
        )

        repair_derived_objects(self.db)

        self.assertEqual(canonical_rows(self.db), before)
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute(
                "UPDATE skill_adoption_operations SET installation_source='local' WHERE operation_id=?",
                (adoption.operation_id,),
            )
        self.assertEqual(canonical_rows(self.db), before)

    def test_explicit_repair_failure_rolls_back_guard_rebuild(self):
        from kairos_state.migrations import repair_derived_objects

        repository = self.modern()
        self.adopt(repository)
        self.db.executescript(
            """
            DROP TRIGGER skill_adoption_operations_no_update;
            CREATE TRIGGER skill_adoption_operations_no_update
            BEFORE UPDATE ON skill_adoption_operations BEGIN SELECT 1; END;
            """
        )
        guarded = (
            "skill_mutation_operations_no_update",
            "skill_tree_operations_writer",
            "skill_adoption_operations_no_update",
            "skill_adoption_events_writer",
            "skill_adoption_events_timeline",
        )
        before_triggers = {
            name: self.db.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
            ).fetchone()[0]
            for name in guarded
        }
        before_rows = canonical_rows(self.db)

        def deny_one_recreated_guard(action, name, *_):
            if action == sqlite3.SQLITE_CREATE_TRIGGER and name == "skill_adoption_events_writer":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.db.set_authorizer(deny_one_recreated_guard)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                repair_derived_objects(self.db)
        finally:
            self.db.set_authorizer(None)

        after_triggers = {
            name: self.db.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
            ).fetchone()[0]
            for name in guarded
        }
        self.assertEqual(after_triggers, before_triggers)
        self.assertEqual(canonical_rows(self.db), before_rows)
        with self.assertRaises(SkillMutationError):
            read_skill_repository(self.db, self.home)


class OldWriterTests(unittest.TestCase):
    def old_client(self):
        provenance_path = Path(
            os.environ.get(
                "KAIROS_SKILL_V8_PROVENANCE",
                ".superpowers/test-fixtures/skill-client-v8/provenance.json",
            )
        ).resolve()
        self.assertTrue(
            provenance_path.is_file(),
            "Fixture v8 ausente: execute scripts/prepare-skill-v8-client.sh",
        )
        provenance = json.loads(provenance_path.read_text())
        self.assertEqual(provenance["revision"], "bdd95025b50ab4a8391f1ef0a8f9eb31140e273e")
        self.assertEqual(
            hashlib.sha256(Path(provenance["wheel"]).read_bytes()).hexdigest(), provenance["sha256"]
        )
        python = provenance["python"]
        isolated = subprocess.run(
            [
                python,
                "-I",
                "-c",
                "import json, kairos_cli, kairos_state.schema as s; print(json.dumps([kairos_cli.__file__, s.SCHEMA_VERSION]))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        module, version = json.loads(isolated.stdout)
        self.assertEqual(version, 8)
        self.assertTrue(Path(module).is_relative_to(Path(python).parent.parent / "lib"))
        return python

    def test_old_writer_refused(self):
        python = self.old_client()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for action in ("add", "remove", "rollback", "restore"):
                for migrated in (False, True):
                    with self.subTest(action=action, migrated=migrated):
                        home = base / (action + str(migrated))
                        env = os.environ | {"KAIROS_HOME": str(home)}
                        source = base / "source.md"
                        source.write_text(creation_text())

                        def run(*args, env=env):
                            return subprocess.run(
                                [
                                    python,
                                    "-I",
                                    "-c",
                                    "import sys; from kairos_cli.main import main; sys.exit(main())",
                                    "skills",
                                    *args,
                                    "--json",
                                ],
                                env=env,
                                cwd=base,
                                capture_output=True,
                                text=True,
                                check=False,
                            )

                        added = run("add", "--file", str(source))
                        self.assertEqual(added.returncode, 0, added.stderr)
                        created_id = json.loads(added.stdout)["id"]
                        command = ("remove", "revisar-docs", "--yes")
                        if action == "add":
                            source.write_text(creation_text(name="new-skill"))
                            command = ("add", "--file", str(source))
                        elif action == "rollback":
                            command = ("rollback", created_id)
                        elif action == "restore":
                            removed = run(*command)
                            self.assertEqual(removed.returncode, 0, removed.stderr)
                            command = ("rollback", json.loads(removed.stdout)["id"])
                        database = connect(home / "state.db")
                        try:
                            if migrated:
                                migrate(database)
                            before = canonical_rows(database), installed_inventory(home)
                            result = run(*command)
                            if migrated:
                                self.assertNotEqual(result.returncode, 0)
                                self.assertRegex(
                                    result.stderr,
                                    "incompatível|segurança|não suportado|Ledger de skills incoerente",
                                )
                                self.assertEqual(
                                    (canonical_rows(database), installed_inventory(home)), before
                                )
                                diagnostic = subprocess.run(
                                    [
                                        python,
                                        "-I",
                                        "-c",
                                        "import sys; from kairos_state import connect; from kairos_state.migrations import migrate; migrate(connect(sys.argv[1]))",
                                        str(home / "state.db"),
                                    ],
                                    cwd=base,
                                    capture_output=True,
                                    text=True,
                                    check=False,
                                )
                                self.assertNotEqual(diagnostic.returncode, 0)
                                self.assertIn("schema futuro não suportado", diagnostic.stderr)
                                self.assertEqual(
                                    (canonical_rows(database), installed_inventory(home)), before
                                )
                            else:
                                self.assertEqual(result.returncode, 0, result.stderr)
                                self.assertEqual(json.loads(result.stdout)["state"], "committed")
                                self.assertNotEqual(
                                    (canonical_rows(database), installed_inventory(home)), before
                                )
                        finally:
                            database.close()
