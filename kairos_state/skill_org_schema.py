"""Proteção organizacional ligada ao mesmo commit do journal de instalação."""

import hashlib
import json
import sqlite3
from functools import lru_cache

from kairos_state.runtime_schema import execute_schema
from kairos_state.skill_adoptions_schema import (
    SKILL_ADOPTIONS_SCHEMA_SQL,
    SKILL_ADOPTIONS_TRIGGERS_SQL,
)
from kairos_state.skill_mutations_schema import SKILL_MUTATIONS_SCHEMA_SQL
from kairos_state.skill_removals_schema import (
    SKILL_REMOVALS_SCHEMA_SQL,
    SKILL_REMOVALS_TRIGGERS_SQL,
)

ORG_TABLES = frozenset(
    {
        "skill_org_migration",
        "skill_org_intents",
        "skill_org_intent_bindings",
        "skill_org_events",
    }
)
ORG_OBJECTS = ORG_TABLES | {"skill_org_protections"}
ORG_VIEW_SQL = """CREATE VIEW skill_org_protections AS
SELECT DISTINCT home_id,name,organization_id FROM skill_org_events"""
ORG_SCHEMA_SQL = """
CREATE TABLE skill_org_migration (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    prefix_last_authority_sequence INTEGER NOT NULL CHECK(typeof(prefix_last_authority_sequence)='integer' AND prefix_last_authority_sequence>=0)
);
CREATE TABLE skill_org_intents (
    operation_id TEXT PRIMARY KEY CHECK(length(operation_id)=32),
    home_id TEXT NOT NULL CHECK(length(home_id)=64),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 64),
    observation_id TEXT NOT NULL CHECK(length(observation_id)=32),
    policy_digest TEXT CHECK(policy_digest IS NULL OR length(policy_digest)=64),
    bindings_digest TEXT NOT NULL CHECK(length(bindings_digest)=64)
);
CREATE TABLE skill_org_intent_bindings (
    operation_id TEXT NOT NULL REFERENCES skill_org_intents(operation_id),
    organization_id TEXT NOT NULL CHECK(length(organization_id) BETWEEN 1 AND 64),
    active INTEGER NOT NULL CHECK(typeof(active)='integer' AND active IN (0,1)),
    PRIMARY KEY(operation_id,organization_id)
);
CREATE TABLE skill_org_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES skill_org_intents(operation_id),
    installation_id TEXT NOT NULL CHECK(installation_id=operation_id),
    home_id TEXT NOT NULL CHECK(length(home_id)=64),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 64),
    organization_id TEXT NOT NULL,
    policy_digest TEXT NOT NULL CHECK(length(policy_digest)=64),
    observation_id TEXT NOT NULL CHECK(length(observation_id)=32),
    UNIQUE(operation_id,organization_id),
    FOREIGN KEY(operation_id,organization_id) REFERENCES skill_org_intent_bindings(operation_id,organization_id)
);
"""


def bindings_digest(bindings) -> str:
    value = sorted((binding.organization_id, binding.active) for binding in bindings)
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _org_triggers() -> str:
    keys = {
        "skill_org_migration": "singleton=NEW.singleton",
        "skill_org_intents": "operation_id=NEW.operation_id OR rowid=NEW.rowid",
        "skill_org_intent_bindings": "(operation_id=NEW.operation_id AND organization_id=NEW.organization_id) OR rowid=NEW.rowid",
        "skill_org_events": "sequence=NEW.sequence OR (operation_id=NEW.operation_id AND organization_id=NEW.organization_id)",
    }
    statements = []
    for table, key in keys.items():
        statements.append(f"""
CREATE TRIGGER {table}_writer BEFORE INSERT ON {table} BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 10 THEN RAISE(ABORT,'unsupported skill writer') END;
END;
CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}) BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;
CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;
""")  # noqa: S608 — nomes e predicados vêm somente do mapa constante local
    statements.append("""
CREATE TRIGGER skill_org_intents_parent BEFORE INSERT ON skill_org_intents BEGIN
    SELECT CASE WHEN NOT EXISTS(
        SELECT 1 FROM skill_mutation_operations WHERE operation_id=NEW.operation_id
            AND action='create' AND home_id=NEW.home_id AND name=NEW.name
        UNION ALL
        SELECT 1 FROM skill_tree_operations WHERE operation_id=NEW.operation_id
            AND action='restore' AND home_id=NEW.home_id AND name=NEW.name
    ) THEN RAISE(ABORT,'invalid protection parent') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM skill_mutation_events WHERE operation_id=NEW.operation_id)
        OR EXISTS(SELECT 1 FROM skill_tree_events WHERE operation_id=NEW.operation_id)
        THEN RAISE(ABORT,'protection must accompany preparation') END;
END;
""")
    for table, operations, action, timeline_key in (
        ("skill_mutation_events", "skill_mutation_operations", "create", "legacy_event"),
        ("skill_tree_events", "skill_tree_operations", "restore", "tree_event"),
    ):
        statements.append(f"""
CREATE TRIGGER {table}_org_required BEFORE INSERT ON {table}
WHEN NEW.state='committed' AND EXISTS(SELECT 1 FROM {operations}
    WHERE operation_id=NEW.operation_id AND action='{action}') BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM skill_org_intents WHERE operation_id=NEW.operation_id)
        AND NOT EXISTS(SELECT 1 FROM {table} e JOIN skill_authority_timeline t
            ON t.{timeline_key}=e.sequence CROSS JOIN skill_org_migration m
            WHERE e.operation_id=NEW.operation_id AND e.state='prepared'
                AND t.sequence<=m.prefix_last_authority_sequence)
        THEN RAISE(ABORT,'installation requires policy receipt') END;
END;
CREATE TRIGGER {table}_org_committed AFTER INSERT ON {table}
WHEN NEW.state='committed' BEGIN
    INSERT INTO skill_org_events(operation_id,installation_id,home_id,name,organization_id,policy_digest,observation_id)
    SELECT i.operation_id,i.operation_id,i.home_id,i.name,b.organization_id,i.policy_digest,i.observation_id
    FROM skill_org_intents i JOIN skill_org_intent_bindings b USING(operation_id)
    WHERE i.operation_id=NEW.operation_id;
END;
""")  # noqa: S608 — tabelas, ação e chave vêm somente da tupla constante local
    return "\n".join(statements)


