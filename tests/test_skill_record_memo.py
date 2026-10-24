"""Reuso nunca substitui a validação do loader real ou atravessa seu dono."""

import errno
import os
import shutil
import sqlite3
import uuid
from unittest.mock import patch

from skill_record_memo_fixtures import RecordMemoFixture

from kairos_domain.ownership import Actor
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.mutation_contract import SkillMutationError
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class RecordMemoTests(RecordMemoFixture):
    def test_same_observation_reuses_one_real_reconstruction(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            first = self.load_record(memo, record)
            second = self.load_record(memo, record)
            self.assertEqual(first, record)
            self.assertEqual(second, record)
            self.assertEqual(self.loads[record.operation_id], 1)

    def test_close_drops_entries_and_disables_reuse(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            self.assertEqual(self.load_record(memo, record), record)
            memo.close()
            self.assertEqual(memo.entry_count, 0)
            self.assertEqual(self.load_record(memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 2)

    def test_own_dml_and_ddl_require_fresh_load(self):
        self.db.execute("CREATE TABLE probe(value INTEGER)")
        record = self.records[0]
        with self.memo_scope() as memo:
            self.load_record(memo, record)
            self.db.execute("INSERT INTO probe VALUES(1)")
            self.assertEqual(self.load_record(memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 2)
            self.db.execute("CREATE TABLE changed_schema(value INTEGER)")
            self.assertEqual(self.load_record(memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 3)

    def test_loader_sql_change_never_relabels_an_old_result_as_fresh(self):
        self.db.execute("CREATE TABLE probe(value)")
        record = self.records[0]
        with self.memo_scope() as memo:

            def changed_loader():
                result = self.repo._get_validated(record.operation_id, modern=True)
                self.db.execute("INSERT INTO probe VALUES(1)")
                return result

            with self.assertRaises(SkillMutationError) as caught:
                memo.load(record.operation_id, modern=True, loader=changed_loader)
            self.assertEqual(caught.exception.kind, "conflict")
            self.assertEqual(memo.entry_count, 0)

    def test_rollback_and_new_scope_do_not_reuse_old_entries(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            self.load_record(memo, record)
            self.db.rollback()
            self.load_record(memo, record)
            self.assertEqual(self.loads[record.operation_id], 2)
        with self.memo_scope() as next_memo:
            self.assertEqual(self.load_record(next_memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 3)

    def test_connection_home_mode_and_unsupported_namespaces_do_not_share(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            self.load_record(memo, record)
            self.load_record(memo, record, modern=False)
            self.assertEqual(self.loads[record.operation_id], 2)
        with self.memo_scope(home_id="0" * 64) as other_home:
            self.load_record(other_home, record)
            self.load_record(other_home, record)
            self.assertEqual(self.loads[record.operation_id], 4)
        other = sqlite3.connect(self.home / "state.db")
        self.addCleanup(other.close)
        repository = SkillMutationRepository(other, home_id=self.repo.home_id)

        def load_other():
            self.loads[record.operation_id] += 1
            return repository._get_validated(record.operation_id, modern=True)

        with self.memo_scope(connection=other) as other_memo:
            result = other_memo.load(record.operation_id, modern=True, loader=load_other)
            self.assertEqual(result, record)
            self.assertEqual(self.loads[record.operation_id], 5)

    def test_temp_attach_and_read_uncommitted_do_not_reuse(self):
        record = self.records[0]
        setup = (
            ("CREATE TEMP TABLE probe(value)", "DROP TABLE temp.probe"),
            ("ATTACH DATABASE ':memory:' AS other", "DETACH DATABASE other"),
            ("PRAGMA read_uncommitted=ON", "PRAGMA read_uncommitted=OFF"),
        )
        for begin, end in setup:
            with self.subTest(begin=begin):
                self.loads.clear()
                self.db.execute(begin)
                try:
                    with self.memo_scope() as memo:
                        self.assertEqual(self.load_record(memo, record), record)
                        self.assertEqual(self.load_record(memo, record), record)
                        self.assertEqual(self.loads[record.operation_id], 2)
                finally:
                    self.db.execute(end)

    def test_memory_connection_uses_real_loader_every_time(self):
        memory = sqlite3.connect(":memory:")
        self.addCleanup(memory.close)
        self.db.backup(memory)
        record = self.records[0]
        repository = SkillMutationRepository(memory, home_id=self.repo.home_id)

        def load_memory():
            self.loads[record.operation_id] += 1
            return repository._get_validated(record.operation_id, modern=True)

        with self.memo_scope(connection=memory) as memo:
            self.assertEqual(
                memo.load(record.operation_id, modern=True, loader=load_memory), record
            )
            self.assertEqual(
                memo.load(record.operation_id, modern=True, loader=load_memory), record
            )
            self.assertEqual(self.loads[record.operation_id], 2)

    def test_none_errors_and_other_record_families_are_not_cached(self):
        legacy = self.home / "skills/legacy"
        legacy.mkdir()
        (legacy / "SKILL.md").write_text("Legacy fixture without semver.\n")
        adopted = (
            SkillAdoptionService(self.home, self.repo, policy=self.service.policy)
            .adopt("legacy", actor=Actor.USER_FOREGROUND, confirmed=True)
            .record
        )
        self.assertIsNotNone(adopted)
        with self.memo_scope() as memo:
            self.assertEqual(self.load_record(memo, adopted), adopted)
            self.assertEqual(self.load_record(memo, adopted), adopted)
            self.assertEqual(self.loads[adopted.operation_id], 2)
            absent_calls = []
            absent_id, error_id = uuid.uuid4().hex, uuid.uuid4().hex

            def absent():
                absent_calls.append(True)
                return self.repo._get_validated(absent_id, modern=True)

            self.assertIsNone(memo.load(absent_id, modern=True, loader=absent))
            self.assertIsNone(memo.load(absent_id, modern=True, loader=absent))
            self.assertEqual(len(absent_calls), 2)

            def invalid_sql():
                return self.db.execute("SELECT * FROM nonexistent_probe_table").fetchone()

            for _ in range(2):
                with self.assertRaises(sqlite3.OperationalError):
                    memo.load(error_id, modern=True, loader=invalid_sql)
            self.assertEqual(memo.entry_count, 0)

    def test_limit_preserves_semantics_without_unbounded_growth(self):
        first, second, third = self.records
        with self.memo_scope(limit=2) as memo:
            for record in (first, second, third, third, first):
                self.assertEqual(self.load_record(memo, record), record)
                self.assertLessEqual(memo.entry_count, 2)
            self.assertEqual(self.loads[first.operation_id], 1)
            self.assertEqual(self.loads[third.operation_id], 2)

    def test_replaced_database_and_sidecars_disable_reuse(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            self.load_record(memo, record)
            database = self.home / "state.db"
            replacement = self.root / "replacement.db"
            shutil.copyfile(database, replacement)
            os.replace(replacement, database)
            self.assertEqual(self.load_record(memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 2)
            self.assertEqual(self.load_record(memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 3)

    def test_new_sidecar_disables_reuse(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            self.load_record(memo, record)
            (self.home / "state.db-journal").touch()
            self.assertEqual(self.load_record(memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 2)

    def test_byte_identical_wal_replacement_disables_reuse(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            self.load_record(memo, record)
            wal = self.home / "state.db-wal"
            self.assertTrue(wal.exists())
            replacement = self.root / "wal-replacement"
            shutil.copyfile(wal, replacement)
            os.replace(replacement, wal)
            self.assertEqual(self.load_record(memo, record), record)
            self.assertEqual(self.loads[record.operation_id], 2)

    def test_batch_discards_partial_cached_values_on_change(self):
        first, second, _third = self.records
        self.db.execute("CREATE TABLE probe(value)")
        with self.memo_scope() as memo:
            self.load_record(memo, first)

            def changed_sql():
                one = self.load_record(memo, first)
                self.db.execute("INSERT INTO probe VALUES(1)")
                return one, self.load_record(memo, second)

            with self.assertRaises(SkillMutationError) as caught:
                memo.run_batch(changed_sql, uncached=lambda: ())
            self.assertEqual(caught.exception.kind, "conflict")

    def test_batch_reloads_uncached_after_physical_observation_changes(self):
        record = self.records[0]
        uncached_calls = []
        with self.memo_scope() as memo:
            self.load_record(memo, record)

            def changed_physical():
                result = self.load_record(memo, record)
                path = self.home / "state.db"
                path.chmod((path.stat().st_mode & 0o777) ^ 0o040)
                return (result,)

            def uncached():
                uncached_calls.append(True)
                return (self.repo._get_validated(record.operation_id, modern=True),)

            result = memo.run_batch(changed_physical, uncached=uncached)
            self.assertEqual(result, (record,))
            self.assertEqual(len(uncached_calls), 1)
            self.assertEqual(memo.entry_count, 0)

    def test_unexpected_observation_error_propagates_sanitized(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            with (
                patch("os.stat", side_effect=OSError(errno.EIO, "private-io-sentinel")),
                self.assertRaises(SkillMutationError) as caught,
            ):
                self.load_record(memo, record)
            self.assertEqual(caught.exception.kind, "io")
            self.assertNotIn("private-io-sentinel", str(caught.exception))

    def test_sql_observation_error_preserves_sqlite_failure(self):
        record = self.records[0]
        with self.memo_scope() as memo:
            self.db.set_authorizer(
                lambda action, *_args: (
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA else sqlite3.SQLITE_OK
                )
            )
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    self.load_record(memo, record)
            finally:
                self.db.set_authorizer(None)
