"""Interrupções de processos e recuperação com SQLite/rename reais."""

import multiprocessing
import shutil
import unittest
from unittest.mock import patch

import test_skill_mutation_service as service_fixtures
from skill_mutation_fixtures import creation_text

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_io import SkillMutationFiles
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import reconcile_pending
from kairos_skills.mutations import SkillMutationService
from kairos_state import connect
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def stopped_creation(home, source, phase, pipe):
    db = connect(home / "state.db")
    repo = SkillMutationRepository(db, home_id=catalog_home_id(home))
    service = SkillMutationService(home, repo)
    original_prepare = repo.prepare
    original_publish = SkillMutationFiles.publish
    original_stage = SkillMutationFiles.stage

    def prepare(draft, text):
        result = original_prepare(draft, text)
        if phase == "prepare":
            pipe.send(draft.operation_id)
            pipe.recv()
        return result

    def publish(files, operation_id, name):
        original_publish(files, operation_id, name)
        if phase == "rename":
            pipe.send(operation_id)
            pipe.recv()

    def stage(files, operation_id, creation):
        result = original_stage(files, operation_id, creation)
        if phase == "stage":
            pipe.send(operation_id)
            pipe.recv()
        return result

    repo.prepare = prepare
    with (
        patch.object(SkillMutationFiles, "publish", publish),
        patch.object(SkillMutationFiles, "stage", stage),
    ):
        service.add(source, actor=Actor.USER_FOREGROUND)
    db.close()


def racing_creation(home, source, barrier, pipe):
    db = connect(home / "state.db")
    repo = SkillMutationRepository(db, home_id=catalog_home_id(home))
    barrier.wait(timeout=10)
    try:
        record = SkillMutationService(home, repo).add(source, actor=Actor.USER_FOREGROUND)
        pipe.send(("committed", record.operation_id))
    except SkillMutationError as error:
        pipe.send(("refused", error.kind))
    finally:
        db.close()


