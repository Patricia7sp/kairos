"""Transactional persistence for Codex runtime sessions and events."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any

from kairos_runtime.contracts import RuntimeCapabilities, RuntimeEvent, RuntimeSession
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.policy import (
    DirectoryIdentity,
    capture_directory_identity,
    negotiate,
    revalidate_directory_identity,
    validate_sandbox,
)
from kairos_state.writes import is_busy_error

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
        return json.dumps(
            _json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
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
        self._fence: tuple[str, str, int] | None = None
        self._allow_expired = False

    def owned(
        self,
        turn_id: str,
        holder: str,
        generation: int,
        operation: str,
        *args,
        allow_expired: bool = False,
    ):
        if operation not in {
            "append",
            "dispatch",
            "confirm_dispatch",
            "save_approval",
            "finish",
            "transition",
            "decide_approval",
            "continue_after_approval",
        }:
            raise ValueError("unsupported owned runtime operation")
        self._fence = (turn_id, holder, generation)
        self._allow_expired = allow_expired
        try:
            return getattr(self, operation)(*args)
        finally:
            self._fence = None
            self._allow_expired = False

    def adopt_runtime_turn(
        self,
        turn_id: str,
        previous_holder: str,
        previous_generation: int,
        holder: str,
        *,
        confirmed_inactive: bool,
        ttl: float,
        clock,
    ) -> int | None:
        with self._transaction():
            turn = self.get_turn(turn_id)
            now = clock()
            row = self._conn.execute(
                "SELECT d.*,s.holder AS session_holder,s.expires_at AS session_expiry,r.requested_cwd,r.directory_device,r.directory_inode "
                "FROM runtime_directory_leases d JOIN runtime_turns t ON t.id=d.turn_id "
                "JOIN runtime_sessions r ON r.session_id=t.session_id "
                "JOIN session_turn_leases s ON s.conversation_id=t.session_id "
                "WHERE d.turn_id=? AND d.holder=? AND d.generation=?",
                (turn_id, previous_holder, previous_generation),
            ).fetchone()
            if (
                row is None
                or row["session_holder"] != previous_holder
                or turn["state"] in {"completed", "failed", "cancelled"}
            ):
                return None
            if not confirmed_inactive and (
                row["quarantined"] or row["expires_at"] <= now or row["session_expiry"] <= now
            ):
                return None
            revalidate_directory_identity(
                DirectoryIdentity(
                    row["requested_cwd"],
                    row["canonical_cwd"],
                    row["directory_device"],
                    row["directory_inode"],
                )
            )
            generation = previous_generation + 1
            self._conn.execute(
                "UPDATE runtime_directory_leases SET holder=?,generation=?,expires_at=?,quarantined=0,updated_at=? WHERE turn_id=?",
                (holder, generation, now + ttl, now, turn_id),
            )
            self._conn.execute(
                "UPDATE session_turn_leases SET holder=?,acquired_at=?,expires_at=? WHERE conversation_id=?",
                (holder, now, now + ttl, turn["session_id"]),
            )
            self._conn.execute(
                "UPDATE runtime_turns SET state='running',updated_at=? WHERE id=?", (now, turn_id)
            )
            self._conn.execute(
                "UPDATE runtime_sessions SET state='running',updated_at=? WHERE session_id=?",
                (now, turn["session_id"]),
            )
            self._conn.execute(
                "UPDATE runtime_queue SET state='active',updated_at=? WHERE turn_id=?",
                (now, turn_id),
            )
            self._append(
                turn_id,
                uuid.uuid4().hex,
                "recovery",
                {"reason": "observation_attached", "lease_generation": generation},
            )
            return generation

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
            if self._conn.execute(
                "SELECT 1 FROM sessions WHERE id=?", (session.session_id,)
            ).fetchone():
                raise _error("idempotency_conflict", "identidade de sessão conflitante")
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
                    "capabilities_json=?,state=CASE WHEN external_thread_id IS NULL "
                    "THEN 'ready' ELSE state END,updated_at=? WHERE session_id=?",
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
        payload = _snapshot_json(payload)
        self._begin()
        try:
            event = self._append(turn_id, event_id, kind, payload)
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return event

    def _append(
        self, turn_id: str, event_id: str, kind: str, payload: Mapping[str, Any]
    ) -> RuntimeEvent:
        """Append inside the caller's transaction; never publish before its commit."""
        if any(not isinstance(value, str) or not value for value in (turn_id, event_id, kind)):
            raise _error("invalid_event", "evento inválido")
        payload_snapshot = _snapshot_json(payload)
        if not isinstance(payload_snapshot, Mapping):
            raise _error("invalid_event", "payload de evento inválido")
        payload_json = _json_dump(payload_snapshot)
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
            return self._event(duplicate)
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
        return RuntimeEvent(
            1, event_id, session_id, turn_id, sequence, cursor, kind, payload_snapshot
        )

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

    def session_details(self, session_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT r.*,s.source,s.parent_session_id FROM runtime_sessions r "
            "JOIN sessions s ON s.id=r.session_id WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise _error("unavailable", "sessão de runtime indisponível")
        result = dict(row)
        result["capabilities"] = json.loads(result.pop("capabilities_json") or "null")
        result["usage"] = {"status": "unknown"}
        return result

    def session_state(self, session_id: str, state: str) -> None:
        with self._transaction():
            self._conn.execute(
                "UPDATE runtime_sessions SET state=?,updated_at=? WHERE session_id=?",
                (state, time.time(), session_id),
            )
            if state == "ended":
                self._conn.execute(
                    "UPDATE sessions SET ended_at=? WHERE id=?", (time.time(), session_id)
                )

    def unbound_sessions(self) -> tuple[dict, ...]:
        return tuple(
            dict(row)
            for row in self._conn.execute(
                "SELECT * FROM runtime_sessions WHERE external_thread_id IS NULL AND state<>'ended'"
            )
        )

    def get_turn(self, turn_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT t.*,m.content,d.holder,d.generation AS lease_generation "
            "FROM runtime_turns t LEFT JOIN messages m ON m.id=t.user_message_id "
            "LEFT JOIN runtime_directory_leases d ON d.turn_id=t.id WHERE t.id=?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise _error("unavailable", "turno de runtime indisponível")
        result = dict(row)
        dispatch = self._conn.execute(
            "SELECT payload_json FROM runtime_events WHERE turn_id=? AND kind IN ('dispatching','observation_attached') "
            "ORDER BY sequence DESC LIMIT 1",
            (turn_id,),
        ).fetchone()
        result["process_generation"] = (
            json.loads(dispatch[0]).get("generation") if dispatch else None
        )
        return result

    def nonterminal_turns(self) -> tuple[dict, ...]:
        return tuple(
            self.get_turn(row[0])
            for row in self._conn.execute(
                "SELECT id FROM runtime_turns WHERE state NOT IN ('completed','failed','cancelled') "
                "AND (state<>'interrupted' OR inactive_confirmed_at IS NULL)"
            ).fetchall()
        )

    def terminal_without_event(self) -> tuple[dict, ...]:
        return tuple(
            self.get_turn(row[0])
            for row in self._conn.execute(
                "SELECT id FROM runtime_turns WHERE state IN ('completed','failed','cancelled','interrupted') "
                "AND NOT EXISTS(SELECT 1 FROM runtime_events e WHERE e.turn_id=runtime_turns.id AND e.kind='turn_end')"
            ).fetchall()
        )

    def transition(self, turn_id: str, expected: str, target: str) -> bool:
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE runtime_turns SET state=?,updated_at=? WHERE id=? AND state=?",
                (target, time.time(), turn_id, expected),
            )
            if not cursor.rowcount:
                return False
            self._append(turn_id, uuid.uuid4().hex, "turn_state", {"state": target})
            state = (
                target if target in {"waiting_approval", "recovering", "interrupted"} else "running"
            )
            self._conn.execute(
                "UPDATE runtime_sessions SET state=? WHERE session_id=(SELECT session_id FROM runtime_turns WHERE id=?)",
                (state, turn_id),
            )
            return True

    def dispatch(self, turn_id: str, generation: str) -> bool:
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE runtime_turns SET send_state='dispatching',updated_at=? "
                "WHERE id=? AND state='starting' AND send_state='not_sent'",
                (time.time(), turn_id),
            )
            if not cursor.rowcount:
                return False
            self._append(turn_id, f"dispatch:{turn_id}", "dispatching", {"generation": generation})
            return True

    def confirm_dispatch(self, turn_id: str, external_turn_id: str) -> None:
        with self._transaction():
            self._conn.execute(
                "UPDATE runtime_turns SET external_turn_id=?,send_state='confirmed',"
                "state=CASE WHEN state='starting' THEN 'running' ELSE state END,updated_at=? "
                "WHERE id=? AND send_state='dispatching'",
                (external_turn_id, time.time(), turn_id),
            )
            self._append(
                turn_id,
                f"confirmed:{turn_id}",
                "turn_start",
                {"external_turn_id": external_turn_id},
            )

    def uncertain(self, turn_id: str) -> None:
        with self._transaction():
            self._conn.execute(
                "UPDATE runtime_turns SET send_state=CASE WHEN send_state='dispatching' "
                "THEN 'uncertain' ELSE send_state END,updated_at=? WHERE id=?",
                (time.time(), turn_id),
            )
            self._append(turn_id, uuid.uuid4().hex, "recovery", {"reason": "observation_lost"})

    def lose_turn(
        self, turn_id: str, holder: str, generation: int, content: str, usage: dict | None
    ) -> bool:
        """Quarantine and preserve partial output atomically, including after lease expiry."""
        with self._transaction():
            directory = self._conn.execute(
                "SELECT * FROM runtime_directory_leases WHERE turn_id=? AND holder=? AND generation=?",
                (turn_id, holder, generation),
            ).fetchone()
            owner = self._directory_owner(turn_id)
            if (
                directory is None
                or owner is None
                or owner["state"] in {"completed", "failed", "cancelled"}
            ):
                return False
            now = time.time()
            self._quarantine_uncertain_owner(directory, owner, now)
            self._conn.execute(
                "UPDATE runtime_queue SET state='recovering',updated_at=? WHERE turn_id=?",
                (now, turn_id),
            )
            self._conn.execute(
                "UPDATE runtime_turns SET send_state=CASE WHEN send_state='dispatching' THEN 'uncertain' ELSE send_state END WHERE id=?",
                (turn_id,),
            )
            self._append(turn_id, uuid.uuid4().hex, "recovery", {"reason": "observation_lost"})
            self._finish(turn_id, "interrupted", content, usage)
            return True

    def save_approval(
        self,
        turn_id: str,
        process_generation: str,
        external_request_id: str,
        external_item_id: str | None,
        request: dict,
    ) -> str:
        with self._transaction():
            existing = self._conn.execute(
                "SELECT * FROM runtime_approvals WHERE process_generation=? AND external_request_id=?",
                (process_generation, external_request_id),
            ).fetchone()
            payload = _json_dump(request)
            if existing:
                if (
                    existing["turn_id"] != turn_id
                    or existing["external_item_id"] != external_item_id
                    or existing["request_json"] != payload
                ):
                    raise _error("invalid_event", "pedido externo conflitante")
                return str(existing["id"])
            turn = self.get_turn(turn_id)
            if turn["state"] not in {"running", "waiting_approval"}:
                raise _error("approval_stale", "aprovação não está mais pendente")
            approval_id = uuid.uuid4().hex
            now = time.time()
            self._conn.execute(
                "INSERT INTO runtime_approvals(id,turn_id,process_generation,external_request_id,"
                "external_item_id,request_json,delivery_state,created_at,updated_at) VALUES (?,?,?,?,?,?,'pending',?,?)",
                (
                    approval_id,
                    turn_id,
                    process_generation,
                    external_request_id,
                    external_item_id,
                    payload,
                    now,
                    now,
                ),
            )
            self._append(
                turn_id,
                f"approval:{approval_id}",
                "approval_request",
                {"approval_id": approval_id, **request},
            )
            self._conn.execute(
                "UPDATE runtime_turns SET state='waiting_approval' WHERE id=?", (turn_id,)
            )
            self._conn.execute(
                "UPDATE runtime_sessions SET state='waiting_approval' WHERE session_id=?",
                (turn["session_id"],),
            )
            return approval_id

    def get_approval(self, approval_id: str) -> dict:
        row = self._conn.execute(
            "SELECT * FROM runtime_approvals WHERE id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise _error("approval_stale", "aprovação não está mais pendente")
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        event = self._conn.execute(
            "SELECT payload_json FROM runtime_events WHERE turn_id=? AND event_id=? AND kind='approval_decision'",
            (result["turn_id"], f"decision:{approval_id}"),
        ).fetchone()
        result["decision_receipt"] = json.loads(event[0]).get("receipt") if event else None
        return result

    def _approval_identity(self, approval: dict, decision: str) -> dict:
        turn = self.get_turn(approval["turn_id"])
        return {
            "approval_id": approval["id"],
            "session_id": turn["session_id"],
            "turn_id": turn["id"],
            "external_turn_id": turn["external_turn_id"],
            "process_generation": approval["process_generation"],
            "external_request_id": approval["external_request_id"],
            "external_item_id": approval["external_item_id"],
            "request": approval["request"],
            "decision": decision,
        }

    def decide_approval(self, approval_id: str, decision: str) -> bool:
        if decision not in {"accept", "decline"}:
            raise _error("invalid_event", "decisão inválida")
        with self._transaction():
            approval = self.get_approval(approval_id)
            if self._fence is not None and self._fence[0] != approval["turn_id"]:
                raise _error("approval_stale", "aprovação não pertence à fence")
            if approval["decision"] is not None:
                if approval["decision"] != decision:
                    raise _error("approval_stale", "decisão de aprovação conflitante")
                return False
            now = time.time()
            self._conn.execute(
                "UPDATE runtime_approvals SET decision=?,decided_at=?,updated_at=?,delivery_state='decided' WHERE id=?",
                (decision, now, now, approval_id),
            )
            payload = {
                "approval_id": approval_id,
                "decision": decision,
                "delivery_state": "decided",
            }
            if self._fence is not None:
                payload["receipt"] = {
                    **self._approval_identity(approval, decision),
                    "holder": self._fence[1],
                    "lease_generation": self._fence[2],
                }
            self._append(
                approval["turn_id"],
                f"decision:{approval_id}",
                "approval_decision",
                payload,
            )
            return True

    def acknowledge_approval(self, approval_id: str, receipt: Mapping | None) -> None:
        """Record a successful originating RPC write, not authority to run the turn."""
        with self._transaction():
            approval = self.get_approval(approval_id)
            persisted = approval["decision_receipt"]
            if not persisted or _json_dump(receipt) != _json_dump(persisted):
                raise _error("approval_stale", "recibo de aprovação inválido")
            identity = {
                **self._approval_identity(approval, approval["decision"]),
                "holder": persisted["holder"],
                "lease_generation": persisted["lease_generation"],
            }
            turn = self.get_turn(approval["turn_id"])
            # Release or reuse by ANOTHER turn does not invalidate historical evidence.
            # A takeover of THIS turn does, even after its directory row is later reused.
            takeovers = self._conn.execute(
                "SELECT payload_json FROM runtime_events WHERE turn_id=? AND kind='recovery' "
                "AND sequence>(SELECT sequence FROM runtime_events WHERE turn_id=? AND event_id=?)",
                (turn["id"], turn["id"], f"decision:{approval_id}"),
            ).fetchall()
            if (
                _json_dump(identity) != _json_dump(persisted)
                or turn["process_generation"] != persisted["process_generation"]
                or (
                    turn["holder"] is not None
                    and (turn["holder"], turn["lease_generation"])
                    != (persisted["holder"], persisted["lease_generation"])
                )
                or any(
                    json.loads(event[0]).get("reason") == "observation_attached"
                    for event in takeovers
                )
            ):
                raise _error("approval_stale", "recibo pertence a uma posse anterior")
            if approval["delivery_state"] == "delivered":
                return
            self._conn.execute(
                "UPDATE runtime_approvals SET delivery_state='delivered',updated_at=? WHERE id=?",
                (time.time(), approval_id),
            )
            self._append(
                approval["turn_id"],
                f"delivered:{approval_id}",
                "approval_delivery",
                {"approval_id": approval_id, "delivery_state": "delivered"},
            )

    def continue_after_approval(self, approval_id: str) -> None:
        """Resume lifecycle only under the original, still-live execution fence."""
        with self._transaction():
            approval = self.get_approval(approval_id)
            receipt = approval["decision_receipt"]
            if (
                self._fence is None
                or not receipt
                or self._fence
                != (approval["turn_id"], receipt["holder"], receipt["lease_generation"])
                or approval["delivery_state"] != "delivered"
            ):
                raise _error("approval_stale", "aprovação não foi entregue sob esta fence")
            outstanding = self._conn.execute(
                "SELECT 1 FROM runtime_approvals WHERE turn_id=? AND delivery_state IN ('pending','decided')",
                (approval["turn_id"],),
            ).fetchone()
            if not outstanding:
                self._conn.execute(
                    "UPDATE runtime_turns SET state='running' WHERE id=? AND state='waiting_approval'",
                    (approval["turn_id"],),
                )
                self._conn.execute(
                    "UPDATE runtime_sessions SET state='running' WHERE session_id=(SELECT session_id FROM runtime_turns WHERE id=?) AND state='waiting_approval'",
                    (approval["turn_id"],),
                )

    def finish(self, turn_id: str, state: str, content: str, usage: dict | None) -> None:
        if state not in {"completed", "failed", "cancelled", "interrupted"}:
            raise _error("invalid_event", "estado terminal inválido")
        with self._transaction():
            self._finish(turn_id, state, content, usage)

    def _finish(self, turn_id: str, state: str, content: str, usage: dict | None) -> None:
        turn = self.get_turn(turn_id)
        usage_value = (
            {"status": "unknown"} if usage is None else {"status": "known", "value": usage}
        )
        metadata = _json_dump({"runtime_kind": "codex", "turn_id": turn_id, "usage": usage_value})
        now = time.time()
        if turn["assistant_message_id"] is None:
            message = self._conn.execute(
                "INSERT INTO messages(session_id,role,content,api_content,timestamp,display_kind,display_metadata,finish_reason) VALUES (?,'assistant',?,?,?,'agent_runtime',?,?)",
                (turn["session_id"], content, content, now, metadata, state),
            )
            message_id = message.lastrowid
        else:
            message_id = turn["assistant_message_id"]
            self._conn.execute(
                "UPDATE messages SET content=?,api_content=?,display_metadata=?,finish_reason=? WHERE id=?",
                (content, content, metadata, state, message_id),
            )
        self._conn.execute(
            "UPDATE runtime_turns SET state=?,assistant_message_id=?,updated_at=? WHERE id=?",
            (state, message_id, now, turn_id),
        )
        self._conn.execute(
            "UPDATE runtime_sessions SET state=?,updated_at=? WHERE session_id=? AND state NOT IN ('ended','unavailable')",
            ("interrupted" if state == "interrupted" else "ready", now, turn["session_id"]),
        )
        self._conn.execute(
            "UPDATE runtime_approvals SET delivery_state='stale',updated_at=? WHERE turn_id=? AND delivery_state<>'delivered'",
            (now, turn_id),
        )
        # A later reconciliation may refine an interrupted outcome; retain both events.
        payload = {"state": state, "content": content, "usage": usage_value}
        digest = hashlib.sha256(_json_dump(payload).encode()).hexdigest()
        self._append(turn_id, f"end:{turn_id}:{digest}", "turn_end", payload)

    @contextmanager
    def _transaction(self):
        self._begin()
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def enqueue_runtime_turn(self, turn_id: str, clock: Callable[[], float]) -> None:
        try:
            self._begin()
            now = clock()
            row = self._conn.execute(
                "SELECT t.state,r.canonical_cwd FROM runtime_turns t "
                "JOIN runtime_sessions r ON r.session_id=t.session_id WHERE t.id=?",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise _error("unavailable", "turno de runtime indisponível")
            existing = self._conn.execute(
                "SELECT state FROM runtime_queue WHERE turn_id=?", (turn_id,)
            ).fetchone()
            if existing is None:
                if row["state"] != "queued":
                    raise _error("invalid_event", "turno não pode entrar na fila")
                self._conn.execute(
                    "INSERT INTO runtime_queue(turn_id,canonical_cwd,state,created_at,updated_at) "
                    "VALUES (?,?,'waiting',?,?)",
                    (turn_id, row["canonical_cwd"], now, now),
                )
            elif existing["state"] != "waiting":
                raise _error("invalid_event", "turno já saiu da fila")
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def claim_runtime_turn(
        self, turn_id: str, holder: str, ttl: float, clock: Callable[[], float]
    ) -> int | None:
        try:
            self._begin()
            now = clock()
            turn = self._conn.execute(
                "SELECT t.session_id,t.state AS turn_state,q.state AS queue_state,q.ticket,"
                "r.requested_cwd,r.canonical_cwd,r.directory_device,r.directory_inode "
                "FROM runtime_turns t JOIN runtime_sessions r ON r.session_id=t.session_id "
                "LEFT JOIN runtime_queue q ON q.turn_id=t.id WHERE t.id=?",
                (turn_id,),
            ).fetchone()
            if turn is None:
                raise _error("unavailable", "turno de runtime indisponível")
            if turn["turn_state"] != "queued" or turn["queue_state"] != "waiting":
                self._conn.rollback()
                return None
            first = self._conn.execute(
                "SELECT ticket FROM runtime_queue "
                "WHERE canonical_cwd=? AND state='waiting' ORDER BY ticket LIMIT 1",
                (turn["canonical_cwd"],),
            ).fetchone()
            if first is None or first["ticket"] != turn["ticket"]:
                self._conn.rollback()
                return None

            identity = DirectoryIdentity(
                requested_path=turn["requested_cwd"],
                canonical_path=turn["canonical_cwd"],
                device=int(turn["directory_device"]),
                inode=int(turn["directory_inode"]),
            )
            revalidate_directory_identity(identity)
            directory = self._conn.execute(
                "SELECT * FROM runtime_directory_leases WHERE canonical_cwd=?",
                (turn["canonical_cwd"],),
            ).fetchone()
            if directory is not None and directory["quarantined"]:
                self._conn.rollback()
                return None
            if directory is not None and directory["expires_at"] > now:
                self._conn.rollback()
                return None
            if directory is not None:
                previous = self._directory_owner(directory["turn_id"])
                if not self._owner_is_confirmed_inactive(previous):
                    self._quarantine_uncertain_owner(directory, previous, now)
                    self._conn.commit()
                    return None
                self._finalize_inactive_owner(directory, previous, now)

            session_lease = self._conn.execute(
                "SELECT holder,expires_at FROM session_turn_leases WHERE conversation_id=?",
                (turn["session_id"],),
            ).fetchone()
            if (
                session_lease is not None
                and session_lease["expires_at"] is not None
                and session_lease["expires_at"] > now
            ):
                self._conn.rollback()
                return None

            expires_at = now + ttl
            self._conn.execute(
                "INSERT INTO session_turn_leases(conversation_id,holder,acquired_at,expires_at) "
                "VALUES (?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET "
                "holder=excluded.holder,acquired_at=excluded.acquired_at,"
                "expires_at=excluded.expires_at",
                (turn["session_id"], holder, now, expires_at),
            )
            generation = 1 if directory is None else int(directory["generation"]) + 1
            self._conn.execute(
                "INSERT INTO runtime_directory_leases("
                "canonical_cwd,turn_id,holder,generation,acquired_at,expires_at,quarantined,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,0,?,?) "
                "ON CONFLICT(canonical_cwd) DO UPDATE SET "
                "turn_id=excluded.turn_id,holder=excluded.holder,generation=excluded.generation,"
                "acquired_at=excluded.acquired_at,expires_at=excluded.expires_at,"
                "quarantined=0,updated_at=excluded.updated_at",
                (
                    turn["canonical_cwd"],
                    turn_id,
                    holder,
                    generation,
                    now,
                    expires_at,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE runtime_queue SET state='active',updated_at=? WHERE turn_id=?",
                (now, turn_id),
            )
            self._conn.execute(
                "UPDATE runtime_turns SET state='starting',updated_at=? WHERE id=?",
                (now, turn_id),
            )
            self._conn.execute(
                "UPDATE runtime_sessions SET state='running',updated_at=? WHERE session_id=?",
                (now, turn["session_id"]),
            )
        except sqlite3.OperationalError as exc:
            self._conn.rollback()
            if is_busy_error(exc):
                return None
            raise
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
            return generation

    def renew_runtime_turn(
        self,
        turn_id: str,
        holder: str,
        generation: int,
        ttl: float,
        clock: Callable[[], float],
    ) -> bool:
        try:
            self._begin()
            now = clock()
            row = self._conn.execute(
                "SELECT d.canonical_cwd,t.session_id FROM runtime_directory_leases d "
                "JOIN runtime_turns t ON t.id=d.turn_id "
                "JOIN session_turn_leases s ON s.conversation_id=t.session_id "
                "WHERE d.turn_id=? AND d.holder=? AND d.generation=? "
                "AND d.quarantined=0 AND d.expires_at>? "
                "AND s.holder=? AND s.expires_at>?",
                (turn_id, holder, generation, now, holder, now),
            ).fetchone()
            if row is None:
                self._conn.rollback()
                return False
            expires_at = now + ttl
            self._conn.execute(
                "UPDATE runtime_directory_leases SET expires_at=?,updated_at=? "
                "WHERE canonical_cwd=? AND holder=? AND generation=?",
                (expires_at, now, row["canonical_cwd"], holder, generation),
            )
            self._conn.execute(
                "UPDATE session_turn_leases SET expires_at=? WHERE conversation_id=? AND holder=?",
                (expires_at, row["session_id"], holder),
            )
        except sqlite3.OperationalError as exc:
            self._conn.rollback()
            if is_busy_error(exc):
                return False
            raise
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
            return True

    def release_runtime_turn(
        self,
        turn_id: str,
        holder: str,
        generation: int,
        clock: Callable[[], float],
        *,
        confirmed_inactive: bool = False,
    ) -> bool:
        try:
            self._begin()
            now = clock()
            row = self._conn.execute(
                "SELECT d.canonical_cwd,d.turn_id,d.holder,t.session_id,t.state,"
                "t.inactive_confirmed_at,r.state AS session_state,s.ended_at "
                "FROM runtime_directory_leases d JOIN runtime_turns t ON t.id=d.turn_id "
                "JOIN runtime_sessions r ON r.session_id=t.session_id "
                "JOIN sessions s ON s.id=t.session_id "
                "WHERE d.turn_id=? AND d.holder=? AND d.generation=?",
                (turn_id, holder, generation),
            ).fetchone()
            if row is None or row["state"] not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }:
                self._conn.rollback()
                return False
            if row["state"] == "interrupted" and not (
                row["inactive_confirmed_at"] is not None or confirmed_inactive
            ):
                self._conn.rollback()
                return False
            if confirmed_inactive:
                self._conn.execute(
                    "UPDATE runtime_turns SET inactive_confirmed_at=?,updated_at=? WHERE id=?",
                    (now, now, turn_id),
                )
            self._conn.execute(
                "UPDATE runtime_directory_leases SET expires_at=?,quarantined=0,updated_at=? "
                "WHERE canonical_cwd=? AND holder=? AND generation=?",
                (now, now, row["canonical_cwd"], holder, generation),
            )
            self._finalize_inactive_owner(row, row, now)
        except sqlite3.OperationalError as exc:
            self._conn.rollback()
            if is_busy_error(exc):
                return False
            raise
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
            return True

    def _directory_owner(self, turn_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT t.id AS turn_id,t.state,t.session_id,t.inactive_confirmed_at,"
            "r.state AS session_state,s.ended_at "
            "FROM runtime_turns t JOIN runtime_sessions r ON r.session_id=t.session_id "
            "JOIN sessions s ON s.id=t.session_id WHERE t.id=?",
            (turn_id,),
        ).fetchone()

    @staticmethod
    def _owner_is_confirmed_inactive(owner: sqlite3.Row | None) -> bool:
        return owner is not None and (
            owner["state"] in {"completed", "failed", "cancelled"}
            or (owner["state"] == "interrupted" and owner["inactive_confirmed_at"] is not None)
        )

    def _quarantine_uncertain_owner(
        self, directory: sqlite3.Row, previous: sqlite3.Row | None, now: float
    ) -> None:
        if previous is not None:
            self._conn.execute(
                "UPDATE runtime_turns SET state='recovering',updated_at=? "
                "WHERE id=? AND state<>'interrupted'",
                (now, directory["turn_id"]),
            )
            self._conn.execute(
                "UPDATE runtime_sessions SET state='recovering',updated_at=? WHERE session_id=?",
                (now, previous["session_id"]),
            )
        self._conn.execute(
            "UPDATE runtime_directory_leases SET quarantined=1,updated_at=? WHERE canonical_cwd=?",
            (now, directory["canonical_cwd"]),
        )

    def _finalize_inactive_owner(
        self, directory: sqlite3.Row, owner: sqlite3.Row, now: float
    ) -> None:
        self._conn.execute(
            "UPDATE session_turn_leases SET holder=NULL,acquired_at=NULL,expires_at=NULL "
            "WHERE conversation_id=? AND holder=?",
            (owner["session_id"], directory["holder"]),
        )
        self._conn.execute(
            "UPDATE runtime_queue SET state='done',updated_at=? WHERE turn_id=?",
            (now, directory["turn_id"]),
        )
        if owner["session_state"] not in {"ended", "unavailable"}:
            state = "ended" if owner["ended_at"] is not None else "ready"
            self._conn.execute(
                "UPDATE runtime_sessions SET state=?,updated_at=? WHERE session_id=?",
                (state, now, owner["session_id"]),
            )

    def quarantine_runtime_turn(self, turn_id: str, clock: Callable[[], float]) -> None:
        try:
            self._begin()
            now = clock()
            row = self._conn.execute(
                "SELECT d.canonical_cwd,d.holder,t.session_id FROM runtime_directory_leases d "
                "JOIN runtime_turns t ON t.id=d.turn_id WHERE d.turn_id=?",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise _error("unavailable", "lease de runtime indisponível")
            self._conn.execute(
                "UPDATE runtime_directory_leases SET quarantined=1,expires_at=?,updated_at=? "
                "WHERE canonical_cwd=?",
                (now, now, row["canonical_cwd"]),
            )
            self._conn.execute(
                "UPDATE session_turn_leases SET expires_at=? WHERE conversation_id=? AND holder=?",
                (now, row["session_id"], row["holder"]),
            )
            self._conn.execute(
                "UPDATE runtime_turns SET state='recovering',updated_at=? "
                "WHERE id=? AND state<>'interrupted'",
                (now, turn_id),
            )
            self._conn.execute(
                "UPDATE runtime_sessions SET state='recovering',updated_at=? WHERE session_id=?",
                (now, row["session_id"]),
            )
            self._conn.execute(
                "UPDATE runtime_queue SET state='recovering',updated_at=? WHERE turn_id=?",
                (now, turn_id),
            )
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def cancel_queued_runtime_turn(self, turn_id: str, clock: Callable[[], float]) -> bool:
        try:
            self._begin()
            now = clock()
            cursor = self._conn.execute(
                "UPDATE runtime_turns SET state='cancelled',updated_at=? "
                "WHERE id=? AND state='queued' AND EXISTS ("
                "SELECT 1 FROM runtime_queue WHERE turn_id=? AND state='waiting')",
                (now, turn_id, turn_id),
            )
            if cursor.rowcount == 0:
                self._conn.rollback()
                return False
            self._conn.execute(
                "UPDATE runtime_queue SET state='cancelled',updated_at=? WHERE turn_id=?",
                (now, turn_id),
            )
            self._finish(turn_id, "cancelled", "", None)
        except sqlite3.OperationalError as exc:
            self._conn.rollback()
            if is_busy_error(exc):
                return False
            raise
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
            return True

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
        if self._fence is not None:
            turn_id, holder, generation = self._fence
            row = self._conn.execute(
                "SELECT 1 FROM runtime_directory_leases d JOIN runtime_turns t ON t.id=d.turn_id "
                "JOIN session_turn_leases s ON s.conversation_id=t.session_id "
                "WHERE d.turn_id=? AND d.holder=? AND d.generation=? AND s.holder=? "
                "AND (? OR (d.quarantined=0 AND d.expires_at>? AND s.expires_at>?))",
                (
                    turn_id,
                    holder,
                    generation,
                    holder,
                    self._allow_expired,
                    time.time(),
                    time.time(),
                ),
            ).fetchone()
            if row is None:
                self._conn.rollback()
                raise _error("lease_lost", "lease de runtime perdida")
