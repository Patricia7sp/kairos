"""Família append-only de autoridade e ordenação canônica das três famílias."""

import sqlite3
from functools import lru_cache

from kairos_state.runtime_schema import execute_schema
from kairos_state.skill_mutations_schema import SKILL_MUTATIONS_SCHEMA_SQL
from kairos_state.skill_removals_schema import (
    SKILL_REMOVALS_SCHEMA_SQL,
    SKILL_REMOVALS_TRIGGERS_SQL,
)

ADOPTION_TABLES = frozenset(
    {
        "skill_adoption_operations",
        "skill_adoption_events",
        "skill_tree_adoptions",
        "skill_authority_timeline",
        "skill_authority_migration",
    }
)

SKILL_ADOPTIONS_SCHEMA_SQL = """
CREATE TABLE skill_adoption_operations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE CHECK(length(operation_id)=32),
    home_id TEXT NOT NULL CHECK(length(home_id)=64),
    action TEXT NOT NULL CHECK(action IN ('adopt','unadopt')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 64),
    actor TEXT NOT NULL CHECK(actor='user_foreground'),
    created_at REAL NOT NULL CHECK(created_at>=0),
    adoption_id TEXT NOT NULL REFERENCES skill_adoption_operations(operation_id),
    installation_id TEXT NOT NULL CHECK(length(installation_id)=32),
    device TEXT NOT NULL,
    inode TEXT NOT NULL,
    installation_source TEXT NOT NULL CHECK(installation_source IN ('unknown','local','package_bundle','verified_acquisition')),
    snapshot_id TEXT NOT NULL REFERENCES skill_tree_snapshots(snapshot_id),
    CHECK(snapshot_id=adoption_id AND ((action='adopt' AND adoption_id=operation_id)
         OR (action='unadopt' AND adoption_id<>operation_id)))
);
CREATE TABLE skill_adoption_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES skill_adoption_operations(operation_id),
    state TEXT NOT NULL CHECK(state IN ('prepared','committed','aborted','conflict'))
);
CREATE TABLE skill_tree_adoptions (
    operation_id TEXT PRIMARY KEY REFERENCES skill_tree_operations(operation_id),
    adoption_id TEXT REFERENCES skill_adoption_operations(operation_id),
    installation_id TEXT,
    installation_source TEXT,
    CHECK((adoption_id IS NULL AND installation_id IS NULL AND installation_source IS NULL)
       OR (adoption_id IS NOT NULL AND length(installation_id)=32 AND installation_source IN ('unknown','local','package_bundle','verified_acquisition')))
);
CREATE TABLE skill_authority_timeline (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_event INTEGER UNIQUE REFERENCES skill_mutation_events(sequence),
    tree_event INTEGER UNIQUE REFERENCES skill_tree_events(sequence),
    adoption_event INTEGER UNIQUE REFERENCES skill_adoption_events(sequence),
    CHECK((legacy_event IS NOT NULL)+(tree_event IS NOT NULL)+(adoption_event IS NOT NULL)=1)
);
CREATE TABLE skill_authority_migration (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    prefix_last_sequence INTEGER NOT NULL CHECK(typeof(prefix_last_sequence)='integer' AND prefix_last_sequence>=0)
);
CREATE INDEX skill_adoption_home_name ON skill_adoption_operations(home_id,name,sequence);
CREATE INDEX skill_adoption_event_order ON skill_adoption_events(operation_id,sequence);
CREATE INDEX skill_adoption_founder ON skill_adoption_operations(adoption_id,sequence);
"""

