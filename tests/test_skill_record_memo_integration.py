"""Transações reais conservam validações e só reutilizam reconstruções estáveis."""

import json
import multiprocessing
import os
import shutil
import sqlite3
from contextlib import closing
from unittest.mock import patch

from skill_mutation_fixtures import creation_text
from skill_record_memo_fixtures import RecordMemoFixture

from kairos_domain.ownership import Actor
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutations import SkillMutationService
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_skills.removal_state import database_connection
from kairos_state.repositories import skill_mutations
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_record_memo import RecordMemo


def hold_writer(path, pipe):
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO external_probe VALUES('committed elsewhere')")
            pipe.send("locked")
            if not pipe.poll(10) or pipe.recv() != "release":
                raise RuntimeError("writer_release_not_received")
            connection.commit()
        pipe.send("committed")
    finally:
        pipe.close()


class RecordMemoIntegrationTests(RecordMemoFixture):
    def test_prepare_reuses_records_but_runs_each_timeline_validation(self):
        original = self.repo._legacy_get
        known = {record.operation_id for record in self.records}

        def counted(operation_id):
            if self.db.in_transaction and operation_id in known:
                self.loads[operation_id] += 1
            return original(operation_id)

        self.source.write_text(creation_text(name="new-target"))
        with patch.object(self.repo, "_legacy_get", counted):
            result = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(result.state, SkillMutationState.COMMITTED)
        for operation_id in known:
            self.assertEqual(self.loads[operation_id], 1)

    def test_finish_returns_committed_after_its_insert(self):
        self.source.write_text(creation_text(name="new-target"))
        result = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(result.state, SkillMutationState.COMMITTED)
        events = [
            row[0]
            for row in self.db.execute(
                "SELECT state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence",
                (result.operation_id,),
            )
        ]
        self.assertEqual(events, ["prepared", "committed"])
        self.assertEqual(
            (self.home / "skills/new-target/SKILL.md").read_bytes(), self.source.read_bytes()
        )

    def test_external_transaction_and_other_write_callbacks_never_enable_memo(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(SkillMutationError) as caught:
                self.repo._write(lambda: self.db.execute("CREATE TABLE accidental(value)"))
            self.assertEqual(caught.exception.kind, "io")
            self.assertTrue(self.db.in_transaction)
        finally:
            self.db.rollback()
        record = self.records[0]
        original = self.repo._legacy_get

        def counted(operation_id):
            self.loads[operation_id] += 1
            return original(operation_id)

        with patch.object(self.repo, "_legacy_get", counted):
            observed = self.repo._write(
                lambda: (self.repo.get(record.operation_id), self.repo.get(record.operation_id))
            )
        self.assertEqual(observed, (record, record))
        self.assertEqual(self.loads[record.operation_id], 2)

    def test_tree_writes_do_not_enter_record_memo(self):
        calls = []
        original = RecordMemo.load

        def counted(memo, *args, **kwargs):
            calls.append(True)
            return original(memo, *args, **kwargs)

        with patch.object(RecordMemo, "load", counted):
            result = self.service.remove("first", actor=Actor.USER_FOREGROUND, confirmed=True)
        self.assertEqual(result.state, SkillMutationState.COMMITTED)
        self.assertFalse((self.home / "skills/first").exists())
        self.assertEqual(calls, [])

    def test_memo_is_closed_before_commit_rollback_and_retry(self):
        observations, attempts = [], []
        original = self.repo._current_create

        def trace(statement):
            if statement in ("COMMIT", "ROLLBACK"):
                observations.append((statement, self.repo._record_memo is not None))

        def transient_busy(name):
            attempts.append(self.repo._record_memo)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            return original(name)

        self.db.set_trace_callback(trace)
        self.source.write_text(creation_text(name="retried"))
        try:
            with patch.object(self.repo, "_current_create", transient_busy):
                result = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        finally:
            self.db.set_trace_callback(None)
        self.assertEqual(result.state, SkillMutationState.COMMITTED)
        self.assertEqual(len(attempts), 2)
        self.assertIsNotNone(attempts[0])
        self.assertIsNot(attempts[0], attempts[1])
        self.assertTrue(all(memo.entry_count == 0 for memo in attempts))
        self.assertIn(("ROLLBACK", False), observations)
        self.assertIn(("COMMIT", False), observations)
        self.assertFalse(any(active for _statement, active in observations))
        self.assertIsNone(self.repo._record_memo)

    def test_timeline_validation_cannot_be_skipped_after_memo_is_populated(self):
        original = self.repo._current_create
        timeline = skill_mutations.validate_skill_timeline
        armed = False

        def second_scan(name):
            nonlocal armed
            armed = True
            return original(name)

        def sentinel(connection):
            if armed and connection.in_transaction:
                raise SkillMutationError("corrupt", "validation sentinel")
            return timeline(connection)

        self.source.write_text(creation_text(name="refused"))
        with (
            patch.object(self.repo, "_current_create", second_scan),
            patch.object(skill_mutations, "validate_skill_timeline", sentinel),
            self.assertRaises(SkillMutationError) as caught,
        ):
            self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(caught.exception.kind, "corrupt")
        self.assertFalse((self.home / "skills/refused").exists())
        self.assertEqual(len(self.repo.history()), len(self.records))

    def test_corrupt_other_name_and_changed_triggers_still_refuse(self):
        original = self.repo._current_create

        def corrupted(name):
            trigger = self.db.execute(
                "SELECT sql FROM sqlite_master WHERE name='skill_mutation_contents_no_update'"
            ).fetchone()[0]
            self.db.execute("DROP TRIGGER skill_mutation_contents_no_update")
            self.db.execute(
                "UPDATE skill_mutation_contents SET text='broken' WHERE sha256=?",
                (self.records[0].sha256,),
            )
            self.db.execute(trigger)
            return original(name)

        def missing_trigger(name):
            self.db.execute("DROP TRIGGER skill_mutation_contents_writer")
            return original(name)

        for fault in (corrupted, missing_trigger):
            with self.subTest(fault=fault.__name__):
                self.source.write_text(creation_text(name="refused"))
                with (
                    patch.object(self.repo, "_current_create", fault),
                    self.assertRaises(SkillMutationError) as caught,
                ):
                    self.service.add(self.source, actor=Actor.USER_FOREGROUND)
                self.assertEqual(caught.exception.kind, "corrupt")
                self.assertFalse((self.home / "skills/refused").exists())
                self.assertEqual(len(self.repo.history()), len(self.records))

    def test_policy_change_after_staging_still_vetoes_effects(self):
        policy_source = self.root / "policy.json"
        policy_source.write_text(json.dumps({"version": 1, "bindings": []}))
        policy = self.enterContext(
            observe_org_policy(OrgPolicyContext("file", policy_source), self.home)
        )
        service = SkillMutationService(self.home, self.repo, policy=policy)
        original = self.repo.prepare

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            policy_source.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "bindings": [
                            {
                                "home": str(self.home),
                                "name": "refused",
                                "organization_id": "org-probe",
                                "active": True,
                            }
                        ],
                    }
                )
            )
            return result

        self.source.write_text(creation_text(name="refused"))
        with (
            patch.object(self.repo, "prepare", changed),
            self.assertRaises(SkillMutationError) as caught,
        ):
            service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertFalse((self.home / "skills/refused").exists())
        self.assertEqual(len(self.repo.pending("refused")), 1)
        self.assertTrue(any((self.home / ".skill-mutations/staging").iterdir()))

    def test_real_writer_contention_restarts_with_empty_observation(self):
        from kairos_state import writes

        self.db.execute("CREATE TABLE external_probe(value)")
        self.db.execute("PRAGMA busy_timeout=0")
        parent, child = multiprocessing.get_context("spawn").Pipe()
        process = multiprocessing.get_context("spawn").Process(
            target=hold_writer, args=(str(self.home / "state.db"), child)
        )
        process.start()
        child.close()
        self.addCleanup(parent.close)
        self.addCleanup(self.stop_writer, process)
        self.assertTrue(parent.poll(10))
        self.assertEqual(parent.recv(), "locked")
        original_sleep = writes._sleep_before_retry
        waits, begins = [], []

        def release(elapsed):
            waits.append(elapsed)
            parent.send("release")
            if not parent.poll(10) or parent.recv() != "committed":
                raise RuntimeError("writer_did_not_commit")
            original_sleep(elapsed)

        def trace(statement):
            if statement == "BEGIN IMMEDIATE":
                begins.append(self.repo._record_memo is not None)

        self.db.set_trace_callback(trace)
        self.source.write_text(creation_text(name="after-contention"))
        try:
            with patch.object(writes, "_sleep_before_retry", release):
                result = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        finally:
            self.db.set_trace_callback(None)
        process.join(10)
        self.assertEqual(process.exitcode, 0)
        self.assertTrue(waits)
        self.assertTrue(begins)
        self.assertFalse(any(begins))
        self.assertEqual(result.state, SkillMutationState.COMMITTED)
        self.assertEqual(
            self.db.execute("SELECT value FROM external_probe").fetchone()[0], "committed elsewhere"
        )

    @staticmethod
    def stop_writer(process):
        process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(5)

    def test_database_identity_guard_still_refuses_after_replacement(self):
        record = self.records[0]
        with (
            self.assertRaises(SkillMutationError) as caught,
            database_connection(self.home, write=True) as connection,
        ):
            repository = SkillMutationRepository(connection, home_id=self.repo.home_id)
            with self.memo_scope(connection=connection) as memo:

                def loader():
                    return repository._get_validated(record.operation_id, modern=True)

                self.assertEqual(memo.load(record.operation_id, modern=True, loader=loader), record)
                replacement = self.root / "new-state.db"
                shutil.copyfile(self.home / "state.db", replacement)
                os.replace(replacement, self.home / "state.db")
                self.assertEqual(memo.load(record.operation_id, modern=True, loader=loader), record)
        self.assertEqual(caught.exception.kind, "conflict")
