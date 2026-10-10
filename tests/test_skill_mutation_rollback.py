"""Rollback conserva evidência e não remove a criação seguinte."""

import multiprocessing
import os
import shutil
import sqlite3
import unittest
import uuid
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


def stopped_rollback(home, create_id, phase, pipe):
    db = connect(home / "state.db")
    repo = SkillMutationRepository(db, home_id=catalog_home_id(home))
    service = SkillMutationService(home, repo)
    original_prepare = repo.prepare
    original_retire = SkillMutationFiles.retire
    original_restore = SkillMutationFiles.restore

    def prepare(draft, text):
        result = original_prepare(draft, text)
        if phase == "prepare":
            pipe.send(draft.operation_id)
            pipe.recv()
        return result

    def retire(files, name, operation):
        original_retire(files, name, operation)
        if phase == "retire":
            pipe.send(operation)
            pipe.recv()
        if phase == "restore":
            (home / ".skill-mutations" / "retired" / operation / "SKILL.md").write_text(
                creation_text(body="concurrent edited version")
            )

    def restore(files, operation, name):
        original_restore(files, operation, name)
        if phase == "restore":
            pipe.send(operation)
            pipe.recv()

    repo.prepare = prepare
    with (
        patch.object(SkillMutationFiles, "retire", retire),
        patch.object(SkillMutationFiles, "restore", restore),
    ):
        service.rollback(create_id, actor=Actor.USER_FOREGROUND)
    db.close()