def _install_triggers(connection):
    connection.execute("DROP TRIGGER IF EXISTS skill_mutation_timeline_frozen")
    execute_schema(connection, SKILL_ADOPTIONS_TRIGGERS_SQL.replace("IS NOT 9", "IS NOT 10"))
    execute_schema(connection, _org_triggers())


@lru_cache(maxsize=1)
def _reference_objects():
    reference = sqlite3.connect(":memory:")
    try:
        for script in (
            SKILL_MUTATIONS_SCHEMA_SQL,
            SKILL_REMOVALS_SCHEMA_SQL,
            SKILL_REMOVALS_TRIGGERS_SQL,
            SKILL_ADOPTIONS_SCHEMA_SQL,
            SKILL_ADOPTIONS_TRIGGERS_SQL,
            ORG_SCHEMA_SQL,
        ):
            execute_schema(reference, script)
        reference.execute(ORG_VIEW_SQL)
        _install_triggers(reference)
        return tuple(
            tuple(row)
            for row in reference.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('trigger','table','view') ORDER BY type,name"
            )
        )
    finally:
        reference.close()


def expected_org_triggers():
    return tuple(
        (name, table, sql) for kind, name, table, sql in _reference_objects() if kind == "trigger"
    )


def read_org_receipt(connection, operation_id):
    from kairos_skills.mutation_contract import SkillMutationError, validate_id
    from kairos_skills.org_policy_contract import OrgPolicyReceipt, OrgProtectionBinding

    try:
        validate_id(operation_id)
    except SkillMutationError:
        raise sqlite3.DatabaseError("operação de proteção inválida") from None
    row = connection.execute(
        "SELECT home_id,name,observation_id,policy_digest,bindings_digest FROM skill_org_intents WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        return None
    rows = connection.execute(
        "SELECT organization_id,active FROM skill_org_intent_bindings WHERE operation_id=? ORDER BY organization_id",
        (operation_id,),
    ).fetchall()
    try:
        if any(type(active) is not int or active not in (0, 1) for _, active in rows):
            raise ValueError
        receipt = OrgPolicyReceipt(
            row[0],
            row[1],
            row[2],
            row[3],
            tuple(OrgProtectionBinding(org, bool(active)) for org, active in rows),
        )
        if bindings_digest(receipt.bindings) != row[4]:
            raise ValueError
        return receipt
    except (SkillMutationError, TypeError, ValueError):
        raise sqlite3.DatabaseError("recibo organizacional incoerente") from None


def validate_org_schema(connection: sqlite3.Connection) -> None:
    expected = _reference_objects()
    objects = {
        name: (kind, table, sql)
        for kind, name, table, sql in expected
        if kind == "trigger" or name in ORG_TABLES or name == "skill_org_protections"
    }
    observed = {
        row[1]: (row[0], row[2], row[3])
        for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master")
        if row[1] in objects
    }
    if observed != objects:
        raise sqlite3.DatabaseError("schema de proteção incompleto ou incompatível")
    protected_tables = {table for kind, _, table, _ in expected if kind == "trigger"}
    actual_triggers = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        )
        if row[1] in protected_tables
    )
    if actual_triggers != expected_org_triggers():
        raise sqlite3.DatabaseError("guards de proteção incompatíveis")
    boundary = connection.execute(
        "SELECT singleton,prefix_last_authority_sequence FROM skill_org_migration"
    ).fetchall()
    if (
        len(boundary) != 1
        or boundary[0][0] != 1
        or type(boundary[0][1]) is not int
        or boundary[0][1] < 0
    ):
        raise sqlite3.DatabaseError("fronteira de proteção incompatível")
    latest = connection.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM skill_authority_timeline"
    ).fetchone()[0]
    if boundary[0][1] > latest:
        raise sqlite3.DatabaseError("fronteira de proteção futura")
    installations = connection.execute("""
SELECT o.operation_id,o.home_id,o.name,MIN(t.sequence),MAX(e.state='committed')
FROM skill_mutation_operations o JOIN skill_mutation_events e USING(operation_id)
JOIN skill_authority_timeline t ON t.legacy_event=e.sequence WHERE o.action='create' GROUP BY o.operation_id
UNION ALL
SELECT o.operation_id,o.home_id,o.name,MIN(t.sequence),MAX(e.state='committed')
FROM skill_tree_operations o JOIN skill_tree_events e USING(operation_id)
JOIN skill_authority_timeline t ON t.tree_event=e.sequence WHERE o.action='restore' GROUP BY o.operation_id
""").fetchall()
    by_id = {row[0]: row for row in installations}
    intents = {row[0] for row in connection.execute("SELECT operation_id FROM skill_org_intents")}
    required = {row[0] for row in installations if row[3] > boundary[0][1]}
    if intents != required:
        raise sqlite3.DatabaseError("instalação sem recibo ou recibo sem instalação")
    expected_events = set()
    for operation_id in intents:
        receipt = read_org_receipt(connection, operation_id)
        parent = by_id[operation_id]
        if (receipt.home_id, receipt.name) != (parent[1], parent[2]):
            raise sqlite3.DatabaseError("recibo fora de seu home/nome")
        if parent[4]:
            expected_events.update(
                (
                    operation_id,
                    operation_id,
                    receipt.home_id,
                    receipt.name,
                    binding.organization_id,
                    receipt.source_digest,
                    receipt.observation_id,
                )
                for binding in receipt.bindings
            )
    events = [
        tuple(row)
        for row in connection.execute(
            "SELECT operation_id,installation_id,home_id,name,organization_id,policy_digest,observation_id FROM skill_org_events ORDER BY sequence"
        )
    ]
    if len(events) != len(set(events)) or set(events) != expected_events:
        raise sqlite3.DatabaseError("eventos de proteção incompletos ou divergentes")
    if (
        connection.execute("""
SELECT 1 FROM skill_org_intent_bindings b LEFT JOIN skill_org_intents i USING(operation_id)
WHERE i.operation_id IS NULL LIMIT 1
""").fetchone()
        is not None
    ):
        raise sqlite3.DatabaseError("binding órfão")
    projection = {
        tuple(row)
        for row in connection.execute(
            "SELECT home_id,name,organization_id FROM skill_org_protections"
        )
    }
    if projection != {(row[2], row[3], row[4]) for row in expected_events}:
        raise sqlite3.DatabaseError("projeção de proteção divergente")


