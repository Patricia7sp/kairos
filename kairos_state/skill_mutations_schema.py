"""Journal aditivo de autoria manual; conteúdo e eventos canônicos."""

SKILL_MUTATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_mutation_contents (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
    text TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 65536)
);
CREATE TABLE IF NOT EXISTS skill_mutation_operations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE CHECK(length(operation_id)=32),
    home_id TEXT NOT NULL CHECK(length(home_id)=64),
    action TEXT NOT NULL CHECK(action IN ('create','rollback')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 64),
    actor TEXT NOT NULL CHECK(actor='user_foreground'),
    created_at REAL NOT NULL CHECK(created_at>=0),
    identity_json TEXT NOT NULL CHECK(length(CAST(identity_json AS BLOB))<=1024),
    sha256 TEXT NOT NULL REFERENCES skill_mutation_contents(sha256),
    size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 65536),
    reverts TEXT REFERENCES skill_mutation_operations(operation_id),
    CHECK((action='create' AND reverts IS NULL) OR (action='rollback' AND reverts IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS skill_mutation_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES skill_mutation_operations(operation_id),
    state TEXT NOT NULL CHECK(state IN ('prepared','committed','aborted','conflict'))
);
CREATE INDEX IF NOT EXISTS skill_mutation_home_name
    ON skill_mutation_operations(home_id,name,sequence);
CREATE INDEX IF NOT EXISTS skill_mutation_event_order
    ON skill_mutation_events(operation_id,sequence);
CREATE INDEX IF NOT EXISTS skill_mutation_reverts
    ON skill_mutation_operations(reverts,sequence);
CREATE TRIGGER IF NOT EXISTS skill_mutation_operations_no_update
BEFORE UPDATE ON skill_mutation_operations BEGIN
    SELECT RAISE(ABORT,'skill mutation operations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_mutation_operations_no_delete
BEFORE DELETE ON skill_mutation_operations BEGIN
    SELECT RAISE(ABORT,'skill mutation operations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_mutation_events_no_update
BEFORE UPDATE ON skill_mutation_events BEGIN
    SELECT RAISE(ABORT,'skill mutation events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_mutation_events_no_delete
BEFORE DELETE ON skill_mutation_events BEGIN
    SELECT RAISE(ABORT,'skill mutation events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_mutation_contents_no_update
BEFORE UPDATE ON skill_mutation_contents BEGIN
    SELECT RAISE(ABORT,'skill mutation contents are append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_mutation_contents_no_delete
BEFORE DELETE ON skill_mutation_contents BEGIN
    SELECT RAISE(ABORT,'skill mutation contents are append-only');
END;
"""
