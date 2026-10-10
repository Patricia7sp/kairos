"""Ledger real: transações, evidência preservada e corrupção recusada."""

import dataclasses
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from skill_mutation_fixtures import creation_text

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillDirectoryIdentity,
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationState,
    validate_skill_creation,
)
from kairos_state import connect
from kairos_state.migrations import migrate, repair_derived_objects
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def creation_draft(home, creation=None, **changes):
    creation = creation or validate_skill_creation(creation_text())
    draft = SkillMutationDraft(
        uuid.uuid4().hex,
        catalog_home_id(home),
        SkillMutationAction.CREATE,
        creation.name,
        Actor.USER_FOREGROUND,
        time.time(),
        SkillDirectoryIdentity(1, 2, 1, 3),
        creation.sha256,
        creation.size_bytes,
    )
    return dataclasses.replace(draft, **changes)


class MutationStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.close_connection)
        migrate(self.db)
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.creation = validate_skill_creation(creation_text())
        self.draft = creation_draft(self.home)

    def close_connection(self):
        self.db.close()

    def committed(self):
        self.repo.prepare(self.draft, self.creation.text)
        return self.repo.finish(self.draft.operation_id, SkillMutationState.COMMITTED)

    def events(self, operation_id):
        return [
            row[0]
            for row in self.db.execute(
                "SELECT state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence",
                (operation_id,),
            )
        ]

    def test_prepare_e_finish_atomicos(self):
        prepared = self.repo.prepare(self.draft, self.creation.text)
        self.assertEqual(prepared.state, SkillMutationState.PREPARED)
        self.assertEqual(self.repo.content(prepared.operation_id), self.creation)
        with self.assertRaises(SkillMutationError):
            self.repo.prepare(creation_draft(self.home), self.creation.text)
        finished = self.repo.finish(prepared.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(self.events(finished.operation_id), ["prepared", "committed"])
        self.assertIs(self.repo.provenance(finished.name), Provenance.USER)
        invalid = validate_skill_creation(creation_text(name="second"))
        with self.assertRaises(SkillMutationError):
            self.repo.prepare(creation_draft(self.home, invalid), self.creation.text)
        self.assertEqual(len(self.repo.history()), 1)

    def test_rollback_vinculado_e_idempotente(self):
        created = self.committed()
        rollback = dataclasses.replace(
            self.draft,
            operation_id=uuid.uuid4().hex,
            action=SkillMutationAction.ROLLBACK,
            reverts=created.operation_id,
        )
        self.repo.prepare(rollback, self.creation.text)
        finished = self.repo.finish(rollback.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(
            self.repo.finish(rollback.operation_id, SkillMutationState.COMMITTED), finished
        )
        self.assertEqual(self.events(rollback.operation_id), ["prepared", "committed"])
        self.assertEqual(self.repo.latest_rollback(created.operation_id), finished)
        self.assertIsNone(self.repo.provenance(created.name))
        self.assertEqual(self.repo.get(created.operation_id), created)
        self.assertEqual(self.repo.content(created.operation_id), self.creation)
        self.assertEqual(self.repo.history(), (finished, created))

    def test_rollback_parent_missing_uncommitted_or_wrong_name_refused(self):
        rollback = dataclasses.replace(
            self.draft,
            operation_id=uuid.uuid4().hex,
            action=SkillMutationAction.ROLLBACK,
            reverts=uuid.uuid4().hex,
        )
        with self.assertRaises(SkillMutationError):
            self.repo.prepare(rollback, self.creation.text)
        prepared = self.repo.prepare(self.draft, self.creation.text)
        rollback = dataclasses.replace(rollback, reverts=prepared.operation_id)
        with self.assertRaises(SkillMutationError):
            self.repo.prepare(rollback, self.creation.text)
        self.repo.finish(prepared.operation_id, SkillMutationState.COMMITTED)
        other = validate_skill_creation(creation_text(name="other"))
        rollback = dataclasses.replace(
            rollback, name=other.name, sha256=other.sha256, size_bytes=other.size_bytes
        )
        with self.assertRaises(SkillMutationError):
            self.repo.prepare(rollback, other.text)
        self.assertEqual(len(self.repo.history()), 1)

    def test_foreign_keys_prevent_orphan_evidence(self):
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute(
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?,?)",
                (uuid.uuid4().hex, "prepared"),
            )
        self.assertEqual(self.repo.history(), ())

    def test_old_parent_cannot_remove_new_provenance(self):
        created = self.committed()
        rollback = dataclasses.replace(
            self.draft,
            operation_id=uuid.uuid4().hex,
            action=SkillMutationAction.ROLLBACK,
            reverts=created.operation_id,
        )
        self.repo.prepare(rollback, self.creation.text)
        self.repo.finish(rollback.operation_id, SkillMutationState.COMMITTED)
        newer = creation_draft(self.home)
        self.repo.prepare(newer, self.creation.text)
        self.repo.finish(newer.operation_id, SkillMutationState.COMMITTED)
        with self.assertRaises(SkillMutationError):
            self.repo.prepare(
                dataclasses.replace(rollback, operation_id=uuid.uuid4().hex), self.creation.text
            )
        self.assertIs(self.repo.provenance(created.name), Provenance.USER)

    def test_illegal_transitions(self):
        for first in (
            SkillMutationState.COMMITTED,
            SkillMutationState.ABORTED,
            SkillMutationState.CONFLICT,
        ):
            with self.subTest(first=first):
                creation = validate_skill_creation(creation_text(name="case-" + first.value))
                draft = creation_draft(self.home, creation)
                self.repo.prepare(draft, creation.text)
                record = self.repo.finish(draft.operation_id, first)
                self.assertEqual(self.repo.finish(draft.operation_id, first), record)
                with self.assertRaises(SkillMutationError):
                    self.repo.finish(draft.operation_id, SkillMutationState.PREPARED)
                if first is SkillMutationState.CONFLICT:
                    self.repo.finish(draft.operation_id, SkillMutationState.ABORTED)
                    self.assertEqual(
                        self.events(draft.operation_id), ["prepared", "conflict", "aborted"]
                    )
                else:
                    with self.assertRaises(SkillMutationError):
                        self.repo.finish(draft.operation_id, SkillMutationState.CONFLICT)

    def test_append_only(self):
        created = self.committed()
        for query in (
            "UPDATE skill_mutation_operations SET name='other'",
            "DELETE FROM skill_mutation_operations",
            "UPDATE skill_mutation_events SET state='aborted'",
            "DELETE FROM skill_mutation_events",
            "UPDATE skill_mutation_contents SET text='private'",
            "DELETE FROM skill_mutation_contents",
        ):
            with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError), self.db:
                self.db.execute(query)
        self.assertEqual(self.repo.get(created.operation_id), created)
        self.assertEqual(self.repo.content(created.operation_id), self.creation)

    def test_corrupt_record_recusa(self):
        queries = (
            "UPDATE skill_mutation_operations SET size_bytes=size_bytes+1",
            "UPDATE skill_mutation_operations SET sha256='" + "0" * 64 + "'",
            "UPDATE skill_mutation_contents SET text='adulterated'",
            "UPDATE skill_mutation_operations SET identity_json='{}'",
            "UPDATE skill_mutation_operations SET home_id='" + "0" * 64 + "'",
            "UPDATE skill_mutation_operations SET actor='curator'",
            "INSERT INTO skill_mutation_events(operation_id,state) SELECT operation_id,'prepared' FROM skill_mutation_operations",
            "DELETE FROM skill_mutation_events",
            "UPDATE skill_mutation_operations SET action='rollback',reverts='0123456789ab4def8123456789abcdef'",
        )
        created = self.committed()
        for query in queries:
            with self.subTest(query=query[:60]):
                copied = sqlite3.connect(":memory:")
                self.db.backup(copied)
                for row in copied.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'skill_mutation_%'"
                ).fetchall():
                    copied.execute('DROP TRIGGER "' + row[0].replace('"', '""') + '"')
                copied.execute("PRAGMA foreign_keys=OFF")
                copied.execute("PRAGMA ignore_check_constraints=ON")
                copied.execute(query)
                copied.commit()
                repo = SkillMutationRepository(copied, home_id=self.repo.home_id)
                try:
                    for read, value in (
                        (repo.get, created.operation_id),
                        (repo.content, created.operation_id),
                        (repo.provenance, created.name),
                    ):
                        with self.assertRaises(SkillMutationError) as raised:
                            read(value)
                        self.assertEqual(raised.exception.kind, "corrupt")
                finally:
                    copied.close()

    def test_history_limits_and_pending_other_name(self):
        created = self.committed()
        second = validate_skill_creation(creation_text(name="second"))
        prepared = self.repo.prepare(creation_draft(self.home, second), second.text)
        self.assertEqual(self.repo.history(limit=1), (prepared,))
        self.assertEqual(self.repo.history(name=created.name), (created,))
        self.assertEqual(self.repo.pending("second"), (prepared,))
        self.assertEqual(self.repo.pending(created.name), ())
        self.assertEqual(self.repo.history(name="missing"), ())
        for limit in (True, 0, 101, "20"):
            with self.subTest(limit=limit), self.assertRaises(SkillMutationError):
                self.repo.history(limit=limit)

    def test_unknown_provenance_nao_se_torna_autonoma(self):
        self.assertIsNone(self.repo.provenance("legacy"))
        self.committed()
        self.assertIs(self.repo.provenance(self.creation.name), Provenance.USER)

    def test_other_home_recusa(self):
        created = self.committed()
        other = SkillMutationRepository(self.db, home_id="0" * 64)
        with self.assertRaises(SkillMutationError):
            other.get(created.operation_id)

    def test_reopen_preserva_origem_e_eventos(self):
        created = self.committed()
        self.db.close()
        self.db = connect(self.home / "state.db")
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.assertEqual(self.repo.get(created.operation_id), created)
        self.assertEqual(self.events(created.operation_id), ["prepared", "committed"])
        self.assertIs(self.repo.provenance(created.name), Provenance.USER)

    def test_sqlite_backup_preserva_conteudo(self):
        created = self.committed()
        backup = sqlite3.connect(self.home / "backup.db")
        try:
            self.db.backup(backup)
            repo = SkillMutationRepository(backup, home_id=self.repo.home_id)
            self.assertEqual(repo.content(created.operation_id), self.creation)
            self.assertEqual(repo.get(created.operation_id), created)
        finally:
            backup.close()

    def test_repair_preserva_ledger(self):
        created = self.committed()
        before = self.repo.history()
        repair_derived_objects(self.db)
        self.assertEqual(self.repo.history(), before)
        self.assertEqual(self.repo.content(created.operation_id), self.creation)

    def test_migration_twice_preserva_transcript_catalogo_selecao(self):
        from kairos_skills.catalog_io import capture_skill_catalog
        from kairos_state.migrations import MIGRATIONS
        from kairos_state.repositories import MessageRepository, SessionRepository
        from kairos_state.repositories.skill_catalogs import SkillCatalogRepository

        self.db.close()
        self.db = connect(self.home / "old.db")
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        migrate(self.db, target=MIGRATIONS[-2].version)

        SessionRepository(self.db).create(
            "s1", source="cli", model="local", model_config='{"provider":"local"}'
        )
        message = MessageRepository(self.db).append(
            "s1", "user", content="exibido", api_content="enviado"
        )
        skills = self.home / "skills" / "revisar-docs"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(self.creation.text)
        snapshot = capture_skill_catalog(self.home)
        catalog = SkillCatalogRepository(self.db)
        catalog.create_if_absent("s1", snapshot)
        migrate(self.db)
        migrate(self.db)
        created = self.committed()
        self.assertEqual(
            tuple(
                self.db.execute("SELECT model,model_config FROM sessions WHERE id='s1'").fetchone()
            ),
            ("local", '{"provider":"local"}'),
        )
        self.assertEqual(
            tuple(
                self.db.execute(
                    "SELECT content,api_content FROM messages WHERE id=?", (message,)
                ).fetchone()
            ),
            ("exibido", "enviado"),
        )
        self.assertEqual(catalog.get("s1", home_id=snapshot.home_id), snapshot)
        self.assertEqual(self.repo.get(created.operation_id), created)