SKILL_ADOPTIONS_TRIGGERS_SQL = """
DROP TRIGGER IF EXISTS skill_mutation_operations_writer;
CREATE TRIGGER skill_mutation_operations_writer BEFORE INSERT ON skill_mutation_operations BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_mutation_events_writer;
CREATE TRIGGER skill_mutation_events_writer BEFORE INSERT ON skill_mutation_events BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_mutation_contents_writer;
CREATE TRIGGER skill_mutation_contents_writer BEFORE INSERT ON skill_mutation_contents BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_tree_operations_writer;
CREATE TRIGGER skill_tree_operations_writer BEFORE INSERT ON skill_tree_operations BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_tree_events_writer;
CREATE TRIGGER skill_tree_events_writer BEFORE INSERT ON skill_tree_events BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_tree_snapshots_writer;
CREATE TRIGGER skill_tree_snapshots_writer BEFORE INSERT ON skill_tree_snapshots BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_tree_blobs_writer;
CREATE TRIGGER skill_tree_blobs_writer BEFORE INSERT ON skill_tree_blobs BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_mutation_timeline_writer;
CREATE TRIGGER skill_mutation_timeline_writer BEFORE INSERT ON skill_mutation_timeline BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_adoption_operations_writer;
CREATE TRIGGER skill_adoption_operations_writer BEFORE INSERT ON skill_adoption_operations BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_adoption_events_writer;
CREATE TRIGGER skill_adoption_events_writer BEFORE INSERT ON skill_adoption_events BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_tree_adoptions_writer;
CREATE TRIGGER skill_tree_adoptions_writer BEFORE INSERT ON skill_tree_adoptions BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_authority_timeline_writer;
CREATE TRIGGER skill_authority_timeline_writer BEFORE INSERT ON skill_authority_timeline BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

DROP TRIGGER IF EXISTS skill_authority_migration_writer;
CREATE TRIGGER skill_authority_migration_writer BEFORE INSERT ON skill_authority_migration BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_authority_migration_no_replace BEFORE INSERT ON skill_authority_migration
WHEN EXISTS (SELECT 1 FROM skill_authority_migration) BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_authority_migration_no_update BEFORE UPDATE ON skill_authority_migration BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_authority_migration_no_delete BEFORE DELETE ON skill_authority_migration BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_adoption_operations_no_replace BEFORE INSERT ON skill_adoption_operations
WHEN EXISTS (SELECT 1 FROM skill_adoption_operations WHERE operation_id=NEW.operation_id OR sequence=NEW.sequence) BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_adoption_operations_no_update BEFORE UPDATE ON skill_adoption_operations BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_adoption_operations_no_delete BEFORE DELETE ON skill_adoption_operations BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_adoption_events_no_replace BEFORE INSERT ON skill_adoption_events
WHEN EXISTS (SELECT 1 FROM skill_adoption_events WHERE sequence=NEW.sequence) BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_adoption_events_no_update BEFORE UPDATE ON skill_adoption_events BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_adoption_events_no_delete BEFORE DELETE ON skill_adoption_events BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_adoptions_no_replace BEFORE INSERT ON skill_tree_adoptions
WHEN EXISTS (SELECT 1 FROM skill_tree_adoptions WHERE operation_id=NEW.operation_id OR rowid=NEW.rowid) BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_adoptions_no_update BEFORE UPDATE ON skill_tree_adoptions BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_adoptions_no_delete BEFORE DELETE ON skill_tree_adoptions BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_authority_timeline_no_replace BEFORE INSERT ON skill_authority_timeline
WHEN EXISTS (SELECT 1 FROM skill_authority_timeline WHERE sequence=NEW.sequence OR legacy_event=NEW.legacy_event OR tree_event=NEW.tree_event OR adoption_event=NEW.adoption_event) BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_authority_timeline_no_update BEFORE UPDATE ON skill_authority_timeline BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_authority_timeline_no_delete BEFORE DELETE ON skill_authority_timeline BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_timeline_frozen BEFORE INSERT ON skill_mutation_timeline BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 9
        THEN RAISE(ABORT,'unsupported skill writer') END;
    SELECT RAISE(ABORT,'historical skill timeline is frozen');
END;

DROP TRIGGER IF EXISTS skill_mutation_events_timeline;
CREATE TRIGGER skill_mutation_events_timeline AFTER INSERT ON skill_mutation_events BEGIN
    INSERT INTO skill_authority_timeline(legacy_event) VALUES (NEW.sequence);
END;

DROP TRIGGER IF EXISTS skill_tree_events_timeline;
CREATE TRIGGER skill_tree_events_timeline AFTER INSERT ON skill_tree_events BEGIN
    INSERT INTO skill_authority_timeline(tree_event) VALUES (NEW.sequence);
END;

DROP TRIGGER IF EXISTS skill_adoption_events_timeline;
CREATE TRIGGER skill_adoption_events_timeline AFTER INSERT ON skill_adoption_events BEGIN
    INSERT INTO skill_authority_timeline(adoption_event) VALUES (NEW.sequence);
END;
"""


def install_adoption_triggers(connection: sqlite3.Connection) -> None:
    execute_schema(connection, SKILL_ADOPTIONS_TRIGGERS_SQL)


@lru_cache(maxsize=1)
def _expected_skill_triggers() -> tuple[tuple[str, str, str], ...]:
    """Obtém o catálogo esperado executando os DDLs confiáveis no próprio SQLite."""
    reference = sqlite3.connect(":memory:")
    try:
        for script in (
            SKILL_MUTATIONS_SCHEMA_SQL,
            SKILL_REMOVALS_SCHEMA_SQL,
            SKILL_REMOVALS_TRIGGERS_SQL,
            SKILL_ADOPTIONS_SCHEMA_SQL,
            SKILL_ADOPTIONS_TRIGGERS_SQL,
        ):
            execute_schema(reference, script)
        return tuple(
            tuple(row)
            for row in reference.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        )
    finally:
        reference.close()


