"""Schema v2 for durable agent-runtime sessions and their journal."""

from __future__ import annotations

import sqlite3

__all__ = ["RUNTIME_SCHEMA_SQL", "execute_schema"]


RUNTIME_SCHEMA_SQL = """
ALTER TABLE sessions ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'model'
    CHECK(execution_kind IN ('model','agent_runtime'));

CREATE TABLE runtime_sessions (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
    runtime_kind TEXT NOT NULL CHECK(runtime_kind='codex'),
    external_thread_id TEXT UNIQUE,
    requested_cwd TEXT NOT NULL,
    canonical_cwd TEXT NOT NULL,
    sandbox_profile TEXT NOT NULL
        CHECK(sandbox_profile IN ('read_only','workspace_write','broad_access')),
    broad_consent_at REAL,
    protocol_version INTEGER CHECK(protocol_version IS NULL OR protocol_version = 1),
    capabilities_json TEXT,
    state TEXT NOT NULL CHECK(state IN (
        'ready','running','waiting_approval','recovering','interrupted','unavailable','ended'
    )),
    next_sequence INTEGER NOT NULL DEFAULT 1 CHECK(next_sequence > 0),
    directory_device INTEGER NOT NULL,
    directory_inode INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE runtime_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id),
    idempotency_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    external_turn_id TEXT,
    state TEXT NOT NULL CHECK(state IN (
        'queued','starting','running','waiting_approval','cancelling','recovering',
        'completed','failed','cancelled','interrupted'
    )),
    send_state TEXT NOT NULL CHECK(send_state IN (
        'not_sent','dispatching','confirmed','uncertain'
    )),
    user_message_id INTEGER REFERENCES messages(id),
    assistant_message_id INTEGER REFERENCES messages(id),
    inactive_confirmed_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(session_id,idempotency_key),
    UNIQUE(id,session_id)
);

CREATE TABLE runtime_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    cursor TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(session_id,sequence),
    FOREIGN KEY(turn_id,session_id) REFERENCES runtime_turns(id,session_id)
);

CREATE TABLE runtime_approvals (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES runtime_turns(id),
    process_generation TEXT NOT NULL,
    external_request_id TEXT NOT NULL,
    external_item_id TEXT,
    request_json TEXT NOT NULL,
    decision TEXT,
    decided_at REAL,
    delivery_state TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(process_generation,external_request_id)
);

CREATE TABLE runtime_directory_leases (
    canonical_cwd TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES runtime_turns(id),
    holder TEXT NOT NULL,
    generation INTEGER NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE runtime_queue (
    ticket INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL UNIQUE REFERENCES runtime_turns(id),
    canonical_cwd TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX idx_runtime_turns_one_active
    ON runtime_turns(session_id)
    WHERE state NOT IN ('completed','failed','cancelled','interrupted');
CREATE INDEX idx_runtime_queue_order
    ON runtime_queue(canonical_cwd,state,ticket);
CREATE INDEX idx_runtime_approvals_delivery
    ON runtime_approvals(turn_id,delivery_state);
CREATE INDEX idx_runtime_directory_leases_expiry
    ON runtime_directory_leases(expires_at);

CREATE TRIGGER runtime_sessions_identity_no_replace
BEFORE INSERT ON runtime_sessions
WHEN EXISTS (
    SELECT 1 FROM runtime_sessions AS bound
     WHERE bound.external_thread_id IS NOT NULL
       AND (
           bound.session_id = NEW.session_id OR
           bound.external_thread_id = NEW.external_thread_id
       )
)
BEGIN
    SELECT RAISE(ABORT, 'bound runtime identity cannot be replaced');
END;

CREATE TRIGGER runtime_sessions_identity_immutable
BEFORE UPDATE OF runtime_kind,external_thread_id,requested_cwd,canonical_cwd,sandbox_profile,
    broad_consent_at,directory_device,directory_inode
ON runtime_sessions
WHEN OLD.external_thread_id IS NOT NULL AND (
    NEW.runtime_kind IS NOT OLD.runtime_kind OR
    NEW.external_thread_id IS NOT OLD.external_thread_id OR
    NEW.requested_cwd IS NOT OLD.requested_cwd OR
    NEW.canonical_cwd IS NOT OLD.canonical_cwd OR
    NEW.sandbox_profile IS NOT OLD.sandbox_profile OR
    NEW.broad_consent_at IS NOT OLD.broad_consent_at OR
    NEW.directory_device IS NOT OLD.directory_device OR
    NEW.directory_inode IS NOT OLD.directory_inode
)
BEGIN
    SELECT RAISE(ABORT, 'runtime identity is immutable after bind');
END;

CREATE TRIGGER runtime_sessions_identity_no_delete
BEFORE DELETE ON runtime_sessions
BEGIN
    SELECT RAISE(ABORT, 'runtime identity cannot be deleted');
END;

CREATE TRIGGER sessions_execution_kind_no_replace
BEFORE INSERT ON sessions
WHEN EXISTS (
    SELECT 1
      FROM sessions AS bound_session
      JOIN runtime_sessions AS runtime
        ON runtime.session_id = bound_session.id
     WHERE bound_session.id = NEW.id
       AND bound_session.execution_kind = 'agent_runtime'
       AND runtime.external_thread_id IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'bound runtime execution kind cannot be replaced');
END;

CREATE TRIGGER sessions_execution_kind_immutable
BEFORE UPDATE OF execution_kind ON sessions
WHEN OLD.execution_kind = 'agent_runtime'
 AND EXISTS (
    SELECT 1 FROM runtime_sessions
     WHERE session_id = OLD.id AND external_thread_id IS NOT NULL
 )
 AND NEW.execution_kind IS NOT OLD.execution_kind
BEGIN
    SELECT RAISE(ABORT, 'runtime execution kind is immutable after bind');
END;
"""


def execute_schema(conn: sqlite3.Connection, script: str) -> None:
    """Execute complete SQLite statements without ``executescript`` commits.

    ``sqlite3.complete_statement`` understands trigger bodies, so semicolons
    inside ``BEGIN ... END`` are not mistaken for migration boundaries.
    """

    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete schema statement")
