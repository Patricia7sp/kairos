"""Schema aditivo e atestação das quatro famílias de mutação de skills."""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache

from kairos_state.runtime_schema import execute_schema

PUBLICATION_TABLES = frozenset(
    {
        "skill_acquisition_receipts",
        "skill_publication_operations",
        "skill_publication_events",
        "skill_publication_migration",
        "skill_publication_timeline",
    }
)
PUBLICATION_SCHEMA_SQL = """
CREATE TABLE skill_acquisition_receipts (
    acquisition_id TEXT PRIMARY KEY CHECK(length(acquisition_id)=32),
    home_id TEXT NOT NULL CHECK(length(home_id)=64), name TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 4194304)
);
CREATE TABLE skill_publication_operations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE CHECK(length(operation_id)=32),
    home_id TEXT NOT NULL CHECK(length(home_id)=64), name TEXT NOT NULL,
    writer TEXT NOT NULL CHECK(writer IN ('bundled_sync','quarantine_promote')),
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 4194304)
);
CREATE TABLE skill_publication_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES skill_publication_operations(operation_id),
    state TEXT NOT NULL CHECK(state IN ('prepared','committed','aborted','conflict','manifest_prepared','manifest_applied','source_cleaned')),
    payload TEXT CHECK(payload IS NULL OR length(CAST(payload AS BLOB)) BETWEEN 1 AND 1024)
);
CREATE TABLE skill_publication_migration (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    prefix_last_authority_sequence INTEGER NOT NULL CHECK(typeof(prefix_last_authority_sequence)='integer' AND prefix_last_authority_sequence>=0)
);
CREATE TABLE skill_publication_timeline (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_event INTEGER UNIQUE REFERENCES skill_mutation_events(sequence),
    tree_event INTEGER UNIQUE REFERENCES skill_tree_events(sequence),
    adoption_event INTEGER UNIQUE REFERENCES skill_adoption_events(sequence),
    publication_event INTEGER UNIQUE REFERENCES skill_publication_events(sequence),
    CHECK((legacy_event IS NOT NULL)+(tree_event IS NOT NULL)+(adoption_event IS NOT NULL)+(publication_event IS NOT NULL)=1)
);
"""


def _base_trigger(name, sql):
    sql = sql.replace("IS NOT 10", "IS NOT 11")
    sql = sql.replace(
        "INSERT INTO skill_authority_timeline", "INSERT INTO skill_publication_timeline"
    )
    sql = sql.replace("JOIN skill_authority_timeline t", "JOIN skill_publication_timeline t")
    if name == "skill_org_intents_parent":
        sql = sql.replace(
            "    ) THEN RAISE(ABORT,'invalid protection parent') END;",
            """
        UNION ALL
        SELECT 1 FROM skill_publication_operations WHERE operation_id=NEW.operation_id
            AND home_id=NEW.home_id AND name=NEW.name
    ) THEN RAISE(ABORT,'invalid protection parent') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM skill_publication_events WHERE operation_id=NEW.operation_id)
        THEN RAISE(ABORT,'protection must accompany preparation') END;""",
        )
    return sql


def _new_triggers():
    statements = []
    keys = {
        "skill_acquisition_receipts": "acquisition_id=NEW.acquisition_id OR rowid=NEW.rowid",
        "skill_publication_operations": "operation_id=NEW.operation_id OR sequence=NEW.sequence",
        "skill_publication_events": "sequence=NEW.sequence",
        "skill_publication_migration": "singleton=NEW.singleton",
        "skill_publication_timeline": "sequence=NEW.sequence OR legacy_event=NEW.legacy_event OR tree_event=NEW.tree_event OR adoption_event=NEW.adoption_event OR publication_event=NEW.publication_event",
    }
    for table, key in keys.items():
        statements.append(f"""
CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}) BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only'); END;
CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only'); END;
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only'); END;
CREATE TRIGGER {table}_writer BEFORE INSERT ON {table} BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 11 THEN RAISE(ABORT,'unsupported skill writer') END; END;
""")  # noqa: S608 — tabelas e predicados vêm exclusivamente do mapa constante local
    statements.append("""
CREATE TRIGGER skill_authority_timeline_frozen BEFORE INSERT ON skill_authority_timeline BEGIN
    SELECT RAISE(ABORT,'historical skill timeline is frozen'); END;
CREATE TRIGGER skill_publication_events_timeline AFTER INSERT ON skill_publication_events BEGIN
    INSERT INTO skill_publication_timeline(publication_event) VALUES(NEW.sequence); END;
CREATE TRIGGER skill_publication_events_org_required BEFORE INSERT ON skill_publication_events BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM skill_org_intents WHERE operation_id=NEW.operation_id)
        OR EXISTS(SELECT 1 FROM skill_org_intent_bindings WHERE operation_id=NEW.operation_id)
        THEN RAISE(ABORT,'publication requires unprotected policy receipt') END; END;
""")
    return "\n".join(statements)


