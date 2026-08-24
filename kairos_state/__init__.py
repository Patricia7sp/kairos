"""Camada de estado do Kairos."""

from kairos_state.connection import (
    BUSY_TIMEOUT_MS,
    CJKExtensionUnavailable,
    apply_wal_with_fallback,
    connect,
    default_db_path,
    initialize_schema,
    read_connection,
    read_schema_version,
)
from kairos_state.contention import Budget, get_write_contention_stats
from kairos_state.migrations import backup_corrupt_db, migrate
from kairos_state.schema import LEGACY_SHAPE_VERSION, SCHEMA_VERSION
from kairos_state.writes import WriteGaveUp, write_with_retry

__all__ = [
    "BUSY_TIMEOUT_MS",
    "LEGACY_SHAPE_VERSION",
    "SCHEMA_VERSION",
    "Budget",
    "CJKExtensionUnavailable",
    "WriteGaveUp",
    "apply_wal_with_fallback",
    "backup_corrupt_db",
    "connect",
    "default_db_path",
    "get_write_contention_stats",
    "initialize_schema",
    "migrate",
    "read_connection",
    "read_schema_version",
    "write_with_retry",
]