def hold_database(path, pipe):
    db = connect(path, timeout=0.01)
    db.execute("BEGIN IMMEDIATE")
    pipe.send("locked")
    pipe.recv()
    db.rollback()
    db.close()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        service_fixtures.MutationServiceTests.setUp(self)
        self.context = multiprocessing.get_context("spawn")

    def interrupted(self, phase):
        parent, child = self.context.Pipe()
        process = self.context.Process(
            target=stopped_creation, args=(self.home, self.source, phase, child)
        )
        process.start()
        try:
            self.assertTrue(parent.poll(10), "process did not reach real boundary")
            operation = parent.recv()
            process.kill()
            process.join(5)
            self.assertEqual(process.exitcode, -9)
            return operation
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def reconcile(self, name="revisar-docs"):
        # Nova conexão equivale à reabertura do serviço depois da interrupção.
        db = connect(self.home / "state.db")
        try:
            repo = SkillMutationRepository(db, home_id=catalog_home_id(self.home))
            with skill_mutation_lock(self.home), SkillMutationFiles(self.home) as files:
                return reconcile_pending(repo, files, name=name)
        finally:
            db.close()

    def test_death_after_prepare(self):
        operation = self.interrupted("prepare")
        self.assertIs(self.repo.get(operation).state, SkillMutationState.PREPARED)
        self.assertFalse((self.home / "skills" / "revisar-docs").exists())
        self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.ABORTED)
        self.assertFalse((self.home / ".skill-mutations" / "staging" / operation).exists())
        self.assertFalse((self.home / "skills" / "revisar-docs").exists())
        record = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertIs(record.state, SkillMutationState.COMMITTED)

    def test_death_after_rename(self):
        operation = self.interrupted("rename")
        original = (self.home / "skills" / "revisar-docs" / "SKILL.md").read_bytes()
        self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.COMMITTED)
        self.assertEqual(self.reconcile(), ())
        self.assertEqual(
            (self.home / "skills" / "revisar-docs" / "SKILL.md").read_bytes(), original
        )
        self.assertEqual(
            [
                row[0]
                for row in self.db.execute(
                    "SELECT state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence",
                    (operation,),
                )
            ],
            ["prepared", "committed"],
        )

    def test_absent_staging_and_install_abort_without_recreating(self):
        operation = self.interrupted("prepare")
        staged = self.home / ".skill-mutations" / "staging" / operation
        (staged / "SKILL.md").unlink()
        staged.rmdir()
        self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.ABORTED)
        self.assertFalse((self.home / "skills" / "revisar-docs").exists())

    def test_both_present_conflict_preserves_both(self):
        operation = self.interrupted("prepare")
        staged = self.home / ".skill-mutations" / "staging" / operation
        installed = self.home / "skills" / "revisar-docs"
        shutil.copytree(staged, installed)
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.CONFLICT)
        self.assertEqual((staged / "SKILL.md").read_bytes(), self.source.read_bytes())
        self.assertEqual((installed / "SKILL.md").read_bytes(), self.source.read_bytes())

    def test_io_failure_never_means_absent(self):
        operation = self.interrupted("rename")
        with (
            patch.object(
                SkillMutationFiles,
                "inspect_installed",
                side_effect=SkillMutationError("io", "fixture"),
            ),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.reconcile()
        self.assertEqual(raised.exception.operation_id, operation)
        self.assertIs(self.repo.get(operation).state, SkillMutationState.PREPARED)
        self.assertTrue((self.home / "skills" / "revisar-docs" / "SKILL.md").exists())

    def test_error_after_publish_keeps_pending_and_id(self):
        original_publish = SkillMutationFiles.publish

        def publish_then_error(files, operation, name):
            original_publish(files, operation, name)
            raise OSError("private backend path")

        with (
            patch.object(SkillMutationFiles, "publish", publish_then_error),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        operation = raised.exception.operation_id
        self.assertNotIn("private backend path", str(raised.exception))
        self.assertIs(self.repo.get(operation).state, SkillMutationState.PREPARED)
        self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.COMMITTED)

    def test_fail_after_commit(self):
        original_finish = self.repo.finish

        def committed_then_error(operation, state):
            original_finish(operation, state)
            raise SkillMutationError("io", "fixture after real commit")

        with (
            patch.object(self.repo, "finish", side_effect=committed_then_error),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        operation = raised.exception.operation_id
        self.assertIs(self.repo.get(operation).state, SkillMutationState.COMMITTED)
        self.assertEqual(self.reconcile(), ())
        self.assertTrue((self.home / "skills" / "revisar-docs" / "SKILL.md").exists())

    def test_db_busy_after_publish(self):
        from kairos_state.contention import BUDGET_SECONDS, Budget

        parent, child = self.context.Pipe()
        process = self.context.Process(target=hold_database, args=(self.home / "state.db", child))
        original_publish = SkillMutationFiles.publish

        def publish_and_lock(files, operation, name):
            original_publish(files, operation, name)
            process.start()
            self.assertTrue(parent.poll(10))
            self.assertEqual(parent.recv(), "locked")

        try:
            with (
                patch.object(SkillMutationFiles, "publish", publish_and_lock),
                patch.dict(BUDGET_SECONDS, {Budget.TRANSCRIPT: 0.05}),
                self.assertRaises(SkillMutationError) as raised,
            ):
                self.service.add(self.source, actor=Actor.USER_FOREGROUND)
            operation = raised.exception.operation_id
            self.assertTrue((self.home / "skills" / "revisar-docs" / "SKILL.md").exists())
            self.assertIs(self.repo.get(operation).state, SkillMutationState.PREPARED)
            parent.send("release")
            process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.reconcile()
            self.assertIs(self.repo.get(operation).state, SkillMutationState.COMMITTED)
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def test_identical_foreign_replacement(self):
        operation = self.interrupted("rename")
        installed = self.home / "skills" / "revisar-docs"
        installed.rename(self.root / "original")
        shutil.copytree(self.root / "original", installed)
        with self.assertRaises(SkillMutationError) as raised:
            self.reconcile()
        self.assertEqual(raised.exception.operation_id, operation)
        self.assertIs(self.repo.get(operation).state, SkillMutationState.CONFLICT)
        events = self.db.execute(
            "SELECT count(*) FROM skill_mutation_events WHERE operation_id=?", (operation,)
        ).fetchone()[0]
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM skill_mutation_events WHERE operation_id=?", (operation,)
            ).fetchone()[0],
            events,
        )
        self.assertEqual((installed / "SKILL.md").read_bytes(), self.source.read_bytes())

    def test_unknown_staging_no_promotion(self):
        operation = self.interrupted("stage")
        staged = self.home / ".skill-mutations" / "staging" / operation / "SKILL.md"
        self.assertIsNone(self.repo.get(operation))
        original = staged.read_bytes()
        self.assertEqual(self.reconcile(), ())
        self.assertFalse((self.home / "skills" / "revisar-docs").exists())
        self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(staged.read_bytes(), original)

    def test_pending_other_name_not_blocking(self):
        operation = self.interrupted("rename")
        (self.home / "skills" / "revisar-docs" / "SKILL.md").write_text(
            creation_text(body="edited")
        )
        self.source.write_text(creation_text(name="other"))
        self.assertIs(
            self.service.add(self.source, actor=Actor.USER_FOREGROUND).state,
            SkillMutationState.COMMITTED,
        )
        self.assertIs(self.repo.get(operation).state, SkillMutationState.PREPARED)

    def test_two_processes_one_winner(self):
        sources = [self.source, self.root / "second.md"]
        sources[1].write_text(creation_text(body="second contender"))
        barrier = self.context.Barrier(2)
        pipes = [self.context.Pipe() for _ in sources]
        processes = [
            self.context.Process(target=racing_creation, args=(self.home, source, barrier, pipe[1]))
            for source, pipe in zip(sources, pipes, strict=True)
        ]
        try:
            for process in processes:
                process.start()
            results = []
            for parent, _child in pipes:
                self.assertTrue(parent.poll(15))
                results.append(parent.recv())
            for process in processes:
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted(result[0] for result in results), ["committed", "refused"])
            winner = results.index(next(result for result in results if result[0] == "committed"))
            self.assertEqual(
                (self.home / "skills" / "revisar-docs" / "SKILL.md").read_bytes(),
                sources[winner].read_bytes(),
            )
            self.assertEqual(len(self.repo.history()), 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                process.join(5)
            for parent, child in pipes:
                parent.close()
                child.close()
