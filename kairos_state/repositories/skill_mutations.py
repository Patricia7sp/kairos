"""Operações append-only e projeção verificável de propriedade."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.mutation_contract import (
    SkillDirectoryIdentity,
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationRecord,
    SkillMutationState,
    require,
    validate_hash,
    validate_id,
    validate_name,
    validate_skill_creation,
)
from kairos_state.contention import Budget
from kairos_state.writes import write_with_retry


@contextmanager
def corrupt_errors():
    try:
        yield
    except (SkillMutationError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise SkillMutationError(
            "corrupt", "Ledger de skills incoerente; operação recusada e arquivos preservados."
        ) from None
    except sqlite3.DatabaseError:
        raise SkillMutationError("io", "Não foi possível ler o ledger de skills.") from None


def identity_json(identity: SkillDirectoryIdentity) -> str:
    return json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))


class SkillMutationRepository:
    def __init__(self, connection: sqlite3.Connection, *, home_id: str):
        validate_hash(home_id)
        self._conn = connection
        self._home_id = home_id

    @property
    def home_id(self):
        return self._home_id

    def _blob(self, draft: SkillMutationDraft):
        row = self._conn.execute(
            "SELECT size_bytes,length(CAST(text AS BLOB)) FROM skill_mutation_contents WHERE sha256=?",
            (draft.sha256,),
        ).fetchone()
        require(row is not None and type(row[0]) is int and row[0] == row[1] == draft.size_bytes)
        text = self._conn.execute(
            "SELECT text FROM skill_mutation_contents WHERE sha256=?", (draft.sha256,)
        ).fetchone()[0]
        creation = validate_skill_creation(text)
        require(
            (creation.name, creation.sha256, creation.size_bytes)
            == (draft.name, draft.sha256, draft.size_bytes)
        )
        return creation

    def _base(self, operation_id: str) -> SkillMutationRecord | None:
        length = self._conn.execute(
            "SELECT length(CAST(identity_json AS BLOB)) FROM skill_mutation_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if length is None:
            require(
                self._conn.execute(
                    "SELECT 1 FROM skill_mutation_events WHERE operation_id=?", (operation_id,)
                ).fetchone()
                is None
            )
            return None
        require(type(length[0]) is int and 0 < length[0] <= 1024)
        row = self._conn.execute(
            "SELECT sequence,operation_id,home_id,action,name,actor,created_at,identity_json,sha256,size_bytes,reverts "
            "FROM skill_mutation_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        require(type(row[7]) is str)
        identity = SkillDirectoryIdentity(**json.loads(row[7]))
        require(identity_json(identity) == row[7])
        draft = SkillMutationDraft(
            row[1],
            row[2],
            SkillMutationAction(row[3]),
            row[4],
            Actor(row[5]),
            row[6],
            identity,
            row[8],
            row[9],
            row[10],
        )
        require(draft.home_id == self.home_id)
        self._blob(draft)
        events = self._conn.execute(
            "SELECT sequence,state FROM skill_mutation_events WHERE operation_id=? ORDER BY sequence LIMIT 4",
            (operation_id,),
        ).fetchall()
        require(1 <= len(events) <= 3)
        state = None
        for event in events:
            require(type(event[0]) is int and event[0] > 0)
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
        return SkillMutationRecord(draft, row[0], state)

    def get(self, operation_id: str) -> SkillMutationRecord | None:
        validate_id(operation_id)
        with corrupt_errors():
            record = self._base(operation_id)
            if record is not None and record.action is SkillMutationAction.ROLLBACK:
                parent = self._base(record.reverts)
                require(
                    parent is not None
                    and parent.action is SkillMutationAction.CREATE
                    and parent.state is SkillMutationState.COMMITTED
                    and parent.sequence < record.sequence
                )
                require(
                    (record.name, record.draft.identity, record.sha256, record.draft.size_bytes)
                    == (parent.name, parent.draft.identity, parent.sha256, parent.draft.size_bytes)
                )
            return record

    def content(self, operation_id: str):
        record = self.get(operation_id)
        if record is None:
            raise SkillMutationError("input", "Operação não encontrada neste histórico.")
        with corrupt_errors():
            return self._blob(record.draft)

    def _records(self, name: str):
        rows = self._conn.execute(
            "SELECT operation_id FROM skill_mutation_operations WHERE name=? ORDER BY sequence",
            (name,),
        ).fetchall()
        return tuple(self.get(row[0]) for row in rows)

    def pending(self, name: str):
        validate_name(name)
        with corrupt_errors():
            return tuple(
                record
                for record in self._records(name)
                if record.state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT)
            )

    def latest_rollback(self, create_id: str):
        parent = self.get(create_id)
        require(
            parent is not None and parent.action is SkillMutationAction.CREATE,
            "Informe o ID de uma criação.",
        )
        with corrupt_errors():
            row = self._conn.execute(
                "SELECT operation_id FROM skill_mutation_operations WHERE reverts=? ORDER BY sequence DESC LIMIT 1",
                (create_id,),
            ).fetchone()
            return None if row is None else self.get(row[0])

    def history(self, *, name: str | None = None, limit: int = 20):
        require(type(limit) is int and 1 <= limit <= 100, "Limite deve ser um inteiro de 1 a 100.")
        if name is not None:
            validate_name(name)
        with corrupt_errors():
            rows = self._conn.execute(
                "SELECT operation_id FROM skill_mutation_operations WHERE (? IS NULL OR name=?) ORDER BY sequence DESC LIMIT ?",
                (name, name, limit),
            ).fetchall()
            return tuple(self.get(row[0]) for row in rows)

    def provenance(self, name: str) -> Provenance | None:
        return None if self._current_create(name) is None else Provenance.USER

    def _current_create(self, name: str) -> str | None:
        validate_name(name)
        with corrupt_errors():
            current = None
            for record in self._records(name):
                if record.state is not SkillMutationState.COMMITTED:
                    continue
                if record.action is SkillMutationAction.CREATE:
                    require(current is None)
                    current = record.operation_id
                else:
                    require(current == record.reverts)
                    current = None
            return current

    def _write(self, operation):
        def transaction():
            if self._conn.in_transaction:
                raise SkillMutationError("io", "Mutação exige uma conexão sem transação pendente.")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                result = operation()
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            return result

        try:
            return write_with_retry(transaction, budget=Budget.TRANSCRIPT, detail="skill_mutation")
        except sqlite3.IntegrityError:
            raise SkillMutationError(
                "conflict", "O ledger recusou uma operação conflitante; arquivos preservados."
            ) from None
        except sqlite3.DatabaseError:
            raise SkillMutationError(
                "io", "Não foi possível confirmar a operação no ledger; consulte seu ID."
            ) from None

    def prepare(self, draft: SkillMutationDraft, text: str):
        require(type(draft) is SkillMutationDraft)
        creation = validate_skill_creation(text)
        require(
            draft.home_id == self.home_id
            and (creation.name, creation.sha256, creation.size_bytes)
            == (draft.name, draft.sha256, draft.size_bytes)
        )

        def operation():
            if self.pending(draft.name):
                raise SkillMutationError(
                    "conflict", "Existe operação pendente para este nome; resolva seu ID."
                )
            current = self._current_create(draft.name)
            if draft.action is SkillMutationAction.CREATE:
                require(current is None, "Já existe uma criação ativa para este nome.")
            else:
                parent = self.get(draft.reverts)
                require(
                    parent is not None
                    and parent.action is SkillMutationAction.CREATE
                    and parent.state is SkillMutationState.COMMITTED
                    and current == parent.operation_id
                )
                require(
                    (parent.name, parent.draft.identity, parent.sha256, parent.draft.size_bytes)
                    == (draft.name, draft.identity, draft.sha256, draft.size_bytes)
                )
            self._conn.execute(
                "INSERT INTO skill_mutation_contents(sha256,text,size_bytes) VALUES (?,?,?) ON CONFLICT(sha256) DO NOTHING",
                (draft.sha256, text, draft.size_bytes),
            )
            with corrupt_errors():
                self._blob(draft)
            self._conn.execute(
                "INSERT INTO skill_mutation_operations(operation_id,home_id,action,name,actor,created_at,identity_json,sha256,size_bytes,reverts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    draft.operation_id,
                    draft.home_id,
                    draft.action.value,
                    draft.name,
                    draft.actor.value,
                    draft.created_at,
                    identity_json(draft.identity),
                    draft.sha256,
                    draft.size_bytes,
                    draft.reverts,
                ),
            )
            self._conn.execute(
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?,?)",
                (draft.operation_id, "prepared"),
            )
            return self.get(draft.operation_id)

        return self._write(operation)

    def finish(self, operation_id: str, state: SkillMutationState):
        validate_id(operation_id)
        require(
            type(state) is SkillMutationState and state is not SkillMutationState.PREPARED,
            "Estado terminal inválido.",
        )

        def operation():
            record = self.get(operation_id)
            require(record is not None, "Operação não encontrada no histórico.")
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
                "INSERT INTO skill_mutation_events(operation_id,state) VALUES (?,?)",
                (operation_id, state.value),
            )
            return self.get(operation_id)

        return self._write(operation)