@lru_cache(maxsize=1)
def expected_publication_objects():
    from kairos_state.skill_org_schema import _reference_objects

    base = _reference_objects()
    with sqlite3.connect(":memory:") as reference:
        for kind, name, _table, sql in base:
            if kind != "trigger" and not name.startswith("sqlite_"):
                reference.execute(sql)
        execute_schema(reference, PUBLICATION_SCHEMA_SQL)
        for kind, name, _table, sql in base:
            if kind == "trigger":
                reference.execute(_base_trigger(name, sql))
        execute_schema(reference, _new_triggers())
        return tuple(
            tuple(row)
            for row in reference.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('trigger','table','view') ORDER BY type,name"
            )
        )


def expected_publication_triggers():
    return tuple(
        (name, table, sql)
        for kind, name, table, sql in expected_publication_objects()
        if kind == "trigger"
    )


def publication_record(draft, events):
    from kairos_skills.mutation_contract import SkillMutationState, require
    from kairos_skills.publication_contract import (
        ManifestDelta,
        PublicationWriter,
        SkillPublicationRecord,
        canonical_json,
    )

    state = None
    delta = draft.manifest_delta
    manifest = delta is None
    cleanup = draft.writer is PublicationWriter.BUNDLED_SYNC
    for event, payload in events:
        if event in ("prepared", "committed", "aborted", "conflict"):
            target = SkillMutationState(event)
            require(payload is None)
            require(
                (state is None and target is SkillMutationState.PREPARED)
                or (
                    state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT)
                    and target is not SkillMutationState.PREPARED
                )
            )
            state = target
        else:
            require(state is SkillMutationState.COMMITTED)
            if event == "manifest_prepared":
                require(manifest and cleanup and payload is not None)
                value = json.loads(payload)
                delta = ManifestDelta(**value)
                require(canonical_json(value) == payload and delta.name == draft.name)
                manifest = False
            elif event == "manifest_applied":
                require(payload is None and not manifest and delta is not None)
                manifest = True
            elif event == "source_cleaned":
                require(payload is None and not cleanup)
                cleanup = True
            else:
                require(False)
    require(state is not None)
    return SkillPublicationRecord(draft, state, manifest, cleanup, delta)


def _validate_rows(connection):
    from kairos_skills.mutation_contract import require
    from kairos_skills.publication_contract import (
        PublicationWriter,
        draft_from_json,
        receipt_from_json,
        record_payload,
    )

    receipts = {}
    for acquisition_id, home_id, name, payload in connection.execute(
        "SELECT acquisition_id,home_id,name,payload FROM skill_acquisition_receipts"
    ):
        receipt = receipt_from_json(json.loads(payload))
        require(record_payload(receipt) == payload)
        require(
            (receipt.acquisition_id, receipt.home_id, receipt.name)
            == (acquisition_id, home_id, name)
        )
        receipts[acquisition_id] = receipt
    operation_ids = set()
    for operation_id, home_id, name, writer, payload in connection.execute(
        "SELECT operation_id,home_id,name,writer,payload FROM skill_publication_operations ORDER BY sequence"
    ):
        draft = draft_from_json(json.loads(payload))
        require(record_payload(draft) == payload)
        require(
            (draft.operation_id, draft.home_id, draft.name, draft.writer.value)
            == (operation_id, home_id, name, writer)
        )
        if draft.writer is PublicationWriter.QUARANTINE_PROMOTE:
            acquisition_id = draft.source_ref.split("/")[0]
            receipt = receipts.get(acquisition_id)
            require(receipt is not None)
            require(
                (receipt.home_id, receipt.name, receipt.source_key, receipt.source_proof)
                == (draft.home_id, draft.name, draft.source_key, draft.source_proof)
            )
        events = [
            (row[0], row[1])
            for row in connection.execute(
                "SELECT state,payload FROM skill_publication_events WHERE operation_id=? ORDER BY sequence",
                (operation_id,),
            )
        ]
        publication_record(draft, events)
        operation_ids.add(operation_id)
    for table in (
        "skill_mutation_operations",
        "skill_tree_operations",
        "skill_adoption_operations",
    ):
        query = f"SELECT operation_id FROM {table}"  # noqa: S608 — tabela limitada à tupla constante local
        require(not operation_ids & {row[0] for row in connection.execute(query)})
    for column, table in (
        ("legacy_event", "skill_mutation_events"),
        ("tree_event", "skill_tree_events"),
        ("adoption_event", "skill_adoption_events"),
        ("publication_event", "skill_publication_events"),
    ):
        query = f"SELECT sequence,{column} FROM skill_publication_timeline WHERE {column} IS NOT NULL ORDER BY sequence"  # noqa: S608 — coluna limitada à tupla constante local
        events = [(row[0], row[1]) for row in connection.execute(query)]
        values = [row[1] for row in events]
        require(values == sorted(set(values)))
        query = f"SELECT sequence FROM {table}"  # noqa: S608 — tabela limitada à tupla constante local
        require(set(values) == {row[0] for row in connection.execute(query)})
    require(
        connection.execute(
            "SELECT 1 FROM skill_publication_events e LEFT JOIN skill_publication_operations o USING(operation_id) WHERE o.operation_id IS NULL LIMIT 1"
        ).fetchone()
        is None
    )


