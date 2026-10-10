"""Schema aditivo de snapshots binários e timeline comum de skills."""

SKILL_REMOVALS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_tree_blobs (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 0 AND 16777216),
    content BLOB NOT NULL CHECK(typeof(content)='blob' AND length(content)=size_bytes)
);
CREATE TABLE IF NOT EXISTS skill_tree_snapshots (
    snapshot_id TEXT PRIMARY KEY CHECK(length(snapshot_id)=32),
    manifest_json TEXT NOT NULL CHECK(length(CAST(manifest_json AS BLOB)) BETWEEN 1 AND 20971520)
);
CREATE TABLE IF NOT EXISTS skill_tree_operations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE CHECK(length(operation_id)=32),
    home_id TEXT NOT NULL CHECK(length(home_id)=64),
    action TEXT NOT NULL CHECK(action IN ('remove','restore')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 64),
    actor TEXT NOT NULL CHECK(actor='user_foreground'),
    created_at REAL NOT NULL CHECK(created_at>=0),
    provenance TEXT NOT NULL CHECK(provenance IN ('user','bundled')),
    proof_json TEXT NOT NULL CHECK(length(CAST(proof_json AS BLOB)) BETWEEN 1 AND 20971520),
    snapshot_id TEXT NOT NULL REFERENCES skill_tree_snapshots(snapshot_id),
    reverts TEXT REFERENCES skill_tree_operations(operation_id),
    CHECK((action='remove' AND reverts IS NULL AND snapshot_id=operation_id)
       OR (action='restore' AND reverts IS NOT NULL AND snapshot_id=reverts AND reverts<>operation_id))
);
CREATE TABLE IF NOT EXISTS skill_tree_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES skill_tree_operations(operation_id),
    state TEXT NOT NULL CHECK(state IN ('prepared','committed','aborted','conflict'))
);
CREATE TABLE IF NOT EXISTS skill_mutation_timeline (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_event INTEGER UNIQUE REFERENCES skill_mutation_events(sequence),
    tree_event INTEGER UNIQUE REFERENCES skill_tree_events(sequence),
    CHECK((legacy_event IS NULL) <> (tree_event IS NULL))
);
CREATE INDEX IF NOT EXISTS skill_tree_home_name ON skill_tree_operations(home_id,name,sequence);
CREATE INDEX IF NOT EXISTS skill_tree_event_order ON skill_tree_events(operation_id,sequence);
CREATE INDEX IF NOT EXISTS skill_tree_reverts ON skill_tree_operations(reverts,sequence);
"""

SKILL_REMOVALS_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS skill_tree_blobs_no_replace
BEFORE INSERT ON skill_tree_blobs
WHEN EXISTS (SELECT 1 FROM skill_tree_blobs WHERE sha256=NEW.sha256 OR rowid=NEW.rowid)
BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_tree_snapshots_no_replace
BEFORE INSERT ON skill_tree_snapshots
WHEN EXISTS (SELECT 1 FROM skill_tree_snapshots WHERE snapshot_id=NEW.snapshot_id OR rowid=NEW.rowid)
BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_tree_operations_no_replace
BEFORE INSERT ON skill_tree_operations
WHEN EXISTS (SELECT 1 FROM skill_tree_operations WHERE operation_id=NEW.operation_id OR sequence=NEW.sequence)
BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_tree_events_no_replace
BEFORE INSERT ON skill_tree_events
WHEN EXISTS (SELECT 1 FROM skill_tree_events WHERE sequence=NEW.sequence)
BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;
CREATE TRIGGER IF NOT EXISTS skill_mutation_timeline_no_replace
BEFORE INSERT ON skill_mutation_timeline
WHEN EXISTS (SELECT 1 FROM skill_mutation_timeline
             WHERE sequence=NEW.sequence OR legacy_event=NEW.legacy_event OR tree_event=NEW.tree_event)
BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_operations_no_update
BEFORE UPDATE ON skill_tree_operations BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_operations_no_delete
BEFORE DELETE ON skill_tree_operations BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_events_no_update
BEFORE UPDATE ON skill_tree_events BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_events_no_delete
BEFORE DELETE ON skill_tree_events BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_snapshots_no_update
BEFORE UPDATE ON skill_tree_snapshots BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_snapshots_no_delete
BEFORE DELETE ON skill_tree_snapshots BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_blobs_no_update
BEFORE UPDATE ON skill_tree_blobs BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_blobs_no_delete
BEFORE DELETE ON skill_tree_blobs BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_timeline_no_update
BEFORE UPDATE ON skill_mutation_timeline BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_timeline_no_delete
BEFORE DELETE ON skill_mutation_timeline BEGIN
    SELECT RAISE(ABORT,'skill journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_operations_writer
BEFORE INSERT ON skill_tree_operations BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_events_writer
BEFORE INSERT ON skill_tree_events BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_snapshots_writer
BEFORE INSERT ON skill_tree_snapshots BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_tree_blobs_writer
BEFORE INSERT ON skill_tree_blobs BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_timeline_writer
BEFORE INSERT ON skill_mutation_timeline BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_operations_writer
BEFORE INSERT ON skill_mutation_operations BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_events_writer
BEFORE INSERT ON skill_mutation_events BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_contents_writer
BEFORE INSERT ON skill_mutation_contents BEGIN
    SELECT CASE WHEN kairos_skill_writer_version() IS NOT 8
        THEN RAISE(ABORT,'unsupported skill writer') END;
END;

CREATE TRIGGER IF NOT EXISTS skill_mutation_events_timeline
AFTER INSERT ON skill_mutation_events BEGIN
    INSERT INTO skill_mutation_timeline(legacy_event) VALUES (NEW.sequence);
END;
CREATE TRIGGER IF NOT EXISTS skill_tree_events_timeline
AFTER INSERT ON skill_tree_events BEGIN
    INSERT INTO skill_mutation_timeline(tree_event) VALUES (NEW.sequence);
END;
"""