class RollbackTests(unittest.TestCase):
    def setUp(self):
        service_fixtures.MutationServiceTests.setUp(self)
        self.created = self.service.add(self.source, actor=Actor.USER_FOREGROUND)

    def test_rollback_retires_and_links_evidence(self):
        with SkillMutationFiles(self.home) as files:
            original = files.inspect_installed(self.created.name)
        rollback = self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertIs(rollback.state, SkillMutationState.COMMITTED)
        self.assertEqual(rollback.reverts, self.created.operation_id)
        self.assertFalse((self.home / "skills" / self.created.name).exists())
        with SkillMutationFiles(self.home) as files:
            self.assertEqual(files.inspect_private(rollback.operation_id, retired=True), original)
        self.assertEqual(self.repo.get(self.created.operation_id), self.created)
        self.assertEqual(
            self.repo.content(self.created.operation_id).text.encode(), self.source.read_bytes()
        )
        self.assertIsNone(self.repo.provenance(self.created.name))
        self.assertEqual(
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND), rollback
        )

    def test_repeat_does_not_touch_new_creation(self):
        rollback = self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.source.write_text(creation_text(body="new creation"))
        newer = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        before = self.repo.history()
        self.assertEqual(
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND), rollback
        )
        self.assertEqual(self.repo.history(), before)
        self.assertEqual(
            (self.home / "skills" / newer.name / "SKILL.md").read_bytes(), self.source.read_bytes()
        )

    def test_invalid_id_actor_parent_home_recusa(self):
        for operation in ("../escape", "", uuid.uuid4().hex):
            with self.subTest(operation=operation[:10]), self.assertRaises(SkillMutationError):
                self.service.rollback(operation, actor=Actor.USER_FOREGROUND)
        for actor in (Actor.CURATOR, Actor.BACKGROUND_REVIEW, "user_foreground"):
            with self.subTest(actor=actor), self.assertRaises(SkillMutationError):
                self.service.rollback(self.created.operation_id, actor=actor)
        self.assertTrue((self.home / "skills" / self.created.name).exists())
        rollback = self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        with self.assertRaises(SkillMutationError):
            self.service.rollback(rollback.operation_id, actor=Actor.USER_FOREGROUND)

    def test_edited_added_link_replaced_missing_preserved(self):
        for case in ("edited", "extra", "link", "replacement", "missing"):
            with self.subTest(case=case):
                target = self.home / "skills" / self.created.name
                old = self.root / ("old-" + case)
                if case == "edited":
                    (target / "SKILL.md").write_text(creation_text(body="edited version"))
                elif case == "extra":
                    (target / "extra.txt").write_text("preserve attachment")
                elif case == "link":
                    (target / "extra.txt").symlink_to(self.source)
                elif case == "replacement":
                    target.rename(old)
                    shutil.copytree(old, target)
                else:
                    target.rename(old)
                before = self.repo.history()
                with self.assertRaises(SkillMutationError):
                    self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
                self.assertEqual(self.repo.history(), before)
                if case == "edited":
                    self.assertIn("edited version", (target / "SKILL.md").read_text())
                    (target / "SKILL.md").write_bytes(self.source.read_bytes())
                elif case == "extra":
                    self.assertEqual((target / "extra.txt").read_text(), "preserve attachment")
                    (target / "extra.txt").unlink()
                elif case == "link":
                    self.assertTrue((target / "extra.txt").is_symlink())
                    (target / "extra.txt").unlink()
                elif case == "replacement":
                    self.assertEqual((target / "SKILL.md").read_bytes(), self.source.read_bytes())
                    shutil.rmtree(target)
                    old.rename(target)
                else:
                    self.assertEqual((old / "SKILL.md").read_bytes(), self.source.read_bytes())
                    old.rename(target)

    def test_editor_after_retire_restores_changed_version(self):
        target = self.home / "skills" / self.created.name / "SKILL.md"
        original_retire = SkillMutationFiles.retire
        edited = creation_text(body="editor still holds original file")
        with target.open("r+b") as editor:

            def retire_and_edit(files, name, operation):
                original_retire(files, name, operation)
                editor.seek(0)
                editor.write(edited.encode())
                editor.truncate()
                editor.flush()
                os.fsync(editor.fileno())

            with (
                patch.object(SkillMutationFiles, "retire", retire_and_edit),
                self.assertRaises(SkillMutationError) as raised,
            ):
                self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        rollback = self.repo.get(raised.exception.operation_id)
        self.assertIs(rollback.state, SkillMutationState.ABORTED)
        self.assertEqual(target.read_text(), edited)
        self.assertFalse(
            (self.home / ".skill-mutations" / "retired" / rollback.operation_id).exists()
        )

    def test_invalid_content_or_added_asset_after_retire_restores_entire_directory(self):
        original_retire = SkillMutationFiles.retire

        def retire_and_change(files, name, operation):
            original_retire(files, name, operation)
            retained = self.home / ".skill-mutations" / "retired" / operation
            (retained / "SKILL.md").write_bytes(b"invalid edited content")
            (retained / "extra.txt").write_text("new attachment")

        with (
            patch.object(SkillMutationFiles, "retire", retire_and_change),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        installed = self.home / "skills" / self.created.name
        self.assertEqual((installed / "SKILL.md").read_bytes(), b"invalid edited content")
        self.assertEqual((installed / "extra.txt").read_text(), "new attachment")
        self.assertIs(
            self.repo.get(raised.exception.operation_id).state, SkillMutationState.ABORTED
        )

    def test_editor_and_occupied_original_preserves_both(self):
        original_retire = SkillMutationFiles.retire
        edited = creation_text(body="edited retained version")
        occupied = creation_text(body="other installed version")

        def retire_edit_and_occupy(files, name, operation):
            original_retire(files, name, operation)
            (self.home / ".skill-mutations" / "retired" / operation / "SKILL.md").write_text(edited)
            directory = self.home / "skills" / name
            directory.mkdir()
            (directory / "SKILL.md").write_text(occupied)

        with (
            patch.object(SkillMutationFiles, "retire", retire_edit_and_occupy),
            self.assertRaises(SkillMutationError) as raised,
        ):
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        operation = raised.exception.operation_id
        self.assertIs(self.repo.get(operation).state, SkillMutationState.CONFLICT)
        self.assertEqual(
            (self.home / ".skill-mutations" / "retired" / operation / "SKILL.md").read_text(),
            edited,
        )
        self.assertEqual(
            (self.home / "skills" / self.created.name / "SKILL.md").read_text(), occupied
        )

    def test_restore_db_over_changed_files_recusa(self):
        backup = sqlite3.connect(self.home / "backup.db")
        self.db.backup(backup)
        edited = creation_text(body="edited after database backup")
        (self.home / "skills" / self.created.name / "SKILL.md").write_text(edited)
        backup.backup(self.db)
        backup.close()
        with self.assertRaises(SkillMutationError):
            self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.assertEqual(
            (self.home / "skills" / self.created.name / "SKILL.md").read_text(), edited
        )

    def test_corrupted_hash_parent_event_recusa(self):
        saved = sqlite3.connect(":memory:")
        self.db.backup(saved)
        self.addCleanup(saved.close)
        target = self.home / "skills" / self.created.name / "SKILL.md"
        original = target.read_bytes()
        for query in (
            "UPDATE skill_mutation_operations SET sha256='0000000000000000000000000000000000000000000000000000000000000000'",
            "DELETE FROM skill_mutation_events",
            "UPDATE skill_mutation_operations SET action='rollback',reverts='0123456789ab4def8123456789abcdef'",
        ):
            with self.subTest(query=query[:50]):
                self.db.execute("PRAGMA foreign_keys=OFF")
                with self.db:
                    for row in self.db.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'skill_mutation_%'"
                    ).fetchall():
                        self.db.execute('DROP TRIGGER "' + row[0].replace('"', '""') + '"')
                    self.db.execute(query)
                with self.assertRaises(SkillMutationError) as raised:
                    self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
                self.assertEqual(raised.exception.kind, "corrupt")
                self.assertEqual(target.read_bytes(), original)
                saved.backup(self.db)
                self.db.execute("PRAGMA foreign_keys=ON")

    def test_uncommitted_parent_and_other_home_refused(self):
        from test_skill_mutation_state import creation_draft

        from kairos_skills.mutation_contract import validate_skill_creation

        creation = validate_skill_creation(creation_text(name="other"))
        prepared = self.repo.prepare(creation_draft(self.home, creation), creation.text)
        with self.assertRaises(SkillMutationError):
            self.service.rollback(prepared.operation_id, actor=Actor.USER_FOREGROUND)
        other = SkillMutationRepository(self.db, home_id="0" * 64)
        with self.assertRaises(SkillMutationError):
            other.get(self.created.operation_id)
        self.assertTrue((self.home / "skills" / self.created.name / "SKILL.md").exists())

    def test_repeat_old_rollback_ignores_new_pending_creation(self):
        from test_skill_mutation_recovery import stopped_creation

        rolled = self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND)
        self.source.write_text(creation_text(body="new pending creation"))
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=stopped_creation, args=(self.home, self.source, "rename", child)
        )
        process.start()
        try:
            self.assertTrue(parent.poll(10))
            operation = parent.recv()
            process.kill()
            process.join(5)
            before = self.repo.history()
            self.assertEqual(
                self.service.rollback(self.created.operation_id, actor=Actor.USER_FOREGROUND),
                rolled,
            )
            self.assertEqual(self.repo.history(), before)
            self.assertIs(self.repo.get(operation).state, SkillMutationState.PREPARED)
            self.assertEqual(
                (self.home / "skills" / self.created.name / "SKILL.md").read_bytes(),
                self.source.read_bytes(),
            )
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def interrupted(self, phase):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=stopped_rollback, args=(self.home, self.created.operation_id, phase, child)
        )
        process.start()
        try:
            self.assertTrue(parent.poll(10), "rollback did not reach real boundary")
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

    def reconcile(self):
        db = connect(self.home / "state.db")
        try:
            repo = SkillMutationRepository(db, home_id=catalog_home_id(self.home))
            with skill_mutation_lock(self.home), SkillMutationFiles(self.home) as files:
                return reconcile_pending(repo, files, name=self.created.name)
        finally:
            db.close()

    def test_death_before_retire_aborts_intact(self):
        operation = self.interrupted("prepare")
        self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.ABORTED)
        self.assertEqual(
            (self.home / "skills" / self.created.name / "SKILL.md").read_bytes(),
            self.source.read_bytes(),
        )

    def test_death_after_retire_commits_once(self):
        operation = self.interrupted("retire")
        self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.COMMITTED)
        self.assertEqual(self.reconcile(), ())
        self.assertFalse((self.home / "skills" / self.created.name).exists())
        events = [
            row[0]
            for row in self.db.execute(
                "SELECT state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence",
                (operation,),
            )
        ]
        self.assertEqual(events, ["prepared", "committed"])

    def test_death_during_restore_preserves_changed_data(self):
        operation = self.interrupted("restore")
        self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.ABORTED)
        self.assertIn(
            "concurrent edited version",
            (self.home / "skills" / self.created.name / "SKILL.md").read_text(),
        )

    def test_retired_replaced_or_name_occupied_conflicts(self):
        operation = self.interrupted("retire")
        retired = self.home / ".skill-mutations" / "retired" / operation
        retired.rename(self.root / "original-retired")
        shutil.copytree(self.root / "original-retired", retired)
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIs(self.repo.get(operation).state, SkillMutationState.CONFLICT)
        self.assertEqual((retired / "SKILL.md").read_bytes(), self.source.read_bytes())
        # Voltar à versão própria permite commit só se o nome estiver ausente.
        shutil.rmtree(retired)
        (self.root / "original-retired").rename(retired)
        target = self.home / "skills" / self.created.name
        target.mkdir()
        (target / "SKILL.md").write_text(creation_text(body="occupied version"))
        with self.assertRaises(SkillMutationError):
            self.reconcile()
        self.assertIn("occupied version", (target / "SKILL.md").read_text())
        self.assertEqual((retired / "SKILL.md").read_bytes(), self.source.read_bytes())