def migrate_org_protections(connection: sqlite3.Connection) -> None:
    from kairos_state.repositories.skill_mutations import validate_skill_timeline

    validate_skill_timeline(connection)
    present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    if present & (ORG_TABLES | {"skill_org_protections"}):
        raise sqlite3.DatabaseError("família organizacional parcial antes da migração")
    boundary = connection.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM skill_authority_timeline"
    ).fetchone()[0]
    execute_schema(connection, ORG_SCHEMA_SQL)
    connection.execute(ORG_VIEW_SQL)
    _install_triggers(connection)
    connection.create_function("kairos_skill_writer_version", 0, lambda: 10)
    connection.execute("INSERT INTO skill_org_migration VALUES(1,?)", (boundary,))
    validate_org_schema(connection)


def repair_org_triggers(connection: sqlite3.Connection) -> None:
    nested = connection.in_transaction
    connection.execute("SAVEPOINT kairos_org_repair" if nested else "BEGIN IMMEDIATE")
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not tables >= ORG_TABLES:
            raise sqlite3.DatabaseError("família organizacional incompleta para reparo")
        for name, _, sql in expected_org_triggers():
            connection.execute('DROP TRIGGER IF EXISTS "' + name + '"')
            connection.execute(sql)
        connection.execute("DROP VIEW IF EXISTS skill_org_protections")
        connection.execute(ORG_VIEW_SQL)
        validate_org_schema(connection)
    except BaseException:
        if nested:
            connection.execute("ROLLBACK TO kairos_org_repair")
            connection.execute("RELEASE kairos_org_repair")
        else:
            connection.rollback()
        raise
    else:
        connection.execute("RELEASE kairos_org_repair") if nested else connection.commit()
