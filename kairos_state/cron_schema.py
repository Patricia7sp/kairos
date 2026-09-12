"""Schema v3: durable scheduler attempts, independent of mutable job definitions."""

CRON_SCHEMA_SQL = """
CREATE TABLE executions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    process_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    process_started_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('claimed','running','completed','failed','unknown')),
    scheduled_at TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    conversation_id TEXT NOT NULL,
    UNIQUE(job_id, scheduled_at)
);
CREATE INDEX idx_executions_job_claimed ON executions(job_id, claimed_at DESC, id DESC);
CREATE INDEX idx_executions_status_claimed ON executions(status, claimed_at DESC, id DESC);
CREATE TRIGGER executions_terminal_immutable BEFORE UPDATE ON executions
WHEN OLD.status IN ('completed','failed','unknown')
BEGIN SELECT RAISE(ABORT, 'terminal execution is immutable'); END;
"""
