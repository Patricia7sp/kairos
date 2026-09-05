"""Transactional persistence for Codex runtime sessions and events."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from kairos_runtime.contracts import RuntimeCapabilities, RuntimeEvent, RuntimeSession
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.policy import capture_directory_identity, negotiate, validate_sandbox

__all__ = ["RuntimeRepository"]


def _error(code: str, message: str, retryable: bool = False) -> RuntimeErrorInfo:
    return RuntimeErrorInfo(code, message, retryable)


def _json_value(value: Any) -> Any:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise _error("invalid_event", "payload de evento inválido")


def _json_dump(value: Any) -> str:
    try:
        return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _error("invalid_event", "payload de evento inválido") from exc


def _snapshot_json(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _error("invalid_event", "payload de evento inválido")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _error("invalid_event", "payload de evento inválido")
        return MappingProxyType({key: _snapshot_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_snapshot_json(item) for item in value)
    raise _error("invalid_event", "payload de evento inválido")


class RuntimeRepository:
    """Synchronous runtime persistence bound to one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_session(
        self,
        session: RuntimeSession,
        source: str,
        parent_session_id: str | None = None,
        *,
        allowed_directories: tuple[str, ...],
        broad_enabled: bool = False,
        consent: bool = False,
    ) -> str:
        if not isinstance(session, RuntimeSession) or session.runtime_kind != "codex":
            raise _error("incompatible", "runtime incompatível")
        if not isinstance(source, str) or not source.strip():
            raise _error("invalid_event", "origem da sessão inválida")
        sandbox = validate_sandbox(session.sandbox, broad_enabled, consent)
        identity = capture_directory_identity(session.cwd, allowed_directories)
        now = time.time()
        broad_consent_at = now if sandbox == "broad_access" and consent else None
        state = "ready"

        self._begin()
        try:
            self._conn.execute(
                "INSERT INTO sessions(id,source,parent_session_id,started_at,cwd,execution_kind) "
                "VALUES (?,?,?,?,?,'agent_runtime')",
                (session.session_id, source, parent_session_id, now, identity.canonical_path),
            )
            self._conn.execute(
                "INSERT INTO runtime_sessions("
                "session_id,runtime_kind,external_thread_id,requested_cwd,canonical_cwd,"
                "sandbox_profile,broad_consent_at,protocol_version,capabilities_json,state,"
                "directory_device,directory_inode,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session.session_id,
                    session.runtime_kind,
                    session.external_thread_id,
                    identity.requested_path,
                    identity.canonical_path,
                    sandbox,
                    broad_consent_at,
                    None,
                    None,
                    state,
                    identity.device,
                    identity.inode,
                    now,
                    now,
                ),
            )
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return session.session_id

    def bind_thread(
        self, session_id: str, thread_id: str, capabilities: RuntimeCapabilities
    ) -> None:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise _error("invalid_event", "thread externo inválido")
        negotiated = negotiate(capabilities)
        payload = _json_dump(
            {
                "protocol_version": negotiated.protocol_version,
                "features": sorted(negotiated.features),
            }
        )
        now = time.time()
        try:
            with self._conn:
                row = self._conn.execute(
                    "SELECT external_thread_id FROM runtime_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise _error("unavailable", "sessão de runtime indisponível")
                if row["external_thread_id"] not in (None, thread_id):
                    raise _error("invalid_event", "identidade do runtime é imutável")
                self._conn.execute(
                    "UPDATE runtime_sessions SET external_thread_id=?,protocol_version=?,"
                    "capabilities_json=?,state='ready',updated_at=? WHERE session_id=?",
                    (thread_id, negotiated.protocol_version, payload, now, session_id),
                )
        except sqlite3.IntegrityError as exc:
            raise _error("invalid_event", "thread externo já associado") from exc

    def get_session(self, session_id: str) -> RuntimeSession:
        row = self._conn.execute(
            "SELECT session_id,runtime_kind,canonical_cwd,sandbox_profile,external_thread_id "
            "FROM runtime_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise _error("unavailable", "sessão de runtime indisponível")
        return RuntimeSession(
            session_id=row["session_id"],
            runtime_kind=row["runtime_kind"],
            cwd=row["canonical_cwd"],
            sandbox=row["sandbox_profile"],
            external_thread_id=row["external_thread_id"],
        )

    def admit(self, session_id: str, key: str, content: str) -> str:
        if not isinstance(key, str) or not key or not isinstance(content, str):
            raise _error("invalid_event", "admissão inválida")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._begin()
        try:
            existing = self._conn.execute(
                "SELECT id,content_hash FROM runtime_turns "
                "WHERE session_id=? AND idempotency_key=?",
                (session_id, key),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != digest:
                    raise _error("idempotency_conflict", "conflito de idempotência")
                self._conn.commit()
                return str(existing["id"])

            session = self._conn.execute(
                "SELECT s.ended_at,r.state,r.external_thread_id,r.protocol_version "
                "FROM sessions s JOIN runtime_sessions r ON r.session_id=s.id WHERE s.id=?",
                (session_id,),
            ).fetchone()
            if (
                session is None
                or session["ended_at"] is not None
                or session["external_thread_id"] is None
                or session["protocol_version"] is None
            ):
                raise _error("unavailable", "sessão de runtime indisponível")
            if session["state"] != "ready":
                if session["state"] in {"unavailable", "ended"}:
                    raise _error("unavailable", "sessão de runtime indisponível")
                raise _error("session_busy", "sessão de runtime não está pronta", True)
            now = time.time()
            message = self._conn.execute(
                "INSERT INTO messages(session_id,role,content,api_content,timestamp) "
                "VALUES (?,'user',?,?,?)",
                (session_id, content, content, now),
            )
            turn_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO runtime_turns("
                "id,session_id,idempotency_key,content_hash,state,send_state,user_message_id,"
                "created_at,updated_at) VALUES (?,?,?,?,'queued','not_sent',?,?,?)",
                (turn_id, session_id, key, digest, message.lastrowid, now, now),
            )
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            if "runtime_turns.session_id" in str(exc):
                raise _error("session_busy", "sessão já possui turno ativo", True) from exc
            raise
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return turn_id

    def append(
        self, turn_id: str, event_id: str, kind: str, payload: Mapping[str, Any]
    ) -> RuntimeEvent:
        if any(not isinstance(value, str) or not value for value in (turn_id, event_id, kind)):
            raise _error("invalid_event", "evento inválido")
        payload_snapshot = _snapshot_json(payload)
        if not isinstance(payload_snapshot, Mapping):
            raise _error("invalid_event", "payload de evento inválido")
        payload_json = _json_dump(payload_snapshot)
        self._begin()
        try:
            turn = self._conn.execute(
                "SELECT session_id FROM runtime_turns WHERE id=?", (turn_id,)
            ).fetchone()
            if turn is None:
                raise _error("invalid_event", "turno do evento inexistente")
            session_id = str(turn["session_id"])
            duplicate = self._conn.execute(
                "SELECT * FROM runtime_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if duplicate is not None:
                if (
                    duplicate["turn_id"] != turn_id
                    or duplicate["session_id"] != session_id
                    or duplicate["kind"] != kind
                    or duplicate["payload_json"] != payload_json
                ):
                    raise _error("invalid_event", "event_id reutilizado com conteúdo diferente")
                event = self._event(duplicate)
                self._conn.commit()
                return event
            sequence_row = self._conn.execute(
                "UPDATE runtime_sessions SET next_sequence=next_sequence+1,updated_at=? "
                "WHERE session_id=? RETURNING next_sequence-1",
                (time.time(), session_id),
            ).fetchone()
            if sequence_row is None:
                raise _error("unavailable", "sessão de runtime indisponível")
            sequence = int(sequence_row[0])
            cursor = f"v1:{session_id}:{sequence}"
            now = time.time()
            self._conn.execute(
                "INSERT INTO runtime_events("
                "event_id,session_id,turn_id,sequence,kind,payload_json,cursor,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?)",
                (event_id, session_id, turn_id, sequence, kind, payload_json, cursor, now, now),
            )
            event = RuntimeEvent(
                1, event_id, session_id, turn_id, sequence, cursor, kind, payload_snapshot
            )
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return event

    def events_after(self, session_id: str, cursor: str | None) -> tuple[RuntimeEvent, ...]:
        start = 0
        if cursor is not None:
            prefix = f"v1:{session_id}:"
            if not isinstance(cursor, str) or not cursor.startswith(prefix):
                raise _error("invalid_event", "cursor de journal inválido")
            raw_sequence = cursor[len(prefix) :]
            if not raw_sequence.isdigit() or int(raw_sequence) <= 0:
                raise _error("invalid_event", "cursor de journal inválido")
            start = int(raw_sequence)
            row = self._conn.execute(
                "SELECT 1 FROM runtime_events WHERE session_id=? AND sequence=? AND cursor=?",
                (session_id, start, cursor),
            ).fetchone()
            if row is None:
                raise _error("invalid_event", "cursor ausente do journal")
        session = self._conn.execute(
            "SELECT 1 FROM runtime_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if session is None:
            raise _error("unavailable", "sessão de runtime indisponível")
        rows = self._conn.execute(
            "SELECT * FROM runtime_events WHERE session_id=? AND sequence>? ORDER BY sequence",
            (session_id, start),
        ).fetchall()
        return tuple(self._event(row) for row in rows)

    def _event(self, row: sqlite3.Row) -> RuntimeEvent:
        return RuntimeEvent(
            protocol_version=1,
            event_id=row["event_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            sequence=int(row["sequence"]),
            cursor=row["cursor"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
        )

    def _begin(self) -> None:
        if self._conn.in_transaction:
            raise sqlite3.OperationalError("runtime write requires a transaction boundary")
        self._conn.execute("BEGIN IMMEDIATE")
