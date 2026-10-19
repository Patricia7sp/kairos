"""Proteção acompanha a conclusão real e recusa estado parcial ou writer antigo."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture, TreeEntry
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillDirectoryIdentity,
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationState,
    validate_skill_creation,
)
from kairos_skills.org_policy_contract import OrgPolicyReceipt, OrgProtectionBinding
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_state import connect
from kairos_state.migrations import CANONICAL_TABLES, migrate, repair_derived_objects
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_org_protections import SkillOrgProtectionRepository
from kairos_state.skill_org_schema import validate_org_schema


class SkillOrgStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.connection = connect(self.home / "state.db")
        self.addCleanup(self.connection.close)
        migrate(self.connection, target=10)
        self.home_id = catalog_home_id(self.home)
        self.repository = SkillMutationRepository(self.connection, home_id=self.home_id)
        self.protections = SkillOrgProtectionRepository(self.connection, home_id=self.home_id)

    def creation(self, name="org-skill"):
        text = f"---\nname: {name}\ndescription: Fixture.\nversion: '0.1.0'\n---\nFixture.\n"
        creation = validate_skill_creation(text)
        draft = SkillMutationDraft(
            uuid.uuid4().hex,
            self.home_id,
            SkillMutationAction.CREATE,
            name,
            Actor.USER_FOREGROUND,
            time.time(),
            SkillDirectoryIdentity(1, 2, 1, 4),
            creation.sha256,
            creation.size_bytes,
        )
        receipt = OrgPolicyReceipt(
            self.home_id,
            name,
            uuid.uuid4().hex,
            "a" * 64,
            (OrgProtectionBinding("equipe-exemplo", False),),
        )
        return draft, text, receipt

    def prepare(self):
        draft, text, receipt = self.creation()
        self.repository.prepare(draft, text, policy_receipt=receipt)
        return draft, receipt

    def canonical_rows(self, connection):
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return {
            name: tuple(tuple(row) for row in connection.execute('SELECT * FROM "' + name + '"'))  # noqa: S608 — nomes limitados à constante CANONICAL_TABLES
            for name in sorted(tables & CANONICAL_TABLES)
        }

    def restoration(self):
        draft, text, receipt = self.creation()
        self.repository.prepare(
            draft, text, policy_receipt=replace(receipt, source_digest=None, bindings=())
        )
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        snapshot = TreeCapture(
            (
                TreeEntry("", "directory", 0o700, 0, None, 1, 2, 1),
                TreeEntry("SKILL.md", "file", 0o600, len(text.encode()), draft.sha256, 1, 4, 1),
            ),
            {"SKILL.md": text.encode()},
        )
        operation_id = uuid.uuid4().hex
        remove = SkillTreeDraft(
            operation_id,
            self.home_id,
            SkillTreeAction.REMOVE,
            draft.name,
            Actor.USER_FOREGROUND,
            1.0,
            Provenance.USER,
            snapshot,
            operation_id,
        )
        self.repository.prepare_remove(remove, snapshot)
        self.repository.finish(remove.operation_id, SkillMutationState.COMMITTED)
        return replace(
            remove,
            operation_id=uuid.uuid4().hex,
            action=SkillTreeAction.RESTORE,
            reverts=remove.operation_id,
        ), receipt

    def test_restore_records_protection_only_at_commit(self):
        draft, receipt = self.restoration()
        self.repository.prepare_restore(draft, policy_receipt=receipt)
        self.assertEqual(self.protections.protected_organizations(draft.name), frozenset())
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(self.protections.protected_organizations(draft.name), {"equipe-exemplo"})
        self.assertEqual(self.protections.prepared_receipt(draft.operation_id), receipt)

    def test_restore_failure_rolls_back_both_completion_and_protection(self):
        draft, receipt = self.restoration()
        self.repository.prepare_restore(draft, policy_receipt=receipt)

        def authorize(action, table, *_args):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_INSERT and table == "skill_org_events"
                else sqlite3.SQLITE_OK
            )

        self.connection.set_authorizer(authorize)
        try:
            with self.assertRaises(SkillMutationError):
                self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        finally:
            self.connection.set_authorizer(None)
        self.assertIs(self.repository.get(draft.operation_id).state, SkillMutationState.PREPARED)
        self.assertEqual(self.protections.protected_organizations(draft.name), frozenset())

    def test_migration_preserves_v9_rows_and_prepared_legacy_receipt_absence(self):
        from kairos_skills.removal_state import read_skill_repository

        with connect(self.home / "legacy.db") as legacy:
            migrate(legacy, target=9)
            repository = SkillMutationRepository(legacy, home_id=self.home_id)
            draft, text, _ = self.creation()
            repository.prepare(draft, text)
            before = self.canonical_rows(legacy)
            read_skill_repository(legacy, self.home)
            self.assertEqual(self.canonical_rows(legacy), before)
            migrate(legacy, target=10)
            after = self.canonical_rows(legacy)
            self.assertEqual({name: after[name] for name in before}, before)
            protection = SkillOrgProtectionRepository(legacy, home_id=self.home_id)
            self.assertIsNone(protection.prepared_receipt(draft.operation_id))
            self.assertEqual(
                tuple(record.operation_id for record in repository.pending(draft.name)),
                (draft.operation_id,),
            )
            self.assertEqual(protection.protected_organizations(draft.name), frozenset())

    def test_prepared_or_aborted_does_not_register_protection(self):
        draft, receipt = self.prepare()
        self.assertEqual(self.protections.protected_organizations(draft.name), frozenset())
        self.assertEqual(self.protections.prepared_receipt(draft.operation_id), receipt)
        self.repository.finish(draft.operation_id, SkillMutationState.ABORTED)
        self.assertEqual(self.protections.protected_organizations(draft.name), frozenset())

    def test_committed_installation_registers_inactive_binding_and_is_idempotent(self):
        draft, _ = self.prepare()
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(
            self.protections.protected_organizations(draft.name), frozenset({"equipe-exemplo"})
        )
        self.assertEqual(self.protections.protected_organizations("personal"), frozenset())
        other = SkillOrgProtectionRepository(self.connection, home_id="b" * 64)
        self.assertEqual(other.protected_organizations(draft.name), frozenset())

    def test_new_installation_requires_matching_receipt_before_inserts(self):
        draft, text, receipt = self.creation()
        with self.assertRaises(SkillMutationError):
            self.repository.prepare(draft, text)
        self.assertIsNone(self.repository.get(draft.operation_id))
        wrong = OrgPolicyReceipt(
            "b" * 64, receipt.name, receipt.observation_id, receipt.source_digest, receipt.bindings
        )
        with self.assertRaises(SkillMutationError):
            self.repository.prepare(draft, text, policy_receipt=wrong)
        self.assertIsNone(self.repository.get(draft.operation_id))

    def test_failure_recording_protection_rolls_back_installation_commit(self):
        draft, _ = self.prepare()

        def authorize(action, table, *_args):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_INSERT and table == "skill_org_events"
                else sqlite3.SQLITE_OK
            )

        self.connection.set_authorizer(authorize)
        try:
            with self.assertRaises(SkillMutationError):
                self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        finally:
            self.connection.set_authorizer(None)
        self.assertIs(self.repository.get(draft.operation_id).state, SkillMutationState.PREPARED)
        self.assertEqual(self.protections.protected_organizations(draft.name), frozenset())

    def test_partial_family_refuses_even_when_no_protection_exists(self):
        self.connection.execute("DROP TABLE skill_org_events")
        with self.assertRaises(sqlite3.DatabaseError):
            validate_org_schema(self.connection)
        with self.assertRaises(SkillMutationError) as caught:
            self.protections.protected_organizations("org-skill")
        self.assertEqual(caught.exception.kind, "corrupt")

    def test_protection_reader_refuses_mislabeled_schema(self):
        draft, _ = self.prepare()
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        self.connection.execute("UPDATE schema_version SET version=9")
        self.connection.commit()
        with self.assertRaises(SkillMutationError) as caught:
            self.protections.protected_organizations(draft.name)
        self.assertEqual(caught.exception.kind, "corrupt")

    def test_prepared_receipt_reader_refuses_mislabeled_schema(self):
        draft, _ = self.prepare()
        self.connection.execute("UPDATE schema_version SET version=9")
        self.connection.commit()
        with self.assertRaises(SkillMutationError) as caught:
            self.protections.prepared_receipt(draft.operation_id)
        self.assertEqual(caught.exception.kind, "corrupt")

    def test_missing_guard_is_detected_and_repair_keeps_old_clients_out(self):
        prior, text, receipt = self.creation("prior")
        self.repository.prepare(prior, text, policy_receipt=receipt)
        self.repository.finish(prior.operation_id, SkillMutationState.COMMITTED)
        before = self.canonical_rows(self.connection)
        self.connection.execute("DROP TRIGGER skill_mutation_contents_writer")
        with self.assertRaises(sqlite3.DatabaseError):
            validate_org_schema(self.connection)
        repair_derived_objects(self.connection)
        self.assertEqual(self.canonical_rows(self.connection), before)
        validate_org_schema(self.connection)
        for version in (None, 8, 9):
            with self.subTest(version=version):
                raw = sqlite3.connect(self.home / "state.db")
                try:
                    if version is not None:
                        raw.create_function(
                            "kairos_skill_writer_version", 0, lambda version=version: version
                        )
                    with self.assertRaises(sqlite3.DatabaseError):
                        raw.execute(
                            "INSERT INTO skill_mutation_contents(sha256,text,size_bytes) VALUES(?,?,?)",
                            ("f" * 64, "fixture", 7),
                        )
                    raw.rollback()
                finally:
                    raw.close()
        draft, _ = self.prepare()
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(self.protections.protected_organizations(draft.name), {"equipe-exemplo"})

    def test_repair_keeps_old_clients_out_of_each_event_family(self):
        from test_skill_adoption_state import capture

        from kairos_skills.adoption_contract import SkillAdoptionAction, SkillAdoptionDraft
        from kairos_state.repositories.skill_adoptions import SkillAdoptionRepository

        restored, restore_receipt = self.restoration()
        self.repository.prepare_restore(restored, policy_receipt=restore_receipt)
        created, text, receipt = self.creation("personal")
        self.repository.prepare(created, text, policy_receipt=receipt)
        adoption_id = uuid.uuid4().hex
        adopted = SkillAdoptionDraft(
            adoption_id,
            self.home_id,
            SkillAdoptionAction.ADOPT,
            "legacy",
            Actor.USER_FOREGROUND,
            1.0,
            adoption_id,
            adoption_id,
            1,
            2,
            "unknown",
            adoption_id,
        )
        SkillAdoptionRepository(self.connection, home_id=self.home_id).prepare_adopt(
            adopted, capture()
        )
        before = self.canonical_rows(self.connection)
        self.connection.execute("DROP VIEW skill_org_protections")
        repair_derived_objects(self.connection)
        statements = (
            (
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?,'committed')",
                created.operation_id,
            ),
            (
                "INSERT INTO skill_tree_events(operation_id,state) VALUES (?,'committed')",
                restored.operation_id,
            ),
            (
                "INSERT INTO skill_adoption_events(operation_id,state) VALUES (?,'committed')",
                adoption_id,
            ),
        )
        for capability in (None, 8, 9, 10):
            with closing(sqlite3.connect(self.home / "state.db")) as raw:
                if capability is not None:
                    raw.create_function(
                        "kairos_skill_writer_version", 0, lambda capability=capability: capability
                    )
                for statement, operation_id in statements:
                    with self.subTest(capability=capability, operation_id=operation_id):
                        raw.execute("BEGIN")
                        try:
                            if capability == 10:
                                raw.execute(statement, (operation_id,))
                            else:
                                with self.assertRaises(sqlite3.DatabaseError) as caught:
                                    raw.execute(statement, (operation_id,))
                                self.assertIn(
                                    "no such function"
                                    if capability is None
                                    else "unsupported skill writer",
                                    str(caught.exception),
                                )
                        finally:
                            raw.rollback()
        self.assertEqual(self.canonical_rows(self.connection), before)
        for record in (created, restored, adopted):
            self.repository.finish(record.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(self.protections.protected_organizations("org-skill"), {"equipe-exemplo"})

    def test_schema_migration_is_idempotent_and_does_not_rewrite_old_rows(self):
        draft, _ = self.prepare()
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        before = tuple(map(tuple, self.connection.execute("SELECT * FROM skill_mutation_events")))
        migrate(self.connection, target=10)
        self.assertEqual(
            tuple(map(tuple, self.connection.execute("SELECT * FROM skill_mutation_events"))),
            before,
        )
        self.assertEqual(self.protections.protected_organizations(draft.name), {"equipe-exemplo"})

    def test_empty_receipt_is_preserved_without_inventing_organization(self):
        draft, text, _ = self.creation()
        receipt = OrgPolicyReceipt(self.home_id, draft.name, uuid.uuid4().hex, None, ())
        self.repository.prepare(draft, text, policy_receipt=receipt)
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(self.protections.prepared_receipt(draft.operation_id), receipt)
        self.assertEqual(self.protections.protected_organizations(draft.name), frozenset())

    def test_distinct_organizations_preserve_union_across_installations(self):
        draft, text, receipt = self.creation()
        self.repository.prepare(draft, text, policy_receipt=receipt)
        self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        rollback = replace(
            draft,
            operation_id=uuid.uuid4().hex,
            action=SkillMutationAction.ROLLBACK,
            reverts=draft.operation_id,
        )
        self.repository.prepare(rollback, text)
        self.repository.finish(rollback.operation_id, SkillMutationState.COMMITTED)
        other, text, other_receipt = self.creation()
        other_receipt = replace(
            other_receipt, bindings=(OrgProtectionBinding("outra-equipe", True),)
        )
        self.repository.prepare(other, text, policy_receipt=other_receipt)
        self.repository.finish(other.operation_id, SkillMutationState.COMMITTED)
        self.assertEqual(
            self.protections.protected_organizations(draft.name), {"equipe-exemplo", "outra-equipe"}
        )

    def corrupt_canonical_rows(self, table):
        draft, text, receipt = self.creation()
        self.repository.prepare(draft, text, policy_receipt=receipt)
        if table == "skill_org_events":
            self.repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        triggers = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)
            )
        ]
        for trigger in triggers:
            self.connection.execute('DROP TRIGGER "' + trigger + '"')
        query = {
            "skill_org_intent_bindings": "DELETE FROM skill_org_intent_bindings WHERE operation_id=?",
            "skill_org_events": "DELETE FROM skill_org_events WHERE operation_id=?",
        }[table]
        self.connection.execute(query, (draft.operation_id,))
        self.connection.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            repair_derived_objects(self.connection)
        with self.assertRaises(SkillMutationError) as caught:
            self.protections.protected_organizations(draft.name)
        self.assertEqual(caught.exception.kind, "corrupt")

    def test_missing_binding_is_corruption_even_after_guard_repair(self):
        self.corrupt_canonical_rows("skill_org_intent_bindings")

    def test_missing_event_is_corruption_even_after_guard_repair(self):
        self.corrupt_canonical_rows("skill_org_events")

    def test_unsupported_explicit_migration_target_refuses_even_after_upgrade(self):
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(self.connection, target=9999)

    def test_legacy_database_with_only_org_view_is_corrupt(self):
        from kairos_skills.removal_state import read_skill_repository

        with connect(self.home / "legacy.db") as legacy:
            migrate(legacy, target=9)
            legacy.execute(
                "CREATE VIEW skill_org_protections AS SELECT 'invalid' AS organization_id"
            )
            with self.assertRaises(SkillMutationError) as caught:
                read_skill_repository(legacy, self.home)
            self.assertEqual(caught.exception.kind, "corrupt")