def validate_publication_schema(connection):
    from kairos_skills.mutation_contract import SkillMutationError, require

    try:
        expected = expected_publication_objects()
        names = {row[1] for row in expected}
        tables = {row[2] for row in expected if row[0] == "trigger"}
        actual = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('table','view','trigger') ORDER BY type,name"
            )
            if row[1] in names
            or (row[0] == "trigger" and row[2] in tables)
            or row[1].startswith("skill_publication_")
        )
        require(actual == expected)
        boundary = connection.execute(
            "SELECT singleton,prefix_last_authority_sequence FROM skill_publication_migration"
        ).fetchall()
        require(len(boundary) == 1 and boundary[0][0] == 1 and type(boundary[0][1]) is int)
        prefix = boundary[0][1]
        require(
            prefix
            == connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM skill_authority_timeline"
            ).fetchone()[0]
        )
        old = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT sequence,legacy_event,tree_event,adoption_event FROM skill_authority_timeline ORDER BY sequence"
            )
        )
        copied = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT sequence,legacy_event,tree_event,adoption_event FROM skill_publication_timeline WHERE sequence<=? ORDER BY sequence",
                (prefix,),
            )
        )
        require(copied == old)
        from kairos_state.skill_adoptions_schema import validate_adoption_schema
        from kairos_state.skill_org_schema import validate_org_schema

        validate_adoption_schema(connection, _publication=True)
        validate_org_schema(connection, _publication=True)
        _validate_rows(connection)
    except (SkillMutationError, ValueError, TypeError, KeyError, RecursionError):
        raise sqlite3.DatabaseError("Estado de publicação incompleto ou incompatível.") from None


def _install(connection):
    for name, _, sql in expected_publication_triggers():
        connection.execute('DROP TRIGGER IF EXISTS "' + name + '"')
        connection.execute(sql)


def migrate_publications(connection):
    from kairos_state.repositories.skill_mutations import validate_skill_timeline

    validate_skill_timeline(connection)
    present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    if present & PUBLICATION_TABLES:
        raise sqlite3.DatabaseError("Família de publicação parcial antes da migração.")
    prefix = connection.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM skill_authority_timeline"
    ).fetchone()[0]
    execute_schema(connection, PUBLICATION_SCHEMA_SQL)
    connection.execute(
        "INSERT INTO skill_publication_timeline(sequence,legacy_event,tree_event,adoption_event) SELECT sequence,legacy_event,tree_event,adoption_event FROM skill_authority_timeline ORDER BY sequence"
    )
    connection.execute("INSERT INTO skill_publication_migration VALUES(1,?)", (prefix,))
    _install(connection)
    connection.create_function("kairos_skill_writer_version", 0, lambda: 11)


def repair_publication_objects(connection):
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not tables >= PUBLICATION_TABLES:
        raise sqlite3.DatabaseError("Família de publicação incompleta para reparo.")
    nested = connection.in_transaction
    connection.execute("SAVEPOINT kairos_publication_repair" if nested else "BEGIN IMMEDIATE")
    try:
        _install(connection)
        from kairos_state.skill_org_schema import ORG_VIEW_SQL

        connection.execute("DROP VIEW IF EXISTS skill_org_protections")
        connection.execute(ORG_VIEW_SQL)
        validate_publication_schema(connection)
    except BaseException:
        if nested:
            connection.execute("ROLLBACK TO kairos_publication_repair")
            connection.execute("RELEASE kairos_publication_repair")
        else:
            connection.rollback()
        raise
    else:
        connection.execute("RELEASE kairos_publication_repair") if nested else connection.commit()
