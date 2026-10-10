"""Journal transacional de árvores e snapshots binários imutáveis."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture, TreeEntry
from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
    require,
    validate_hash,
    validate_id,
)
from kairos_skills.removal_contract import (
    MAX_MANIFEST_BYTES,
    SkillTreeAction,
    SkillTreeDraft,
    SkillTreeRecord,
    validate_capture,
)
from kairos_state.contention import Budget
from kairos_state.repositories.skill_mutations import corrupt_errors
from kairos_state.skill_writer import register_skill_writer
from kairos_state.writes import write_with_retry


def manifest_json(capture: TreeCapture) -> str:
    return json.dumps(
        [asdict(entry) for entry in capture.entries],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _manifest(value: str) -> TreeCapture:
    require(type(value) is str and 0 < len(value.encode("utf-8")) <= MAX_MANIFEST_BYTES)
    entries = json.loads(value)
    require(type(entries) is list and 1 <= len(entries) <= 4096)
    require(all(type(entry) is dict for entry in entries))
    capture = TreeCapture(tuple(TreeEntry(**entry) for entry in entries), None)
    validate_capture(capture)
    require(manifest_json(capture) == value)
    return capture


def _inventory(capture: TreeCapture):
    return tuple(
        (entry.path, entry.kind, entry.mode, entry.size, entry.sha256) for entry in capture.entries
    )


class SkillRemovalRepository:
    def __init__(self, connection: sqlite3.Connection, *, home_id: str):
        validate_hash(home_id)
        self._conn = connection
        self._home_id = home_id
        register_skill_writer(connection)

    def _snapshot(self, snapshot_id: str) -> TreeCapture:
        length = self._conn.execute(
            "SELECT length(CAST(manifest_json AS BLOB)) FROM skill_tree_snapshots WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        require(
            length is not None and type(length[0]) is int and 0 < length[0] <= MAX_MANIFEST_BYTES
        )
        value = self._conn.execute(
            "SELECT manifest_json FROM skill_tree_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()[0]
        capture = _manifest(value)
        # Todos os comprimentos são atestados antes de buscar o primeiro BLOB.
        for entry in capture.entries:
            if entry.kind == "file":
                row = self._conn.execute(
                    "SELECT size_bytes,length(content),typeof(content) FROM skill_tree_blobs WHERE sha256=?",
                    (entry.sha256,),
                ).fetchone()
                require(
                    row is not None
                    and type(row[0]) is int
                    and row[0] == row[1] == entry.size
                    and row[2] == "blob"
                )
        contents = {}
        for entry in capture.entries:
            if entry.kind == "file":
                data = self._conn.execute(
                    "SELECT content FROM skill_tree_blobs WHERE sha256=?", (entry.sha256,)
                ).fetchone()[0]
                require(
                    type(data) is bytes
                    and len(data) == entry.size
                    and hashlib.sha256(data).hexdigest() == entry.sha256
                )
                contents[entry.path] = data
        return TreeCapture(capture.entries, contents)

    def _base(self, operation_id: str) -> SkillTreeRecord | None:
        length = self._conn.execute(
            "SELECT length(CAST(proof_json AS BLOB)) FROM skill_tree_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if length is None:
            require(
                self._conn.execute(
                    "SELECT 1 FROM skill_tree_events WHERE operation_id=?", (operation_id,)
                ).fetchone()
                is None
            )
            return None
        require(type(length[0]) is int and 0 < length[0] <= MAX_MANIFEST_BYTES)
        row = self._conn.execute(
            "SELECT operation_id,home_id,action,name,actor,created_at,provenance,proof_json,snapshot_id,reverts FROM skill_tree_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        draft = SkillTreeDraft(
            row[0],
            row[1],
            SkillTreeAction(row[2]),
            row[3],
            Actor(row[4]),
            row[5],
            Provenance(row[6]),
            _manifest(row[7]),
            row[8],
            row[9],
        )
        require(draft.home_id == self._home_id)
        snapshot = self._snapshot(draft.snapshot_id)
        if draft.action is SkillTreeAction.REMOVE:
            require(draft.proof.entries == snapshot.entries)
        else:
            require(_inventory(draft.proof) == _inventory(snapshot))
        events = self._conn.execute(
            "SELECT e.sequence,e.state,t.sequence FROM skill_tree_events e LEFT JOIN skill_mutation_timeline t ON t.tree_event=e.sequence WHERE e.operation_id=? ORDER BY e.sequence LIMIT 4",
            (operation_id,),
        ).fetchall()
        require(1 <= len(events) <= 3)
        state = None
        previous = 0
        for event in events:
            require(
                type(event[0]) is int
                and event[0] > 0
                and type(event[2]) is int
                and event[2] > previous
            )
            next_state = SkillMutationState(event[1])
            require(
                (state is None and next_state is SkillMutationState.PREPARED)
                or (
                    state is SkillMutationState.PREPARED
                    and next_state is not SkillMutationState.PREPARED
                )
                or (
                    state is SkillMutationState.CONFLICT
                    and next_state in (SkillMutationState.COMMITTED, SkillMutationState.ABORTED)
                )
            )
            state = next_state
            previous = event[2]
        return SkillTreeRecord(draft, state)

    def get(self, operation_id: str) -> SkillTreeRecord | None:
        validate_id(operation_id)
        with corrupt_errors():
            record = self._base(operation_id)
            if record is not None and record.action is SkillTreeAction.RESTORE:
                parent = self._base(record.reverts)
                require(
                    parent is not None
                    and parent.action is SkillTreeAction.REMOVE
                    and parent.state is SkillMutationState.COMMITTED
                )
                require((record.name, record.provenance) == (parent.name, parent.provenance))
                first = self._conn.execute(
                    "SELECT operation_id FROM skill_tree_operations WHERE operation_id IN (?,?) ORDER BY sequence LIMIT 1",
                    (parent.operation_id, record.operation_id),
                ).fetchone()
                require(first[0] == parent.operation_id)
            return record

    def snapshot(self, operation_id: str) -> TreeCapture:
        record = self.get(operation_id)
        require(record is not None, "Operação não encontrada neste histórico.")
        with corrupt_errors():
            return self._snapshot(record.draft.snapshot_id)

    def _write(self, operation):
        def transaction():
            if self._conn.in_transaction:
                raise SkillMutationError("io", "Mutação exige uma conexão sem transação pendente.")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                result = operation()
                self._conn.commit()
                return result
            except BaseException:
                self._conn.rollback()
                raise

        try:
            return write_with_retry(transaction, budget=Budget.TRANSCRIPT, detail="skill_removal")
        except sqlite3.IntegrityError:
            raise SkillMutationError(
                "conflict", "O ledger recusou uma operação conflitante; arquivos preservados."
            ) from None
        except sqlite3.DatabaseError:
            raise SkillMutationError(
                "io", "Não foi possível confirmar a operação no ledger; consulte seu ID."
            ) from None

    def _insert(self, draft: SkillTreeDraft) -> SkillTreeRecord:
        self._conn.execute(
            "INSERT INTO skill_tree_operations(operation_id,home_id,action,name,actor,created_at,provenance,proof_json,snapshot_id,reverts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                draft.operation_id,
                draft.home_id,
                draft.action.value,
                draft.name,
                draft.actor.value,
                draft.created_at,
                draft.provenance.value,
                manifest_json(draft.proof),
                draft.snapshot_id,
                draft.reverts,
            ),
        )
        self._conn.execute(
            "INSERT INTO skill_tree_events(operation_id,state) VALUES (?,'prepared')",
            (draft.operation_id,),
        )
        return self.get(draft.operation_id)

    def prepare_remove(self, draft: SkillTreeDraft, snapshot: TreeCapture) -> SkillTreeRecord:
        require(
            type(draft) is SkillTreeDraft
            and draft.action is SkillTreeAction.REMOVE
            and draft.home_id == self._home_id
        )
        validate_capture(snapshot, contents=True)
        require(draft.proof.entries == snapshot.entries)

        def operation():
            for entry in snapshot.entries:
                if entry.kind == "file":
                    self._conn.execute(
                        "INSERT INTO skill_tree_blobs(sha256,size_bytes,content) VALUES (?,?,?) ON CONFLICT(sha256) DO NOTHING",
                        (entry.sha256, entry.size, snapshot.contents[entry.path]),
                    )
            self._conn.execute(
                "INSERT INTO skill_tree_snapshots(snapshot_id,manifest_json) VALUES (?,?)",
                (draft.snapshot_id, manifest_json(snapshot)),
            )
            return self._insert(draft)

        return self._write(operation)

    def prepare_restore(self, draft: SkillTreeDraft) -> SkillTreeRecord:
        require(
            type(draft) is SkillTreeDraft
            and draft.action is SkillTreeAction.RESTORE
            and draft.home_id == self._home_id
        )

        def operation():
            parent = self.get(draft.reverts)
            require(
                parent is not None
                and parent.action is SkillTreeAction.REMOVE
                and parent.state is SkillMutationState.COMMITTED
            )
            require((parent.name, parent.provenance) == (draft.name, draft.provenance))
            require(_inventory(draft.proof) == _inventory(self._snapshot(draft.snapshot_id)))
            return self._insert(draft)

        return self._write(operation)

    def finish(self, operation_id: str, state: SkillMutationState) -> SkillTreeRecord:
        validate_id(operation_id)
        require(
            type(state) is SkillMutationState and state is not SkillMutationState.PREPARED,
            "Estado terminal inválido.",
        )

        def operation():
            record = self.get(operation_id)
            require(record is not None, "Operação não encontrada neste histórico.")
            if record.state is state:
                return record
            require(
                record.state is SkillMutationState.PREPARED
                or (
                    record.state is SkillMutationState.CONFLICT
                    and state in (SkillMutationState.COMMITTED, SkillMutationState.ABORTED)
                ),
                "Transição de estado recusada.",
            )
            self._conn.execute(
                "INSERT INTO skill_tree_events(operation_id,state) VALUES (?,?)",
                (operation_id, state.value),
            )
            return self.get(operation_id)

        return self._write(operation)
