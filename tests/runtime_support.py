from __future__ import annotations

from pathlib import Path

from kairos_runtime import RuntimeCapabilities, RuntimeSession
from kairos_state import connect, initialize_schema


def open_runtime_db(path: Path):
    connection = connect(path)
    initialize_schema(connection)
    return connection


def runtime_session(project: Path, session_id: str = "runtime-1") -> RuntimeSession:
    return RuntimeSession(
        session_id=session_id,
        runtime_kind="codex",
        cwd=str(project),
        sandbox="workspace_write",
    )


def capabilities(*features: str) -> RuntimeCapabilities:
    return RuntimeCapabilities(protocol_version=1, features=frozenset(features or ("text",)))