def repair_adoption_triggers(connection: sqlite3.Connection) -> None:
    """Reconstrói apenas guards conhecidos; não apaga triggers desconhecidos."""
    version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
    if version == 10:
        from kairos_state.skill_org_schema import repair_org_triggers

        repair_org_triggers(connection)
        return
    expected = _expected_skill_triggers()
    nested = connection.in_transaction
    if nested:
        connection.execute("SAVEPOINT kairos_repair_skill_triggers")
    else:
        connection.execute("BEGIN IMMEDIATE")
    try:
        protected_tables = {row[1] for row in expected}
        present_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not protected_tables <= present_tables:
            raise sqlite3.DatabaseError("schema de skills incompleto para reparo de guards")

        for name, _, _ in expected:
            quoted = '"' + name.replace('"', '""') + '"'
            connection.execute(f"DROP TRIGGER IF EXISTS {quoted}")
        for script in (
            SKILL_MUTATIONS_SCHEMA_SQL,
            SKILL_REMOVALS_SCHEMA_SQL,
            SKILL_REMOVALS_TRIGGERS_SQL,
        ):
            execute_schema(connection, script)
        install_adoption_triggers(connection)
    except BaseException:
        if nested:
            connection.execute("ROLLBACK TO kairos_repair_skill_triggers")
            connection.execute("RELEASE kairos_repair_skill_triggers")
        else:
            connection.rollback()
        raise
    else:
        if nested:
            connection.execute("RELEASE kairos_repair_skill_triggers")
        else:
            connection.commit()


def validate_adoption_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not tables >= ADOPTION_TABLES:
        raise sqlite3.DatabaseError("schema de adoção incompleto")
    version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
    if version == 10:
        from kairos_state.skill_org_schema import expected_org_triggers

        expected_triggers = expected_org_triggers()
    else:
        expected_triggers = _expected_skill_triggers()
    guarded_tables = {row[1] for row in expected_triggers}
    observed_triggers = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        )
        if row[1] in guarded_tables
    )
    if observed_triggers != expected_triggers:
        raise sqlite3.DatabaseError("guards do schema de skills ausentes ou incompatíveis")
    metadata = connection.execute(
        "SELECT singleton,prefix_last_sequence FROM skill_authority_migration"
    ).fetchall()
    if len(metadata) != 1 or metadata[0][0] != 1:
        raise sqlite3.DatabaseError("fronteira histórica de skills ausente ou incompatível")
    boundary = metadata[0][1]
    historical_last = connection.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM skill_mutation_timeline"
    ).fetchone()[0]
    if not isinstance(boundary, int) or boundary < 0 or boundary != historical_last:
        raise sqlite3.DatabaseError("fronteira histórica de skills incompatível")
    # A igualdade vale nos dois sentidos, incluindo lacunas e prefixo vazio.
    mismatch = connection.execute(
        """
WITH historical AS (
    SELECT sequence,legacy_event,tree_event,NULL AS adoption_event FROM skill_mutation_timeline
), imported AS (
    SELECT sequence,legacy_event,tree_event,adoption_event FROM skill_authority_timeline WHERE sequence<=?
)
SELECT 1 FROM (SELECT * FROM historical EXCEPT SELECT * FROM imported)
UNION ALL
SELECT 1 FROM (SELECT * FROM imported EXCEPT SELECT * FROM historical)
LIMIT 1
""",
        (boundary,),
    ).fetchone()
    if mismatch is not None:
        raise sqlite3.DatabaseError("prefixo histórico de skills incompatível")


def migrate_adoptions(connection: sqlite3.Connection) -> None:
    from kairos_skills.mutation_contract import SkillMutationError
    from kairos_state.repositories.skill_mutations import validate_skill_timeline

    present = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if present & ADOPTION_TABLES:
        raise sqlite3.DatabaseError("schema de adoção parcial ou incompatível")
    try:
        validate_skill_timeline(connection)
    except SkillMutationError:
        raise sqlite3.DatabaseError("prefixo histórico de skills incompleto") from None
    execute_schema(connection, SKILL_ADOPTIONS_SCHEMA_SQL)
    connection.execute(
        "INSERT INTO skill_authority_timeline(sequence,legacy_event,tree_event) SELECT sequence,legacy_event,tree_event FROM skill_mutation_timeline ORDER BY sequence"
    )
    connection.execute(
        "INSERT INTO skill_tree_adoptions(operation_id) SELECT operation_id FROM skill_tree_operations"
    )
    connection.execute(
        "INSERT INTO skill_authority_migration(singleton,prefix_last_sequence) SELECT 1,COALESCE(MAX(sequence),0) FROM skill_mutation_timeline"
    )
    install_adoption_triggers(connection)
    validate_adoption_schema(connection)
