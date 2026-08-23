"""Camada de estado do Kairos."""

from kairos_state.connection import (
    BUSY_TIMEOUT_MS,
    CJKExtensionUnavailable,
    apply_wal_with_fallback,
    read_connection,
    connect,
    default_db_path,
    initialize_schema,
    read_schema_version,
)
from kairos_state.contention import Budget, get_write_contention_stats
from kairos_state.migrations import backup_corrupt_db, migrate
from kairos_state.schema import LEGACY_SHAPE_VERSION, SCHEMA_VERSION
from kairos_state.writes import WriteGaveUp, write_with_retry

__all__ = [
    "CJKExtensionUnavailable",
    "connect",
    "default_db_path",
    "initialize_schema",
    "read_schema_version",
    "SCHEMA_VERSION",
    "LEGACY_SHAPE_VERSION",
    "apply_wal_with_fallback", "read_connection", "BUSY_TIMEOUT_MS",
    "Budget", "get_write_contention_stats",
    "write_with_retry", "WriteGaveUp",
    "migrate", "backup_corrupt_db",
]
