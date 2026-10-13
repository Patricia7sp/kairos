"""Persistência transacional de adoções e revogações sem mutação de arquivos."""

from __future__ import annotations

import sqlite3

from kairos_domain.ownership import Actor
from kairos_filesystem.contract import TreeCapture
from kairos_skills.adoption_contract import (
    SkillAdoptionAction,
    SkillAdoptionDraft,
    SkillAdoptionRecord,
)
from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
    require,
    validate_hash,
    validate_id,
)
from kairos_skills.removal_contract import validate_capture
from kairos_state.repositories.skill_mutations import (
    SkillMutationRepository,
    _project_committed_records,
    corrupt_errors,
)
from kairos_state.repositories.skill_removals import SkillRemovalRepository


class SkillAdoptionRepository:
    def __init__(self, connection: sqlite3.Connection, *, home_id: str):
        validate_hash(home_id)
        self._conn = connection
        self._home_id = home_id
        self._common = SkillMutationRepository(connection, home_id=home_id)
        self._trees = SkillRemovalRepository(connection, home_id=home_id)

    def _base(self, operation_id: str) -> SkillAdoptionRecord | None:
        row = self._conn.execute(
            "SELECT operation_id,home_id,action,name,actor,created_at,adoption_id,installation_id,device,inode,installation_source,snapshot_id FROM skill_adoption_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            require(
                self._conn.execute(
                    "SELECT 1 FROM skill_adoption_events WHERE operation_id=?", (operation_id,)
                ).fetchone()
                is None
            )
            return None
        require(type(row[8]) is str and type(row[9]) is str)
        require(str(int(row[8])) == row[8] and str(int(row[9])) == row[9])
        draft = SkillAdoptionDraft(
            row[0],
            row[1],
            SkillAdoptionAction(row[2]),
            row[3],
            Actor(row[4]),
            row[5],
            row[6],
            row[7],
            int(row[8]),
            int(row[9]),
            row[10],
            row[11],
        )
        require(draft.home_id == self._home_id)
        snapshot = self._trees._snapshot(draft.snapshot_id)
        if draft.action is SkillAdoptionAction.ADOPT:
            require(
                (draft.device, draft.inode)
                == (snapshot.entries[0].device, snapshot.entries[0].inode)
            )
        events = self._conn.execute(
            "SELECT e.sequence,e.state,t.sequence FROM skill_adoption_events e LEFT JOIN skill_authority_timeline t ON t.adoption_event=e.sequence WHERE e.operation_id=? ORDER BY e.sequence LIMIT 4",
            (operation_id,),
        ).fetchall()
        require(1 <= len(events) <= 3)
        state = None
        previous = 0
        for event, value, position in events:
            require(
                type(event) is int and event > 0 and type(position) is int and position > previous
            )
            next_state = SkillMutationState(value)
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
            previous = position
        if draft.action is SkillAdoptionAction.UNADOPT:
            require(state is SkillMutationState.COMMITTED and len(events) == 2)
        return SkillAdoptionRecord(draft, state)

    def get(self, operation_id: str) -> SkillAdoptionRecord | None:
        validate_id(operation_id)
        with corrupt_errors():
            record = self._base(operation_id)
            if record is not None and record.action is SkillAdoptionAction.UNADOPT:
                parent = self._base(record.draft.adoption_id)
                require(
                    parent is not None
                    and parent.action is SkillAdoptionAction.ADOPT
                    and parent.state is SkillMutationState.COMMITTED
                )
                require(
                    (record.name, record.draft.installation_source)
                    == (parent.name, parent.draft.installation_source)
                )
                committed = self._conn.execute(
                    "SELECT t.sequence FROM skill_authority_timeline t JOIN skill_adoption_events e ON e.sequence=t.adoption_event WHERE e.operation_id=? AND e.state='committed'",
                    (parent.operation_id,),
                ).fetchone()
                first = self._conn.execute(
                    "SELECT t.sequence FROM skill_authority_timeline t JOIN skill_adoption_events e ON e.sequence=t.adoption_event WHERE e.operation_id=? ORDER BY t.sequence LIMIT 1",
                    (operation_id,),
                ).fetchone()
                require(committed[0] < first[0])
            return record

    def snapshot(self, operation_id: str) -> TreeCapture:
        record = self.get(operation_id)
        require(record is not None, "Operação não encontrada neste histórico.")
        with corrupt_errors():
            return self._trees._snapshot(record.draft.snapshot_id)

    def _validate_transition(self, draft):
        records, _ = self._common._projection_inputs(draft.name)
        try:
            _project_committed_records(
                (*records, SkillAdoptionRecord(draft, SkillMutationState.COMMITTED))
            )
        except SkillMutationError:
            raise SkillMutationError(
                "conflict", "A autoridade ou identidade da instalação mudou; operação recusada."
            ) from None

    def _insert(self, draft):
        self._conn.execute(
            "INSERT INTO skill_adoption_operations(operation_id,home_id,action,name,actor,created_at,adoption_id,installation_id,device,inode,installation_source,snapshot_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                draft.operation_id,
                draft.home_id,
                draft.action.value,
                draft.name,
                draft.actor.value,
                draft.created_at,
                draft.adoption_id,
                draft.installation_id,
                str(draft.device),
                str(draft.inode),
                draft.installation_source,
                draft.snapshot_id,
            ),
        )
        self._conn.execute(
            "INSERT INTO skill_adoption_events(operation_id,state) VALUES (?,'prepared')",
            (draft.operation_id,),
        )

    def prepare_adopt(
        self, draft: SkillAdoptionDraft, snapshot: TreeCapture
    ) -> SkillAdoptionRecord:
        require(
            type(draft) is SkillAdoptionDraft
            and draft.action is SkillAdoptionAction.ADOPT
            and draft.home_id == self._home_id
        )
        validate_capture(snapshot, contents=True)
        require(
            (draft.device, draft.inode) == (snapshot.entries[0].device, snapshot.entries[0].inode)
        )

        def operation():
            self._common._assert_no_pending(draft.name)
            self._validate_transition(draft)
            self._trees._store_snapshot(draft.snapshot_id, snapshot)
            self._insert(draft)
            return self._common.get(draft.operation_id)

        return self._common._write(operation)

    def commit_unadopt(self, draft: SkillAdoptionDraft) -> SkillAdoptionRecord:
        require(
            type(draft) is SkillAdoptionDraft
            and draft.action is SkillAdoptionAction.UNADOPT
            and draft.home_id == self._home_id
        )

        def operation():
            self._common._assert_no_pending(draft.name)
            self._validate_transition(draft)
            self._insert(draft)
            self._conn.execute(
                "INSERT INTO skill_adoption_events(operation_id,state) VALUES (?,'committed')",
                (draft.operation_id,),
            )
            return self._common.get(draft.operation_id)

        return self._common._write(operation)

    def finish(self, operation_id: str, state: SkillMutationState) -> SkillAdoptionRecord:
        validate_id(operation_id)
        require(type(state) is SkillMutationState and state is not SkillMutationState.PREPARED)

        def operation():
            record = self.get(operation_id)
            require(record is not None)
            if record.state is state:
                return record
            require(record.action is SkillAdoptionAction.ADOPT)
            require(
                record.state is SkillMutationState.PREPARED
                or (
                    record.state is SkillMutationState.CONFLICT
                    and state in (SkillMutationState.COMMITTED, SkillMutationState.ABORTED)
                )
            )
            if state is SkillMutationState.COMMITTED:
                self._validate_transition(record.draft)
            self._conn.execute(
                "INSERT INTO skill_adoption_events(operation_id,state) VALUES (?,?)",
                (operation_id, state.value),
            )
            return self._common.get(operation_id)

        return self._common._write(operation)
