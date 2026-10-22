"""Migração e confirmações não podem apagar história nem fabricar instalação."""

import sqlite3
from dataclasses import replace

from skill_publication_fixtures import PublicationStateFixture, canonical_rows

from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_state.migrations import migrate, repair_derived_objects


class PublicationStateTests(PublicationStateFixture):
    def test_confirmation_refuses_before_commit(self):
        repository, draft = self.prepared(manifest=True)
        before = tuple(self.connection.execute("SELECT * FROM skill_publication_events"))
        for confirm in (repository.confirm_manifest, repository.confirm_cleanup):
            with self.subTest(confirm=confirm), self.assertRaises(SkillMutationError):
                confirm(draft.operation_id, guard=lambda: None)
        self.assertEqual(
            tuple(self.connection.execute("SELECT * FROM skill_publication_events")), before
        )

    def test_manifest_maintenance_does_not_reinstall(self):
        from kairos_skills.publication_contract import ManifestDelta

        repository, draft = self.prepared(manifest=True)
        repository.finish(draft.operation_id, SkillMutationState.COMMITTED, guard=lambda: None)
        repository.confirm_manifest(draft.operation_id, guard=lambda: None)
        repository.confirm_cleanup(draft.operation_id, guard=lambda: None)
        delta = ManifestDelta(draft.name, "a" * 32, None)
        pending = repository.prepare_manifest(draft.operation_id, delta, guard=lambda: None)
        self.assertFalse(pending.manifest_complete)
        self.assertEqual(repository.pending(draft.name)[0].operation_id, draft.operation_id)
        completed = repository.confirm_manifest(draft.operation_id, guard=lambda: None)
        self.assertTrue(completed.manifest_complete)
        self.assertFalse(completed.pending)
        self.assertEqual(completed.draft, draft)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM skill_publication_events WHERE operation_id=? AND state='committed'",
                (draft.operation_id,),
            ).fetchone()[0],
            1,
        )

    def test_incomplete_v11_never_registers_writer_capability(self):
        from kairos_state.skill_writer import register_skill_writer

        self.upgrade()
        self.connection.execute("DROP TRIGGER skill_acquisition_receipts_writer")
        with self.assertRaises(sqlite3.DatabaseError):
            register_skill_writer(self.connection)
        self.assertNotEqual(
            self.connection.execute("SELECT kairos_skill_writer_version()").fetchone()[0], 11
        )

    def test_upgrade_preserves_canonical_prefix(self):
        from kairos_state.migrations import CANONICAL_TABLES

        self.legacy_creation()
        tables = sorted(
            {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            & CANONICAL_TABLES
        )
        before = canonical_rows(self.connection, tables)
        migrate(self.connection, target=11)
        self.assertEqual(canonical_rows(self.connection, tables), before)
        self.assertIsNotNone(
            self.connection.execute("SELECT 1 FROM skill_publication_migration").fetchone()
        )

    def prepared(self, *, manifest=False):
        repository = self.upgrade()
        receipt, draft = self.publication(manifest=manifest)
        repository.issue(receipt, guard=lambda: None)
        repository.prepare(draft, guard=lambda: None)
        return repository, draft

    def test_phase_confirmation_does_not_create_installation(self):
        repository, draft = self.prepared(manifest=True)
        committed = repository.finish(
            draft.operation_id, SkillMutationState.COMMITTED, guard=lambda: None
        )
        self.assertFalse(committed.manifest_complete)
        self.assertFalse(committed.cleanup_complete)
        for _ in range(2):
            repository.confirm_manifest(draft.operation_id, guard=lambda: None)
            repository.confirm_cleanup(draft.operation_id, guard=lambda: None)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM skill_publication_events WHERE operation_id=? AND state='committed'",
                (draft.operation_id,),
            ).fetchone()[0],
            1,
        )
        self.assertFalse(repository.pending(draft.name))
        self.assertEqual(repository.get(draft.operation_id).operation_id, draft.operation_id)

    def test_old_writer_cannot_mutate_any_family_after_repair(self):
        repository, draft = self.prepared()
        repair_derived_objects(self.connection)
        receipt, _other = self.publication()
        self.connection.create_function("kairos_skill_writer_version", 0, lambda: 10)
        with self.assertRaises((SkillMutationError, sqlite3.DatabaseError)):
            repository.issue(receipt, guard=lambda: None)
        for table in (
            "skill_mutation_operations",
            "skill_tree_operations",
            "skill_adoption_operations",
            "skill_org_intents",
        ):
            with (
                self.subTest(table=table),
                self.assertRaisesRegex(sqlite3.DatabaseError, "unsupported skill writer"),
            ):
                self.connection.execute(f"INSERT INTO {table} DEFAULT VALUES")
        self.assertEqual(repository.get(draft.operation_id).state, SkillMutationState.PREPARED)

    def test_partial_schema_refuses_read_write_and_repair(self):
        repository, draft = self.prepared()
        self.connection.execute("DROP TABLE skill_acquisition_receipts")
        for operation in (
            lambda: repository.get(draft.operation_id),
            lambda: repository.confirm_cleanup(draft.operation_id, guard=lambda: None),
            lambda: repair_derived_objects(self.connection),
        ):
            with (
                self.subTest(operation=operation),
                self.assertRaises((SkillMutationError, sqlite3.DatabaseError)),
            ):
                operation()

    def test_policy_change_before_commit_rolls_back_confirmation(self):
        from kairos_skills.org_policy import observe_org_policy
        from kairos_skills.org_policy_contract import OrgPolicyContext

        repository = self.upgrade()
        source = self.home / "policy.json"
        source.write_text('{"version":1,"bindings":[]}')
        with observe_org_policy(OrgPolicyContext("file", source), self.home) as policy:
            receipt, draft = self.publication()
            draft = replace(draft, policy_receipt=policy.receipt(draft.name))
            repository.issue(receipt, guard=policy.revalidate)
            repository.prepare(draft, guard=policy.revalidate)
            repository.finish(
                draft.operation_id, SkillMutationState.COMMITTED, guard=policy.revalidate
            )
            before = tuple(self.connection.execute("SELECT * FROM skill_publication_events"))

            def veto():
                self.assertTrue(self.connection.in_transaction)
                source.write_text('{"version":1, "bindings": []}')
                policy.revalidate()

            with self.assertRaises(SkillMutationError) as caught:
                repository.confirm_cleanup(draft.operation_id, guard=veto)
            self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(
            tuple(self.connection.execute("SELECT * FROM skill_publication_events")), before
        )
        self.assertFalse(repository.get(draft.operation_id).cleanup_complete)

    def test_acquisition_roundtrip_and_wrong_home_refusal(self):
        repository = self.upgrade()
        receipt, draft = self.publication()
        repository.issue(receipt, guard=lambda: None)
        self.assertEqual(repository.receipt(receipt.acquisition_id), receipt)
        with self.assertRaises(SkillMutationError):
            repository.prepare(replace(draft, home_id="b" * 64), guard=lambda: None)
        self.assertIsNone(repository.get(draft.operation_id))
