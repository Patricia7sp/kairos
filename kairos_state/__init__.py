"""Camada de estado do Kairos."""

from kairos_state.connection import (
    CJKExtensionUnavailable,
    connect,
    default_db_path,
    initialize_schema,
    read_schema_version,
)
from kairos_state.schema import LEGACY_SHAPE_VERSION, SCHEMA_VERSION

__all__ = [
    "CJKExtensionUnavailable",
    "connect",
    "default_db_path",
    "initialize_schema",
    "read_schema_version",
    "SCHEMA_VERSION",
    "LEGACY_SHAPE_VERSION",
]
