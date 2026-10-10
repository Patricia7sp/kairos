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
from kairos_skills.removal_contract import (
    CommonRecord,
    InstallationProof,
    SkillTreeAction,
    SkillTreeDraft,
    SkillTreeRecord,
)
from kairos_state.contention import Budget
from kairos_state.skill_writer import register_skill_writer
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
        register_skill_writer(connection)

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

    def _legacy_get(self, operation_id: str) -> SkillMutationRecord | None:
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

    def _tree_repository(self):
        from kairos_state.repositories.skill_removals import SkillRemovalRepository

        return SkillRemovalRepository(self._conn, home_id=self.home_id)

    def _timeline(self):
        """Atesta famílias e referências antes de usar a sequência comum."""
        with corrupt_errors():
            names = {
                row[0]
                for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            modern = {
                "skill_tree_operations",
                "skill_tree_events",
                "skill_tree_snapshots",
                "skill_tree_blobs",
                "skill_mutation_timeline",
            }
            version = None
            if "schema_version" in names:
                versions = self._conn.execute("SELECT version FROM schema_version").fetchall()
                require(len(versions) == 1 and type(versions[0][0]) is int and versions[0][0] <= 8)
                version = versions[0][0]
            if not names & modern:
                require(version is None or version < 8)
                return None
            require(modern <= names and version == 8)
            require(
                {"skill_mutation_operations", "skill_mutation_events", "skill_mutation_contents"}
                <= names
            )
            rows = self._conn.execute(
                "SELECT t.sequence,t.legacy_event,t.tree_event,COALESCE(l.operation_id,r.operation_id),COALESCE(l.state,r.state) "
                "FROM skill_mutation_timeline t LEFT JOIN skill_mutation_events l ON l.sequence=t.legacy_event "
                "LEFT JOIN skill_tree_events r ON r.sequence=t.tree_event ORDER BY t.sequence"
            ).fetchall()
            seen = [set(), set()]
            last_event = [0, 0]
            operations = [set(), set()]
            previous = 0
            for sequence, legacy, tree, operation_id, state in rows:
                require(
                    type(sequence) is int
                    and sequence > previous
                    and (legacy is None) != (tree is None)
                )
                family = 0 if legacy is not None else 1
                event = legacy if family == 0 else tree
                require(type(event) is int and event > 0 and event not in seen[family])
                require(event > last_event[family])
                last_event[family] = event
                validate_id(operation_id)
                SkillMutationState(state)
                seen[family].add(event)
                operations[family].add(operation_id)
                previous = sequence
            for family, query in enumerate(
                (
                    "SELECT sequence FROM skill_mutation_events",
                    "SELECT sequence FROM skill_tree_events",
                )
            ):
                require(seen[family] == {row[0] for row in self._conn.execute(query)})
            for family, query in enumerate(
                (
                    "SELECT operation_id FROM skill_mutation_operations",
                    "SELECT operation_id FROM skill_tree_operations",
                )
            ):
                require(operations[family] == {row[0] for row in self._conn.execute(query)})
            require(
                self._conn.execute(
                    "SELECT 1 FROM skill_mutation_operations l JOIN skill_tree_operations r USING(operation_id) LIMIT 1"
                ).fetchone()
                is None
            )
            return rows

    def get(self, operation_id: str) -> CommonRecord | None:
        validate_id(operation_id)
        modern = self._timeline() is not None
        return self._get_validated(operation_id, modern=modern)

    def _get_validated(self, operation_id, *, modern):
        record = self._legacy_get(operation_id)
        return (
            record
            if record is not None or not modern
            else self._tree_repository().get(operation_id)
        )

    def content(self, operation_id: str):
        record = self.get(operation_id)
        if record is None or not isinstance(record, SkillMutationRecord):
            raise SkillMutationError("input", "Operação não encontrada neste histórico.")
        with corrupt_errors():
            return self._blob(record.draft)

    def _records(self, name: str):
        timeline = self._timeline()
        if timeline is not None:
            ids = dict.fromkeys(row[3] for row in timeline)
            records = tuple(self._get_validated(operation_id, modern=True) for operation_id in ids)
            require(all(record is not None for record in records))
            return tuple(record for record in records if record.name == name)
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
            timeline = self._timeline()
            if timeline is not None:
                ids = dict.fromkeys(row[3] for row in timeline)
                records = tuple(
                    self._get_validated(operation_id, modern=True) for operation_id in reversed(ids)
                )
                require(all(record is not None for record in records))
                return tuple(record for record in records if name is None or record.name == name)[
                    :limit
                ]
            rows = self._conn.execute(
                "SELECT operation_id FROM skill_mutation_operations WHERE (? IS NULL OR name=?) ORDER BY sequence DESC LIMIT ?",
                (name, name, limit),
            ).fetchall()
            return tuple(self.get(row[0]) for row in rows)

    def provenance(self, name: str) -> Provenance | None:
        current = self.current_installation(name)
        return None if current is None else current.provenance

    def _current_create(self, name: str) -> str | None:
        current = self.current_installation(name)
        return None if current is None else current.operation_id

    def _projection(self, name: str):
        validate_name(name)
        with corrupt_errors():
            current = None
            removed = None
            bundled_removed = False
            records = self._records(name)
            timeline = self._timeline()
            if timeline is not None:
                by_id = {record.operation_id: record for record in records}
                records = tuple(
                    by_id[row[3]] for row in timeline if row[4] == "committed" and row[3] in by_id
                )
            else:
                records = tuple(
                    record for record in records if record.state is SkillMutationState.COMMITTED
                )
            for record in records:
                if record.action is SkillMutationAction.CREATE:
                    require(current is None)
                    identity = record.draft.identity
                    current = InstallationProof(
                        record.operation_id,
                        Provenance.USER,
                        identity.directory_dev,
                        identity.directory_ino,
                    )
                elif record.action is SkillMutationAction.ROLLBACK:
                    require(current is not None and current.operation_id == record.reverts)
                    current = None
                elif record.action is SkillTreeAction.REMOVE:
                    root = record.draft.proof.entries[0]
                    if current is None:
                        require(record.provenance is Provenance.BUNDLED)
                    else:
                        require(
                            (current.provenance, current.device, current.inode)
                            == (record.provenance, root.device, root.inode)
                        )
                    current = None
                    removed = record
                    if record.provenance is Provenance.BUNDLED:
                        bundled_removed = True
                elif record.action is SkillTreeAction.RESTORE:
                    require(
                        current is None
                        and removed is not None
                        and removed.operation_id == record.reverts
                    )
                    root = record.draft.proof.entries[0]
                    current = InstallationProof(
                        record.operation_id, record.provenance, root.device, root.inode
                    )
                    removed = None
                    bundled_removed = False
                else:
                    require(False)
            return current, removed, bundled_removed

    def current_installation(self, name: str) -> InstallationProof | None:
        return self._projection(name)[0]

    def current_removal(self, name: str) -> SkillTreeRecord | None:
        return self._projection(name)[1]

    def has_later_installation(self, remove_id: str) -> bool:
        parent = self.get(remove_id)
        require(
            parent is not None
            and parent.action is SkillTreeAction.REMOVE
            and parent.state is SkillMutationState.COMMITTED,
            "Informe o ID de uma remoção concluída.",
        )
        with corrupt_errors():
            timeline = self._timeline()
            require(timeline is not None)
            records = {record.operation_id: record for record in self._records(parent.name)}
            committed = [row for row in timeline if row[4] == "committed"]
            removed_sequence = next(row[0] for row in committed if row[3] == remove_id)
            return any(
                row[0] > removed_sequence
                and row[3] in records
                and records[row[3]].action in (SkillMutationAction.CREATE, SkillTreeAction.RESTORE)
                for row in committed
            )

    def snapshot(self, operation_id: str):
        self._timeline()
        return self._tree_repository().snapshot(operation_id)

    def latest_restore(self, remove_id: str):
        parent = self.get(remove_id)
        require(
            parent is not None and parent.action is SkillTreeAction.REMOVE,
            "Informe o ID de uma remoção.",
        )
        with corrupt_errors():
            records = self._records(parent.name)
            return next(
                (
                    record
                    for record in reversed(records)
                    if record.action is SkillTreeAction.RESTORE and record.reverts == remove_id
                ),
                None,
            )

    def bundled_tombstones(self) -> frozenset[str]:
        with corrupt_errors():
            names = {
                row[0]
                for row in self._conn.execute("SELECT DISTINCT name FROM skill_mutation_operations")
            }
            if self._timeline() is not None:
                names.update(
                    row[0]
                    for row in self._conn.execute("SELECT DISTINCT name FROM skill_tree_operations")
                )
            return frozenset(name for name in names if self._projection(name)[2])

    def _assert_no_pending(self, name: str):
        pending = self.pending(name)
        if pending:
            raise SkillMutationError(
                "conflict",
                "Existe operação pendente para este nome; resolva seu ID.",
                operation_id=pending[0].operation_id,
            )

    def _prepare_tree(self, draft, prepare):
        require(type(draft) is SkillTreeDraft)
        tree = self._tree_repository()

        def guarded_write(operation):
            def guarded():
                self._assert_no_pending(draft.name)
                current, removed, _ = self._projection(draft.name)
                if draft.action is SkillTreeAction.REMOVE:
                    root = draft.proof.entries[0]
                    require(
                        (current is None and draft.provenance is Provenance.BUNDLED)
                        or (
                            current is not None
                            and (current.provenance, current.device, current.inode)
                            == (draft.provenance, root.device, root.inode)
                        ),
                        "A instalação não tem a prova corrente exigida.",
                    )
                elif draft.action is SkillTreeAction.RESTORE:
                    if (
                        current is not None
                        or removed is None
                        or removed.operation_id != draft.reverts
                    ):
                        raise SkillMutationError(
                            "conflict",
                            "A remoção não é corrente ou o nome já tem instalação; versões preservadas.",
                            operation_id=current.operation_id
                            if current is not None
                            else (removed.operation_id if removed is not None else draft.reverts),
                        )
                else:
                    require(False)
                return operation()

            return self._write(guarded)

        tree._write = guarded_write
        return prepare(tree)

    def prepare_remove(self, draft, snapshot):
        return self._prepare_tree(draft, lambda tree: tree.prepare_remove(draft, snapshot))

    def prepare_restore(self, draft):
        return self._prepare_tree(draft, lambda tree: tree.prepare_restore(draft))

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
            self._assert_no_pending(draft.name)
            current = self._current_create(draft.name)
            if draft.action is SkillMutationAction.CREATE:
                require(current is None, "Já existe uma criação ativa para este nome.")
            elif draft.action is SkillMutationAction.ROLLBACK:
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
            else:
                require(False)
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
        if isinstance(self.get(operation_id), SkillTreeRecord):
            return self._tree_repository().finish(operation_id, state)

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
